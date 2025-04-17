import aiohttp
import asyncio
import streamlit as st
import streamlit.logger
import time
from datetime import datetime
from config import HKGOLDEN_API
import re
from utils import clean_html

logger = streamlit.logger.get_logger(__name__)

async def fetch_page(session, url, headers, page, cat_id, sub_cat_id):
    """抓取單頁數據"""
    params = {
        "cat_id": cat_id,
        "sub_cat_id": sub_cat_id,
        "page": page,
        "sense": "all",
        "order": "lastpost",
        "filter": "all"
    }
    start_time = time.time()
    try:
        async with session.get(url, headers=headers, params=params, timeout=10) as response:
            elapsed = time.time() - start_time
            if response.status != 200:
                logger.error(f"Failed to fetch page {page}: status={response.status}")
                return []
            data = await response.json()
            logger.info(f"Fetched page {page}: elapsed={elapsed:.2f}s, items={len(data.get('items', []))}")
            return data.get("items", [])
    except Exception as e:
        logger.error(f"Error fetching page {page}: {str(e)}")
        return []

async def fetch_thread_replies(session, thread_id, page, headers):
    """抓取帖子回覆"""
    url = f"{HKGOLDEN_API['BASE_URL']}/topics/{thread_id}/replies"
    params = {"page": page}
    start_time = time.time()
    try:
        async with session.get(url, headers=headers, params=params, timeout=10) as response:
            elapsed = time.time() - start_time
            if response.status != 200:
                logger.error(f"Failed to fetch replies for thread {thread_id}, page {page}: status={response.status}")
                return []
            data = await response.json()
            logger.debug(f"Thread page {page} fetch completed: elapsed={elapsed:.2f}s")
            return data.get("replies", [])
    except Exception as e:
        logger.error(f"Error fetching replies for thread {thread_id}, page {page}: {str(e)}")
        return []

async def get_hkgolden_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter, last_reset, rate_limit_until):
    """抓取高登討論區帖子列表"""
    current_time = time.time()
    if current_time - last_reset > HKGOLDEN_API["RATE_LIMIT_RESET"]:
        request_counter = 0
        last_reset = current_time
    
    if current_time < rate_limit_until:
        logger.warning(f"Rate limit active until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}")
        return {"items": [], "rate_limit_info": [f"Rate limit active until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}"], "request_counter": request_counter, "last_reset": last_reset, "rate_limit_until": rate_limit_until}
    
    headers = {
        "User-Agent": HKGOLDEN_API["USER_AGENT"],
        "Accept": "application/json"
    }
    
    all_items = []
    rate_limit_info = []
    url = f"{HKGOLDEN_API['BASE_URL']}/topics"
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            if request_counter >= HKGOLDEN_API["RATE_LIMIT"]:
                rate_limit_until = current_time + HKGOLDEN_API["RATE_LIMIT_DURATION"]
                rate_limit_info.append(f"Rate limit reached at page {page}, waiting until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}")
                break
            
            request_counter += 1
            items = await fetch_page(session, url, headers, page, cat_id, sub_cat_id)
            if not items:
                rate_limit_info.append(f"No items fetched for page {page}")
                continue
            
            # 為每個帖子抓取回覆
            for item in items:
                thread_id = item.get("id")
                total_replies = item.get("no_of_reply", 0)
                replies = []
                max_reply_pages = min(1, -(-total_replies // 25))  # 每頁最多 25 條回覆
                for reply_page in range(1, min(max_reply_pages + 1, 2)):
                    if request_counter >= HKGOLDEN_API["RATE_LIMIT"]:
                        rate_limit_until = current_time + HKGOLDEN_API["RATE_LIMIT_DURATION"]
                        rate_limit_info.append(f"Rate limit reached at thread {thread_id}, page {reply_page}")
                        break
                    request_counter += 1
                    thread_replies = await fetch_thread_replies(session, thread_id, reply_page, headers)
                    replies.extend(thread_replies)
                
                item["replies"] = replies[:25]  # 限制最多 25 條回覆
                item["rt"] = item.get("rt", 0)
                item["lrt"] = item.get("lrt", current_time)
                logger.debug(f"Thread {thread_id}: total_replies={total_replies}, fetched_replies={len(replies)}")
            
            all_items.extend(items)
            logger.info(f"Page {page} processed: items={len(items)}, total_items={len(all_items)}")
    
    logger.info(f"Total items fetched: {len(all_items)}")
    return {
        "items": all_items,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_hkgolden_thread_content(thread_id, max_pages=2):
    """抓取高登討論區帖子內容"""
    headers = {
        "User-Agent": HKGOLDEN_API["USER_AGENT"],
        "Accept": "application/json"
    }
    replies = []
    async with aiohttp.ClientSession() as session:
        for page in range(1, max_pages + 1):
            thread_replies = await fetch_thread_replies(session, thread_id, page, headers)
            replies.extend(thread_replies)
    
    processed_replies = [
        {
            "msg": clean_html(reply.get("msg", "")),
            "like_count": reply.get("like_count", 0),
            "dislike_count": reply.get("dislike_count", 0)
        }
        for reply in replies
    ]
    return processed_replies
