import aiohttp
import asyncio
import streamlit as st
import streamlit.logger
import time
from datetime import datetime
from config import LIHKG_API
import re
from utils import clean_html
import hashlib
import uuid
from collections import deque

logger = streamlit.logger.get_logger(__name__)

class RateLimiter:
    def __init__(self, max_requests, reset_interval):
        self.max_requests = max_requests
        self.reset_interval = reset_interval
        self.requests = deque()

    async def acquire(self):
        now = time.time()
        while self.requests and now - self.requests[0] > self.reset_interval:
            self.requests.popleft()
        if len(self.requests) >= self.max_requests:
            wait_time = self.reset_interval - (now - self.requests[0])
            logger.warning(f"Rate limit reached, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
        self.requests.append(now)

def validate_config():
    """驗證 LIHKG_API 配置"""
    required_keys = ["BASE_URL", "USER_AGENT", "CATEGORIES", "MAX_PAGES", "RATE_LIMIT", "RATE_LIMIT_DURATION", "RATE_LIMIT_RESET"]
    missing_keys = [key for key in required_keys if key not in LIHKG_API]
    if missing_keys:
        logger.error(f"Missing LIHKG_API configuration keys: {missing_keys}")
        raise KeyError(f"Missing configuration keys: {missing_keys}")
    
    rate_limit_keys = ["MAX_REQUESTS", "RESET_INTERVAL"]
    missing_rate_limit_keys = [key for key in rate_limit_keys if key not in LIHKG_API["RATE_LIMIT"]]
    if missing_rate_limit_keys:
        logger.error(f"Missing LIHKG_API['RATE_LIMIT'] keys: {missing_rate_limit_keys}")
        raise KeyError(f"Missing RATE_LIMIT keys: {missing_rate_limit_keys}")
    
    logger.debug(f"LIHKG_API configuration: {LIHKG_API}")

# 初始化速率限制器
try:
    validate_config()
    rate_limiter = RateLimiter(
        max_requests=LIHKG_API["RATE_LIMIT"]["MAX_REQUESTS"],
        reset_interval=LIHKG_API["RATE_LIMIT"]["RESET_INTERVAL"]
    )
except KeyError as e:
    logger.error(f"Failed to initialize RateLimiter: {str(e)}")
    raise

async def get_lihkg_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter, last_reset, rate_limit_until):
    """抓取 LIHKG 帖子列表"""
    current_time = time.time()
    if current_time - last_reset > LIHKG_API["RATE_LIMIT_RESET"]:
        request_counter = 0
        last_reset = current_time
    
    if current_time < rate_limit_until:
        logger.warning(f"Rate limit active until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}")
        return {"items": [], "rate_limit_info": [f"Rate limit active until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}"], "request_counter": request_counter, "last_reset": last_reset, "rate_limit_until": rate_limit_until}
    
    headers = {
        "User-Agent": LIHKG_API["USER_AGENT"],
        "Accept": "application/json"
    }
    
    all_items = []
    rate_limit_info = []
    url = f"{LIHKG_API['BASE_URL']}/threads"
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            await rate_limiter.acquire()
            if request_counter >= LIHKG_API["RATE_LIMIT"]["MAX_REQUESTS"]:
                rate_limit_until = current_time + LIHKG_API["RATE_LIMIT_DURATION"]
                rate_limit_info.append(f"Rate limit reached at page {page}, waiting until {datetime.fromtimestamp(rate_limit_until).strftime('%Y-%m-%d %H:%M:%S')}")
                break
            
            request_counter += 1
            params = {
                "cat_id": cat_id,
                "sub_cat_id": sub_cat_id,
                "page": page,
                "order": "reply_time"
            }
            start_time = time.time()
            try:
                async with session.get(url, headers=headers, params=params, timeout=10) as response:
                    elapsed = time.time() - start_time
                    if response.status != 200:
                        logger.error(f"Failed to fetch page {page}: status={response.status}")
                        rate_limit_info.append(f"Failed to fetch page {page}: status={response.status}")
                        continue
                    data = await response.json()
                    items = data.get("items", [])
                    logger.info(f"Fetched page {page}: elapsed={elapsed:.2f}s, items={len(items)}")
                    all_items.extend(items)
            except Exception as e:
                logger.error(f"Error fetching page {page}: {str(e)}")
                rate_limit_info.append(f"Error fetching page {page}: {str(e)}")
                continue
    
    logger.info(f"Total items fetched: {len(all_items)}")
    return {
        "items": all_items,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_lihkg_thread_content(thread_id, max_pages=2):
    """抓取 LIHKG 帖子內容"""
    headers = {
        "User-Agent": LIHKG_API["USER_AGENT"],
        "Accept": "application/json"
    }
    replies = []
    async with aiohttp.ClientSession() as session:
        for page in range(1, max_pages + 1):
            await rate_limiter.acquire()
            url = f"{LIHKG_API['BASE_URL']}/threads/{thread_id}/replies"
            params = {"page": page}
            start_time = time.time()
            try:
                async with session.get(url, headers=headers, params=params, timeout=10) as response:
                    elapsed = time.time() - start_time
                    if response.status != 200:
                        logger.error(f"Failed to fetch replies for thread {thread_id}, page {page}: status={response.status}")
                        continue
                    data = await response.json()
                    replies.extend(data.get("replies", []))
                    logger.debug(f"Thread page {page} fetch completed: elapsed={elapsed:.2f}s")
            except Exception as e:
                logger.error(f"Error fetching replies for thread {thread_id}, page {page}: {str(e)}")
                continue
    
    processed_replies = [
        {
            "msg": clean_html(reply.get("msg", "")),
            "like_count": reply.get("like_count", 0),
            "dislike_count": reply.get("dislike_count", 0)
        }
        for reply in replies
    ]
    return processed_replies
