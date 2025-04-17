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
from grok3_client import call_grok3_api
import re
from threading import Lock

logger = streamlit.logger.get_logger(__name__)
processing_lock = Lock()

def clean_expired_cache(platform):
    """清理過期緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]

def clean_reply_text(text):
    """清理回覆中的 HTML 標籤，保留純文字"""
    text = re.sub(r'<img[^>]+alt="\[([^\]]+)\]"[^>]*>', r'[\1]', text)
    text = clean_html(text)
    text = ' '.join(text.split())
    return text

async def process_user_question(question, platform, cat_id_map, selected_cat):
    """處理用戶問題並返回相關帖子數據"""
    request_key = f"{question}:{platform}:{selected_cat}"
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
    
    logger.info(f"Starting to process question: question={question}, platform={platform}, category={selected_cat}")
    
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
    
    items = result["items"]
    rate_limit_info = result["rate_limit_info"]
    st.session_state.request_counter = result["request_counter"]
    st.session_state.last_reset = result["last_reset"]
    st.session_state.rate_limit_until = result["rate_limit_until"]
    
    logger.info(f"Fetch completed: platform={platform}, category={selected_cat}, total_items={len(items)}, elapsed={time.time() - start_fetch_time:.2f}s")
    
    if not items:
        logger.error("No items fetched from API")
        return {
            "response": f"無法抓取帖子，API 返回空數據。請檢查分類（{selected_cat}）或稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    # 過濾掉無回覆的帖子，選擇有回覆的帖子
    items_with_replies = [item for item in items if item.get("no_of_reply", 0) > 0]
    if not items_with_replies:
        logger.error("No items with replies found")
        return {
            "response": f"無符合條件的帖子（所有帖子均無回覆）。請檢查分類（{selected_cat}）或稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    # 隨機選擇一個有回覆的帖子
    selected_item = random.choice(items_with_replies)
    
    try:
        thread_id = selected_item["thread_id"] if platform == "LIHKG" else selected_item["id"]
    except KeyError as e:
        logger.error(f"Missing thread_id or id in selected_item: {selected_item}, error={str(e)}")
        return {
            "response": "無法提取帖子 ID，請稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    thread_title = selected_item["title"]
    
    # 抓取帖子回覆內容
    logger.info(f"Fetching thread content: thread_id={thread_id}, platform={platform}")
    if platform == "LIHKG":
        thread_result = await get_lihkg_thread_content(
            thread_id=thread_id,
            cat_id=cat_id,
            request_counter=st.session_state.request_counter,
            last_reset=st.session_state.last_reset,
            rate_limit_until=st.session_state.rate_limit_until
        )
    else:
        thread_result = await get_hkgolden_thread_content(
            thread_id=thread_id,
            cat_id=cat_id,
            request_counter=st.session_state.request_counter,
            last_reset=st.session_state.last_reset,
            rate_limit_until=st.session_state.rate_limit_until
        )
    
    replies = thread_result["replies"]
    thread_title = thread_result["title"] or thread_title
    rate_limit_info.extend(thread_result["rate_limit_info"])
    st.session_state.request_counter = thread_result["request_counter"]
    st.session_state.rate_limit_until = thread_result["rate_limit_until"]
    
    logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, replies={len(replies)}")
    
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
    
    if not replies:
        logger.warning(f"No valid replies for thread_id={thread_id}")
        response = f"""
Hot thread on {platform}! "{thread_title[:50]}" sparks discussion.

Details:
- ID: {thread_id}
- Title: {thread_title}
"""
        return {
            "response": response,
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    cleaned_replies = [clean_reply_text(r["msg"])[:100] + '...' if len(clean_reply_text(r["msg"])) > 100 else clean_reply_text(r["msg"]) for r in replies[:3]]
    prompt = f"""
You are Grok, sharing a notable post from {platform} ({selected_cat} category). Below is a selected post chosen for its relevance.

Post data:
- ID: {thread_id}
- Title: {thread_title}
- Replies: {', '.join(cleaned_replies) if cleaned_replies else 'No replies'}

Generate a concise sharing text (max 280 characters) highlighting why this post is engaging, including the title and a snippet of the first reply if available.

Example:
Hot {platform} post! "{thread_title[:50]}" sparks debate. First reply: "{cleaned_replies[0][:50] if cleaned_replies else 'None'}". Check it out!

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
Hot thread on {platform}! "{thread_title[:50]}" sparks discussion. First reply: "{first_reply}".

Details:
- ID: {thread_id}
- Title: {thread_title}
"""
        for i, data in enumerate(processed_data[:3], 1):
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
