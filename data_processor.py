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
from grok3_client import stream_grok3_response

logger = streamlit.logger.get_logger(__name__)

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
    # 加權：回覆數(40%) + 評分(40%) - 時間差(20%)
    score = (0.4 * no_of_reply / 100) + (0.4 * rt / 10) - (0.2 * (current_time - lrt) / 3600)
    return score

async def process_user_question(question, platform, cat_id_map, selected_cat):
    """處理用戶問題並返回相關帖子數據"""
    # 檢查重複調用
    if "last_processed_time" in st.session_state and time.time() - st.session_state["last_processed_time"] < 1:
        logger.warning("Skipping duplicate process_user_question call")
        return st.session_state.get("last_result", {
            "response": "重複請求，請稍後重試。",
            "rate_limit_info": [],
            "processed_data": []
        })
    
    logger.info(f"Starting to process question: question={question}, platform={platform}, category={selected_cat}")
    
    clean_expired_cache(platform)
    
    cat_id = cat_id_map[selected_cat]
    
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
    
    min_replies = LIHKG_API["MIN_REPLIES"] if platform == "LIHKG" else HKGOLDEN_API["MIN_REPLIES"]
    filtered_items = [item for item in items if item["no_of_reply"] >= min_replies]
    
    if not filtered_items:
        logger.warning(f"No posts found with no_of_reply >= {min_replies}")
        return {
            "response": "無符合條件的帖子，請嘗試其他分類或稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    # 確保至少 3 條非空回覆
    valid_items = [
        item for item in filtered_items
        if len([r for r in item["replies"] if r["msg"].strip()]) >= 3
    ]
    
    if not valid_items:
        logger.warning("No posts found with at least 3 non-empty replies")
        return {
            "response": "無帖子包含足夠的有效回覆，請稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    # 加權選擇最佳帖子
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
            "content": clean_html(reply["msg"]),
            "like_count": reply.get("like_count", 0),
            "dislike_count": reply.get("dislike_count", 0)
        }
        for reply in replies
    ]
    
    if len(processed_data) < 3:
        logger.warning(f"Insufficient valid replies for thread_id={thread_id}, found={len(processed_data)}")
        return {
            "response": "帖子回覆不足或無有效內容，請稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": []
        }
    
    # 硬編碼 Grok 3 prompt
    prompt = f"""
You are Grok, embodying the collective voice of 高登討論區. Your role is to share a post that is highly engaging, insightful, or representative of the platform's discussions. Below is a selected post from the 聊天 category, chosen for its high reply count ({selected_item["no_of_reply"]}), strong rating ({selected_item["rt"]}), and recent activity (last reply: {datetime.fromtimestamp(selected_item["lrt"]).strftime('%Y-%m-%d %H:%M:%S')}).

Post data:
- ID: {thread_id}
- Title: {thread_title}
- Number of replies: {selected_item["no_of_reply"]}
- Rating: {selected_item["rt"]}
- Last reply time: {datetime.fromtimestamp(selected_item["lrt"]).strftime('%Y-%m-%d %H:%M:%S')}
- Replies: {', '.join([r["msg"][:100] + '...' if len(r["msg"]) > 100 else r["msg"] for r in replies[:3]])}

Generate a concise sharing text (max 280 characters) that highlights why this post is worth sharing, includes the post's title, reply count, rating, and a snippet of the first meaningful reply. Explain why you chose it (e.g., high engagement, insightful replies).

Example:
This 高登討論區 post is buzzing with {selected_item["no_of_reply"]} replies and a {selected_item["rt"]} rating! "{thread_title[:50]}" sparks debate. First reply: "{replies[0]["msg"][:50]}". Chosen for its lively discussion!

{'{{ output }}' if replies else 'No engaging posts found with meaningful replies.'}
"""
    
    logger.info(f"Generated Grok 3 prompt: {prompt}")
    
    # 調用 Grok 3 API
    response_text = ""
    async for chunk in stream_grok3_response(prompt):
        response_text += chunk
    
    if "Error" in response_text:
        logger.warning(f"Falling back to local response due to Grok 3 API failure: {response_text}")
        response = f"以下是從 {platform} 的帖子（標題：{thread_title}，ID：{thread_id}）中提取的內容：\n\n"
        for i, data in enumerate(processed_data[:3], 1):
            response += f"回覆 {i}：{data['content']}\n讚好：{data['like_count']}，點踩：{data['dislike_count']}\n\n"
    else:
        response = response_text.strip()
    
    # 記錄結果和時間
    result = {
        "response": response,
        "rate_limit_info": rate_limit_info,
        "processed_data": processed_data
    }
    st.session_state["last_processed_time"] = time.time()
    st.session_state["last_result"] = result
    
    logger.info(f"Processed question: question={question}, platform={platform}, response_length={len(response)}")
    
    return result
