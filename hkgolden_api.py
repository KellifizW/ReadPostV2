import aiohttp
import asyncio
import time
from datetime import datetime
import random
import uuid
import hashlib
import streamlit as st
import streamlit.logger
from config import HKGOLDEN_API, GENERAL

logger = streamlit.logger.get_logger(__name__)

# 隨機化的 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"
]

# 全局速率限制管理器
class RateLimiter:
    def __init__(self, max_requests: int, period: float):
        self.max_requests = max_requests
        self.period = period
        self.requests = []

    async def acquire(self, context: dict = None):
        now = time.time()
        self.requests = [t for t in self.requests if now - t < self.period]
        if len(self.requests) >= self.max_requests:
            wait_time = self.period - (now - self.requests[0])
            context_info = f", context={context}" if context else ""
            logger.warning(f"Reached internal rate limit, current requests={len(self.requests)}/{self.max_requests}, waiting {wait_time:.2f} seconds{context_info}")
            await asyncio.sleep(wait_time)
            self.requests = self.requests[1:]
        self.requests.append(now)

# 初始化速率限制器
rate_limiter = RateLimiter(max_requests=HKGOLDEN_API["RATE_LIMIT"]["MAX_REQUESTS"], period=HKGOLDEN_API["RATE_LIMIT"]["PERIOD"])

def get_api_topics_list_key(cat_id: str, page: int, user_id: str = "%GUEST%") -> str:
    """生成帖子列表的API密鑰"""
    date_string = datetime.now().strftime("%Y%m%d")
    filter_mode = "N"
    return hashlib.md5(f"{date_string}_HKGOLDEN_{user_id}_$API#Android_1_2^{cat_id}_{page}_{filter_mode}_N".encode()).hexdigest()

def get_api_topic_details_key(thread_id: int, page: int, user_id: str = "%GUEST%") -> str:
    """生成帖子內容的API密鑰"""
    date_string = datetime.now().strftime("%Y%m%d")
    limit = 100
    start = (page - 1) * limit
    filter_mode = "N"
    return hashlib.md5(f"{date_string}_HKGOLDEN_{user_id}_$API#Android_1_2^{thread_id}_{start}_{filter_mode}_N".encode()).hexdigest()

async def get_hkgolden_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter, last_reset, rate_limit_until):
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{HKGOLDEN_API['BASE_URL']}/topics/{cat_id}",
    }
    
    items = []
    rate_limit_info = []
    max_retries = 3
    
    current_time = time.time()
    if current_time < rate_limit_until:
        rate_limit_info.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API rate limit active, retry after {datetime.fromtimestamp(rate_limit_until)}")
        logger.warning(f"API rate limit active, waiting until {datetime.fromtimestamp(rate_limit_until)}")
        return {"items": items, "rate_limit_info": rate_limit_info, "request_counter": request_counter, "last_reset": last_reset, "rate_limit_until": rate_limit_until}
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            if current_time - last_reset >= 60:
                request_counter = 0
                last_reset = current_time
            
            api_key = get_api_topics_list_key(cat_id, page)
            params = {
                "s": api_key,
                "type": cat_id,
                "page": str(page),
                "pagesize": "60",
                "user_id": "0",
                "block": "Y",
                "sensormode": "N",
                "filterMode": "N",
                "hotOnly": "N",
                "returntype": "json"
            }
            url = f"{HKGOLDEN_API['BASE_URL']}/v1/topics/{cat_id}/{page}"
            
            fetch_conditions = {
                "cat_id": cat_id,
                "sub_cat_id": sub_cat_id,
                "page": page,
                "max_pages": max_pages,
                "user_agent": headers["User-Agent"],
                "device_id": device_id,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "request_counter": request_counter,
                "last_reset": datetime.fromtimestamp(last_reset).strftime("%Y-%m-%d %H:%M:%S"),
                "rate_limit_until": datetime.fromtimestamp(rate_limit_until).strftime("%Y-%m-%d %H:%M:%S") if rate_limit_until > time.time() else "None"
            }
            
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    async with session.get(url, headers=headers, params=params, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        if response.status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - Server rate limit: cat_id={cat_id}, page={page}, "
                                f"status=429, attempt {attempt+1}, waiting {wait_time:.2f} seconds, "
                                f"Retry-After={retry_after}, request_count={request_counter}"
                            )
                            logger.warning(
                                f"Server rate limit: cat_id={cat_id}, page={page}, status=429, "
                                f"waiting {wait_time:.2f} seconds, Retry-After={retry_after}"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - Fetch failed: cat_id={cat_id}, page={page}, status={response.status}"
                            )
                            logger.error(
                                f"Fetch failed: cat_id={cat_id}, page={page}, status={response.status}, conditions={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        try:
                            data = await response.json()
                        except aiohttp.ContentTypeError:
                            rate_limit_info.append(
                                f"{current_time} - Invalid JSON response: cat_id={cat_id}, page={page}, content_type={response.content_type}"
                            )
                            logger.error(
                                f"Invalid JSON response: cat_id={cat_id}, page={page}, content_type={response.content_type}, conditions={fetch_conditions}"
                            )
                            break
                        
                        logger.info(f"Raw response for cat_id={cat_id}, page={page}: {data}")  # 記錄原始響應
                        if not data.get("result", True):
                            error_message = data.get("error_message", "Unknown error")
                            rate_limit_info.append(
                                f"{current_time} - API returned failure: cat_id={cat_id}, page={page}, error={error_message}"
                            )
                            logger.error(
                                f"API returned failure: cat_id={cat_id}, page={page}, error={error_message}, conditions={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        # 提取帖子列表
                        data_content = data.get("data", {})
                        logger.info(f"Data content for cat_id={cat_id}, page={page}: {data_content}")  # 記錄 data 內容
                        new_items = data_content.get("list", [])  # 優先使用 data["data"]["list"]
                        
                        # 兼容其他可能的結構
                        if not new_items and isinstance(data_content, dict):
                            new_items = (
                                data_content.get("items", []) or
                                data_content.get("threads", []) or
                                data_content.get("topics", []) or
                                []
                            )
                        elif isinstance(data.get("data"), list):
                            new_items = data.get("data", [])  # 兼容舊結構
                        elif isinstance(data.get("data"), str):
                            rate_limit_info.append(
                                f"{current_time} - Data is a string, cannot parse: cat_id={cat_id}, page={page}, data={data['data']}"
                            )
                            logger.error(
                                f"Data is a string: cat_id={cat_id}, page={page}, data={data['data']}, conditions={fetch_conditions}"
                            )
                            break
                        else:
                            rate_limit_info.append(
                                f"{current_time} - Invalid data structure: cat_id={cat_id}, page={page}, data={data}"
                            )
                            logger.error(
                                f"Invalid data structure: cat_id={cat_id}, page={page}, data={data}, conditions={fetch_conditions}"
                            )
                            break
                        
                        logger.info(f"Extracted items for cat_id={cat_id}, page={page}: {len(new_items)} items")  # 記錄提取的項目數
                        standardized_items = []
                        for item in new_items:
                            if not isinstance(item, dict):
                                rate_limit_info.append(
                                    f"{current_time} - Invalid item type: cat_id={cat_id}, page={page}, item={item}, type={type(item)}"
                                )
                                logger.error(
                                    f"Invalid item type: cat_id={cat_id}, page={page}, item={item}, type={type(item)}, conditions={fetch_conditions}"
                                )
                                continue
                            try:
                                standardized_items.append({
                                    "thread_id": item["id"],
                                    "title": item.get("title", "Unknown title"),
                                    "no_of_reply": item.get("totalReplies", 0),
                                    "last_reply_time": item.get("lastReplyDate", 0) / 1000,  # 毫秒轉秒
                                    "like_count": item.get("marksGood", 0),
                                    "dislike_count": item.get("marksBad", 0)
                                })
                            except (TypeError, KeyError) as e:
                                rate_limit_info.append(
                                    f"{current_time} - Item parsing error: cat_id={cat_id}, page={page}, item={item}, error={str(e)}"
                                )
                                logger.error(
                                    f"Item parsing error: cat_id={cat_id}, page={page}, item={item}, error={str(e)}, conditions={fetch_conditions}"
                                )
                                continue
                        
                        logger.info(
                            f"Fetch successful: cat_id={cat_id}, page={page}, items={len(new_items)}, "
                            f"standardized_items={len(standardized_items)}, conditions={fetch_conditions}"
                        )
                        
                        items.extend(standardized_items)
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - Fetch error: cat_id={cat_id}, page={page}, error={str(e)}"
                    )
                    logger.error(
                        f"Fetch error: cat_id={cat_id}, page={page}, error={str(e)}, conditions={fetch_conditions}"
                    )
                    await asyncio.sleep(1)
                    break
            
            await asyncio.sleep(HKGOLDEN_API["REQUEST_DELAY"])
            current_time = time.time()
    
    return {
        "items": items,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_hkgolden_thread_content(thread_id, cat_id=None, request_counter=0, last_reset=0, rate_limit_until=0):
    cache_key = f"hkgolden_thread_{thread_id}"
    if cache_key in st.session_state.thread_content_cache:
        cache_data = st.session_state.thread_content_cache[cache_key]
        if time.time() - cache_data["timestamp"] < HKGOLDEN_API["CACHE_DURATION"]:
            logger.info(f"Using thread content cache: thread_id={thread_id}")
            return cache_data["data"]
    
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{HKGOLDEN_API['BASE_URL']}/view/{thread_id}",
        "hkgauth": "null"
    }
    
    replies = []
    page = 1
    thread_title = None
    total_replies = None
    rate_limit_info = []
    max_retries = 3
    
    current_time = time.time()
    if current_time < rate_limit_until:
        rate_limit_info.append(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API rate limit active, "
            f"retry after {datetime.fromtimestamp(rate_limit_until)}"
        )
        logger.warning(f"API rate limit active, waiting until {datetime.fromtimestamp(rate_limit_until)}")
        return {
            "replies": replies,
            "title": thread_title,
            "total_replies": total_replies,
            "rate_limit_info": rate_limit_info,
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    async with aiohttp.ClientSession() as session:
        while True:
            if current_time - last_reset >= 60:
                request_counter = 0
                last_reset = current_time
            
            api_key = get_api_topic_details_key(thread_id, page)
            params = {
                "s": api_key,
                "message": str(thread_id),
                "page": str(page),
                "user_id": "0",
                "sensormode": "Y",
                "hideblock": "N",
                "returntype": "json"
            }
            url = f"{HKGOLDEN_API['BASE_URL']}/v1/view/{thread_id}/{page}"
            
            fetch_conditions = {
                "thread_id": thread_id,
                "cat_id": cat_id if cat_id else "None",
                "page": page,
                "user_agent": headers["User-Agent"],
                "device_id": device_id,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "request_counter": request_counter,
                "last_reset": datetime.fromtimestamp(last_reset).strftime("%Y-%m-%d %H:%M:%S"),
                "rate_limit_until": datetime.fromtimestamp(rate_limit_until).strftime("%Y-%m-%d %H:%M:%S") if rate_limit_until > time.time() else "None"
            }
            
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    async with session.get(url, headers=headers, params=params, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        if response.status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - Server rate limit: thread_id={thread_id}, page={page}, "
                                f"status=429, attempt {attempt+1}, waiting {wait_time:.2f} seconds, "
                                f"Retry-After={retry_after}, request_count={request_counter}"
                            )
                            logger.warning(
                                f"Server rate limit: thread_id={thread_id}, page={page}, status=429, "
                                f"waiting {wait_time:.2f} seconds, Retry-After={retry_after}"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - Fetch thread content failed: thread_id={thread_id}, page={page}, status={response.status}"
                            )
                            logger.error(
                                f"Fetch thread content failed: thread_id={thread_id}, page={page}, status={response.status}, conditions={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data = await response.json()
                        if not data.get("result", True):
                            error_message = data.get("error_message", "Unknown error")
                            rate_limit_info.append(
                                f"{current_time} - API returned failure: thread_id={thread_id}, page={page}, error={error_message}"
                            )
                            logger.error(
                                f"API returned failure: thread_id={thread_id}, page={page}, error={error_message}, conditions={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        thread_data = data.get("data", {})
                        if page == 1:
                            thread_title = thread_data.get("title", "Unknown title")
                            total_replies = thread_data.get("totalReplies", 0)
                        
                        new_replies = thread_data.get("replies", [])
                        if not new_replies:
                            break
                        
                        standardized_replies = [
                            {
                                "msg": reply.get("content", ""),
                                "like_count": reply.get("marksGood", 0),
                                "dislike_count": reply.get("marksBad", 0)
                            }
                            for reply in new_replies
                        ]
                        
                        logger.info(
                            f"Fetch thread replies successful: thread_id={thread_id}, page={page}, "
                            f"replies={len(new_replies)}, conditions={fetch_conditions}"
                        )
                        
                        replies.extend(standardized_replies)
                        page += 1
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - Fetch thread content error: thread_id={thread_id}, page={page}, error={str(e)}"
                    )
                    logger.error(
                        f"Fetch thread content error: thread_id={thread_id}, page={page}, error={str(e)}, conditions={fetch_conditions}"
                    )
                    await asyncio.sleep(1)
                    break
            
            await asyncio.sleep(HKGOLDEN_API["REQUEST_DELAY"])
            current_time = time.time()
            
            if total_replies and len(replies) >= total_replies:
                break
    
    result = {
        "replies": replies,
        "title": thread_title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
    
    st.session_state.thread_content_cache[cache_key] = {
        "data": result,
        "timestamp": time.time()
    }
    
    return result
