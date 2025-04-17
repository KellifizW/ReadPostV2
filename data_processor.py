import asyncio
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html
import streamlit as st
import streamlit.logger
import time
import random
from datetime import datetime
from config import LIHKG_API, HKGOLDEN_API
import re
from threading import Lock
from grok3_client import call_grok3_api

logger = streamlit.logger.get_logger(__name__)
processing_lock = Lock()

def clean_expired_cache(platform):
    """清理過期緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]

def score_item(item, current_time):
    """計算帖子分數：高回覆數、高評分、最近回覆"""
    no_of_reply = item["no_of_reply"]
    rt = item["rt"]
    lrt = item["lrt"]
    score = (0.4 * no_of_reply / 100) + (0.4 * rt / 10) - (0.2 * (current_time - lrt) / 3600)
    return score

def clean_reply_text(text):
    """清理回覆中的 HTML 標籤和表情符號，保留純文字"""
    text = re.sub(r'\[sosad\]', '(sad)', text)  # 替換 [sosad] 表情符號
    text = re.sub(r'<img[^>]+alt="\[([^\]]+)\]"[^>]*>', r'(\1)', text)
    text = clean_html(text)
    text = ' '.join(text.split())
    return text

async def process_user_question(question, platform, cat_id_map, selected_cat, min_replies):
    """處理用戶問題並返回相關帖子數據"""
    request_key = f"{question}:{platform}:{selected_cat}:{min_replies}"
    current_time = time.time()
    
    with processing_lock:
        if ("last_request_key" in st.session_state and 
            st.session_state["last_request_key"] == request_key and 
            current_time - st.session_state.get("last_processed_time", 0) < 5):
            logger.warning("Skipping duplicate process_user_question call")
            return st.session_state.get("last_result", {
                "response": "重複請求，請稍後再試。",
                "rate_limit_info": [],
                "processed_data": []
            })
    
    logger.info(f"Processing question: question={question}, platform={platform}, category={selected_cat}, min_replies={min_replies}")
    
    clean_expired_cache(platform)
    
    cat_id = cat_id_map[selected_cat]
    
    start_fetch_time = time.time()
    if platform == "LIHKG":
        result = await get_lihkg_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=LIHKG_API["MAX_PAGES"],
            request_counter=st.session_state.get("request_counter", 0),
            last_reset=st.session_state.get("last_reset", time.time()),
            rate_limit_until=st.session_state.get("rate_limit_until", 0)
        )
    else:
        result = await get_hkgolden_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=HKGOLDEN_API["MAX_PAGES"],
            request_counter=st.session_state.get("request_counter", 0),
            last_reset=st.session_state.get("last_reset", time.time()),
            rate_limit_until=st.session_state.get("rate_limit_until", 0)
        )
    fetch_elapsed = time.time() - start_fetch_time
    logger.info(f"Fetch completed: platform={platform}, category={selected_cat}, elapsed={fetch_elapsed:.2f}s")
    
    items = result["items"]
    rate_limit_info = result["rate_limit_info"]
    st.session_state.request_counter = result["request_counter"]
    st.session_state.last_reset = result["last_reset"]
    st.session_state.rate_limit_until = result["rate_limit_until"]
    
    filtered_items = [item for item in items if item["no_of_reply"] >= min_replies]
    
    if not filtered_items:
        logger.warning(f"No posts found with no_of_reply >= {min_replies}")
        # 備用邏輯：選擇回覆數最多的帖子
        if items:
            filtered_items = [max(items, key=lambda x: x["no_of_reply"])]
            logger.info(f"Fallback: Selected post with {filtered_items[0]['no_of_reply']} replies (below min_replies={min_replies})")
        else:
            return {
                "response": f"無符合條件的帖子（最少回覆數：{min_replies}）。請嘗試降低回覆數量要求或更改分類。",
                "rate_limit_info": rate_limit_info,
                "processed_data": []
            }
    
    valid_items = [
        item for item in filtered_items
        if len([r for r in item["replies"] if r["msg"].strip()]) >= 3
    ]
    
    if not valid_items:
        logger.warning("No posts found with at least 3 non-empty replies")
        # 備用邏輯：放寬有效回覆要求
        valid_items = filtered_items
        logger.info("Fallback: Using posts without strict reply validation")
        if not valid_items:
            return {
                "response": f"無帖子包含足夠的有效回覆（最少回覆數：{min_replies}）。請嘗試降低回覆數量要求或稍後重試。",
                "rate_limit_info": rate_limit_info,
                "processed_data": []
            }
    
    current_time = time.time()
    scored_items = [(item, score_item(item, current_time)) for item in valid_items]
    selected_item = max(scored_items, key=lambda x: x[1])[0]
    
    try:
        thread_id = selected_item["id"] if platform in ["HKGOLDEN", "高登討論區"] else selected_item["thread_id"]
    except KeyError as e:
        logger.error(f"Missing thread_id or id in selected_item: {selected_item}, error={str(e)}")
        return {
            "response": "無法提取帖子 ID，請稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    thread_title = selected_item["title"]
    replies = [r for r in selected_item.get("replies", []) if r["msg"].strip()]
    
    logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, replies={len(replies)}, score={score_item(selected_item, current_time):.2f}")
    
    processed_data = [
        {
            "thread_id": thread_id,
            "title": thread_title,
            "content": clean_reply_text(reply["msg"]),
            "like_count": reply.get("like_count", 0),
            "dislike_count": reply.get("dislike_count", 0)
        }
        for reply in replies
    ]
    
    if len(processed_data) < 3:
        logger.warning(f"Insufficient valid replies for thread_id={thread_id}, found={len(processed_data)}")
        # 放寬要求，允許少於 3 條回覆
        if not processed_data:
            return {
                "response": f"帖子回覆不足或無有效內容（最少回覆數：{min_replies}）。請嘗試降低回覆數量要求或稍後重試。",
                "rate_limit_info": rate_limit_info,
                "processed_data": []
            }
    
    cleaned_replies = [clean_reply_text(r["msg"])[:50] + '...' if len(clean_reply_text(r["msg"])) > 50 else clean_reply_text(r["msg"]) for r in replies[:2]]
    prompt = f"""
You are Grok, sharing a notable post from 高登討論區. Below is a selected post from the {selected_cat} category, chosen for high engagement ({selected_item["no_of_reply"]} replies, rating {selected_item["rt"]}) and recent activity (last reply: {datetime.fromtimestamp(selected_item["lrt"]).strftime('%Y-%m-%d %H:%M:%S')}).

Post data:
- ID: {thread_id}
- Title: {thread_title}
- Replies: {', '.join(cleaned_replies)}

Generate a concise sharing text (max 280 characters) highlighting why this post is engaging, including the title, reply count, and a snippet of the first reply.

Example:
Hot 高登 post with {selected_item["no_of_reply"]} replies! "{thread_title[:50]}" sparks debate. First reply: "{cleaned_replies[0][:50]}". Chosen for lively discussion!

{{ output }}
"""
    
    logger.info(f"Generated Grok 3 prompt (length={len(prompt)} chars)")
    
    start_api_time = time.time()
    api_result = await call_grok3_api(prompt)
    api_elapsed = time.time() - start_api_time
    logger.info(f"Grok 3 API call completed: elapsed={api_elapsed:.2f}s")
    
    if api_result.get("status") == "error":
        logger.warning(f"Falling back to local response due to Grok 3 API failure: {api_result['content']}")
        first_reply = cleaned_replies[0][:50] if cleaned_replies else "無回覆"
        response = f"""
Hot thread on 高登討論區 with {selected_item["no_of_reply"]} replies! "{thread_title[:50]}" sparks debate. First reply: "{first_reply}". Chosen for high engagement!

Details:
- ID: {thread_id}
- Title: {thread_title}
"""
        for i, data in enumerate(processed_data[:2], 1):
            response += f"\n回覆 {i}：{data['content'][:50]}...\n讚好：{data['like_count']}，點踩：{data['dislike_count']}\n"
    else:
        response = api_result["content"].strip()
    
    result = {
        "response": response,
        "rate_limit_info": rate_limit_info,
        "processed_data": processed_data
    }
    with processing_lock:
        st.session_state["last_request_key"] = request_key
        st.session_state["last_processed_time"] = current_time
        st.session_state["last_result"] = result
    
    logger.info(f"Processed question: question={question}, platform={platform}, response_length={len(response)}")
    
    return result
