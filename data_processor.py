import asyncio
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html
import streamlit as st
import streamlit.logger
from config import LIHKG_API, HKGOLDEN_API

logger = streamlit.logger.get_logger(__name__)

def clean_expired_cache(platform):
    """清理過期緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]

async def process_user_question(question, platform, cat_id_map, selected_cat, request_counter, last_reset, rate_limit_until):
    # 清理過期緩存
    clean_expired_cache(platform)
    
    # 預設使用 UI 選擇的分類
    cat_id = cat_id_map[selected_cat]
    max_pages = LIHKG_API["MAX_PAGES"] if platform == "LIHKG" else HKGOLDEN_API["MAX_PAGES"]
    min_replies = LIHKG_API["MIN_REPLIES"] if platform == "LIHKG" else HKGOLDEN_API["MIN_REPLIES"]
    
    # 如果問題中包含其他分類名稱，則覆蓋 UI 選擇的分類
    for cat_name, cat_id_val in cat_id_map.items():
        if cat_name in question:
            selected_cat = cat_name
            cat_id = cat_id_val
            break
    
    # 抓取帖子列表
    if platform == "LIHKG":
        result = await get_lihkg_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=max_pages,
            request_counter=request_counter,
            last_reset=last_reset,
            rate_limit_until=rate_limit_until
        )
    else:
        result = await get_hkgolden_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=max_pages,
            request_counter=request_counter,
            last_reset=last_reset,
            rate_limit_until=rate_limit_until
        )
    
    # 更新速率限制狀態
    request_counter = result.get("request_counter", request_counter)
    last_reset = result.get("last_reset", last_reset)
    rate_limit_until = result.get("rate_limit_until", rate_limit_until)
    
    # 篩選回覆數 ≥ min_replies 的帖子
    items = result.get("items", [])
    filtered_items = [item for item in items if item.get("no_of_reply", 0) >= min_replies]
    logger.info(f"篩選帖子: platform={platform}, cat_id={cat_id}, 總數={len(items)}, 符合條件={len(filtered_items)}")
    
    thread_ids = [item["thread_id"] for item in filtered_items]
    max_replies_per_thread = LIHKG_API["MAX_REPLIES_PER_THREAD"] if platform == "LIHKG" else HKGOLDEN_API["MAX_REPLIES_PER_THREAD"]
    batch_size = LIHKG_API["BATCH_SIZE"] if platform == "LIHKG" else HKGOLDEN_API["BATCH_SIZE"]
    thread_data = []
    
    # 分批抓取帖子內容
    for i in range(0, len(thread_ids), batch_size):
        batch_thread_ids = thread_ids[i:i + batch_size]
        for thread_id in batch_thread_ids:
            item = next((x for x in filtered_items if x["thread_id"] == thread_id), None)
            if not item:
                continue
            
            logger.info(f"開始抓取帖子內容: platform={platform}, thread_id={thread_id}, cat_id={cat_id}")
            if platform == "LIHKG":
                thread_result = await get_lihkg_thread_content(
                    thread_id=thread_id,
                    cat_id=cat_id,
                    request_counter=request_counter,
                    last_reset=last_reset,
                    rate_limit_until=rate_limit_until
                )
            else:
                thread_result = await get_hkgolden_thread_content(
                    thread_id=thread_id,
                    cat_id=cat_id,
                    request_counter=request_counter,
                    last_reset=last_reset,
                    rate_limit_until=rate_limit_until
                )
            
            replies = thread_result.get("replies", [])
            sorted_replies = sorted(
                replies,
                key=lambda x: x.get("like_count", 0),
                reverse=True
            )[:max_replies_per_thread]
            
            thread_data.append({
                "thread_id": thread_id,
                "title": item["title"],
                "no_of_reply": item["no_of_reply"],
                "last_reply_time": item.get("last_reply_time", 0),
                "like_count": item.get("like_count", 0),
                "dislike_count": item.get("dislike_count", 0),
                "replies": [
                    {
                        "msg": clean_html(reply["msg"]),
                        "like_count": reply.get("like_count", 0),
                        "dislike_count": reply.get("dislike_count", 0)
                    }
                    for reply in sorted_replies
                ]
            })
            
            # 更新速率限制狀態
            request_counter = thread_result.get("request_counter", request_counter)
            last_reset = thread_result.get("last_reset", last_reset)
            rate_limit_until = thread_result.get("rate_limit_until", rate_limit_until)
            
            await asyncio.sleep(LIHKG_API["REQUEST_DELAY"] if platform == "LIHKG" else HKGOLDEN_API["REQUEST_DELAY"])
        
        if i + batch_size < len(thread_ids):
            await asyncio.sleep(LIHKG_API["REQUEST_DELAY"] if platform == "LIHKG" else HKGOLDEN_API["REQUEST_DELAY"])
    
    result.update({
        "selected_cat": selected_cat,
        "max_pages": max_pages,
        "thread_data": thread_data,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    })
    return result