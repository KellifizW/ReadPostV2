import asyncio
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html
import streamlit as st
import streamlit.logger
import time
import random
from config import LIHKG_API, HKGOLDEN_API

logger = streamlit.logger.get_logger(__name__)

def clean_expired_cache(platform):
    """清理過期緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]

async def process_user_question(question, platform, cat_id_map, selected_cat):
    """處理用戶問題並返回相關帖子數據"""
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
    
    selected_item = random.choice(filtered_items)
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
    replies = selected_item.get("replies", []) or []
    
    logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, replies={len(replies)}")
    
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
    
    response = f"以下是從 {platform} 的帖子（標題：{thread_title}，ID：{thread_id}）中提取的內容：\n\n"
    for i, data in enumerate(processed_data[:3], 1):
        response += f"回覆 {i}：{data['content']}\n讚好：{data['like_count']}，點踩：{data['dislike_count']}\n\n"
    
    return {
        "response": response,
        "rate_limit_info": rate_limit_info,
        "processed_data": processed_data
    }
