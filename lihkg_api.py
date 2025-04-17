import aiohttp
import asyncio
import time
from datetime import datetime
import random
import uuid
import hashlib
import streamlit as st
import streamlit.logger
from config import LIHKG_API, GENERAL

logger = streamlit.logger.get_logger(__name__)

# 隨機化的 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
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
            context_info = f", 上下文={context}" if context else ""
            logger.warning(f"達到內部速率限制，當前請求數={len(self.requests)}/{self.max_requests}，等待 {wait_time:.2f} 秒{context_info}")
            await asyncio.sleep(wait_time)
            self.requests = self.requests[1:]
        self.requests.append(now)

# 初始化速率限制器
rate_limiter = RateLimiter(max_requests=LIHKG_API["RATE_LIMIT"]["MAX_REQUESTS"], period=LIHKG_API["RATE_LIMIT"]["PERIOD"])

async def get_lihkg_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter, last_reset, rate_limit_until):
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-LI-DEVICE": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{LIHKG_API['BASE_URL']}/category/{cat_id}",
        "DNT": "1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    items = []
    rate_limit_info = []
    data_structure_errors = []
    max_retries = 3
    
    current_time = time.time()
    if current_time < rate_limit_until:
        rate_limit_info.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API 速率限制中，請在 {datetime.fromtimestamp(rate_limit_until)} 後重試")
        logger.warning(f"API 速率限制中，需等待至 {datetime.fromtimestamp(rate_limit_until)}")
        return {
            "items": items,
            "rate_limit_info": rate_limit_info,
            "data_structure_errors": data_structure_errors,
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            if current_time - last_reset >= 60:
                request_counter = 0
                last_reset = current_time
            
            url = f"{LIHKG_API['BASE_URL']}/api_v2/thread/latest?cat_id={cat_id}&page={page}&count=60&type=now&order=now"
            
            fetch_conditions = {
                "cat_id": cat_id,
                "sub_cat_id": sub_cat_id,
                "page": page,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            }
            
            logger.info(f"Fetching cat_id={cat_id}, page={page}")
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    async with session.get(url, headers=headers, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        status = response.status
                        if status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - 伺服器速率限制: cat_id={cat_id}, page={page}, "
                                f"狀態碼=429, 第 {attempt+1} 次重試，等待 {wait_time:.2f} 秒"
                            )
                            logger.warning(
                                f"伺服器速率限制: cat_id={cat_id}, page={page}, 狀態碼=429, "
                                f"等待 {wait_time:.2f} 秒"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if status != 200:
                            rate_limit_info.append(
                                f"{current_time} - 抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={status}"
                            )
                            logger.error(
                                f"抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={status}, url={url}"
                            )
                            try:
                                error_data = await response.text()
                                logger.error(f"API error response: {error_data[:200]}")
                            except Exception as e:
                                logger.error(f"Failed to read error response: {str(e)}")
                            await asyncio.sleep(1)
                            break
                        
                        try:
                            data = await response.json()
                        except aiohttp.ContentTypeError:
                            rate_limit_info.append(
                                f"{current_time} - Invalid JSON response: cat_id={cat_id}, page={page}, content_type={response.content_type}"
                            )
                            logger.error(
                                f"Invalid JSON response: cat_id={cat_id}, page={page}, content_type={response.content_type}"
                            )
                            break
                        
                        if not data.get("success"):
                            error_message = data.get("error_message", "未知錯誤")
                            rate_limit_info.append(
                                f"{current_time} - API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}"
                            )
                            logger.error(
                                f"API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data_content = data.get("response", {})
                        logger.debug(f"API response for cat_id={cat_id}, page={page}: status={status}, data={data_content}")
                        
                        new_items = data_content.get("items", [])
                        if not new_items:
                            data_structure_errors.append(
                                f"{current_time} - Empty list: cat_id={cat_id}, page={page}"
                            )
                            logger.warning(
                                f"Empty list: cat_id={cat_id}, page={page}, data={data_content}"
                            )
                            break
                        
                        logger.info(f"Fetched {len(new_items)} items for cat_id={cat_id}, page={page}")
                        standardized_items = []
                        for item in new_items:
                            if not isinstance(item, dict):
                                data_structure_errors.append(
                                    f"{current_time} - Invalid item type: cat_id={cat_id}, page={page}, type={type(item)}"
                                )
                                logger.error(
                                    f"Invalid item type: cat_id={cat_id}, page={page}, type={type(item)}"
                                )
                                continue
                            try:
                                last_reply_time = item.get("last_reply_time", 0)
                                if isinstance(last_reply_time, str):
                                    last_reply_time = datetime.fromisoformat(last_reply_time.replace("Z", "+00:00")).timestamp()
                                standardized_items.append({
                                    "thread_id": item["thread_id"],
                                    "title": item.get("title", "Unknown title"),
                                    "no_of_reply": item.get("total_replies", 0),
                                    "last_reply_time": last_reply_time,
                                    "like_count": item.get("like_count", 0),
                                    "dislike_count": item.get("dislike_count", 0)
                                })
                            except (TypeError, KeyError, ValueError) as e:
                                data_structure_errors.append(
                                    f"{current_time} - Item parsing error: cat_id={cat_id}, page={page}, error={str(e)}"
                                )
                                logger.error(
                                    f"Item parsing error: cat_id={cat_id}, page={page}, error={str(e)}"
                                )
                                continue
                        
                        items.extend(standardized_items)
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - 抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}"
                    )
                    logger.error(
                        f"抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}, url={url}"
                    )
                    await asyncio.sleep(1)
                    break
            
            await asyncio.sleep(LIHKG_API["REQUEST_DELAY"])
            current_time = time.time()
    
    return {
        "items": items,
        "rate_limit_info": rate_limit_info,
        "data_structure_errors": data_structure_errors,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_lihkg_thread_content(thread_id, cat_id=None, request_counter=0, last_reset=0, rate_limit_until=0, max_replies=50):
    cache_key = f"lihkg_thread_{thread_id}"
    if cache_key in st.session_state.thread_content_cache:
        cache_data = st.session_state.thread_content_cache[cache_key]
        if time.time() - cache_data["timestamp"] < LIHKG_API["CACHE_DURATION"]:
            logger.info(f"Cache hit for thread_id={thread_id}, replies={len(cache_data['data']['replies'])}")
            return cache_data["data"]
    
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-LI-DEVICE": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{LIHKG_API['BASE_URL']}/thread/{thread_id}",
        "DNT": "1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    replies = []
    page = 1
    thread_title = None
    total_replies = None
    rate_limit_info = []
    max_retries = 3
    request_counter_increment = 0
    pages_fetched = []
    
    current_time = time.time()
    if current_time < rate_limit_until:
        rate_limit_info.append(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API 速率限制中，"
            f"請在 {datetime.fromtimestamp(rate_limit_until)} 後重試"
        )
        logger.warning(f"API 速率限制中，需等待至 {datetime.fromtimestamp(rate_limit_until)}")
        return {
            "replies": replies,
            "title": thread_title,
            "total_replies": total_replies,
            "rate_limit_info": rate_limit_info,
            "request_counter": request_counter,
            "request_counter_increment": request_counter_increment,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    async with aiohttp.ClientSession() as session:
        while True:
            if current_time - last_reset >= 60:
                request_counter = 0
                last_reset = current_time
            
            url = f"{LIHKG_API['BASE_URL']}/api_v2/thread/{thread_id}/message?page={page}&count=100"
            
            fetch_conditions = {
                "thread_id": thread_id,
                "page": page,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            }
            
            logger.info(f"Fetching thread_id={thread_id}, page={page}")
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    request_counter_increment += 1
                    async with session.get(url, headers=headers, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        status = response.status
                        if status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - 伺服器速率限制: thread_id={thread_id}, page={page}, "
                                f"狀態碼=429, 第 {attempt+1} 次重試，等待 {wait_time:.2f} 秒"
                            )
                            logger.warning(
                                f"伺服器速率限制: thread_id={thread_id}, page={page}, 狀態碼=429, "
                                f"等待 {wait_time:.2f} 秒"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if status != 200:
                            rate_limit_info.append(
                                f"{current_time} - 抓取帖子內容失敗: thread_id={thread_id}, page={page}, 狀態碼={status}"
                            )
                            logger.error(
                                f"抓取帖子內容失敗: thread_id={thread_id}, page={page}, 狀態碼={status}, url={url}"
                            )
                            try:
                                error_data = await response.text()
                                logger.error(f"API error response: {error_data[:200]}")
                            except Exception as e:
                                logger.error(f"Failed to read error response: {str(e)}")
                            await asyncio.sleep(1)
                            break
                        
                        try:
                            data = await response.json()
                        except aiohttp.ContentTypeError:
                            rate_limit_info.append(
                                f"{current_time} - Invalid JSON response: thread_id={thread_id}, page={page}, content_type={response.content_type}"
                            )
                            logger.error(
                                f"Invalid JSON response: thread_id={thread_id}, page={page}, content_type={response.content_type}"
                            )
                            break
                        
                        if not data.get("success"):
                            error_message = data.get("error_message", "未知錯誤")
                            rate_limit_info.append(
                                f"{current_time} - API 返回失敗: thread_id={thread_id}, page={page}, 錯誤={error_message}"
                            )
                            logger.error(
                                f"API 返回失敗: thread_id={thread_id}, page={page}, 錯誤={error_message}"
                            )
                            if "998" in error_message:
                                logger.warning(f"帖子無效或無權訪問: thread_id={thread_id}, page={page}")
                                return {
                                    "replies": [],
                                    "title": None,
                                    "total_replies": 0,
                                    "rate_limit_info": rate_limit_info,
                                    "request_counter": request_counter,
                                    "request_counter_increment": request_counter_increment,
                                    "last_reset": last_reset,
                                    "rate_limit_until": rate_limit_until
                                }
                            await asyncio.sleep(1)
                            break
                        
                        response_data = data.get("response", {})
                        logger.debug(f"Thread response for thread_id={thread_id}, page={page}: data={response_data}")
                        if page == 1:
                            thread_title = response_data.get("title") or response_data.get("thread", {}).get("title", "Unknown title")
                            total_replies = response_data.get("total_replies", 0)
                        
                        new_replies = response_data.get("items", [])
                        if not new_replies:
                            break
                        
                        standardized_replies = [
                            {
                                "msg": reply.get("msg", ""),
                                "like_count": reply.get("like_count", 0),
                                "dislike_count": reply.get("dislike_count", 0)
                            }
                            for reply in new_replies if reply.get("msg", "").strip()
                        ]
                        
                        replies.extend(standardized_replies)
                        pages_fetched.append(page)
                        page += 1
                        
                        if len(replies) >= max_replies:
                            break
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - 抓取帖子內容錯誤: thread_id={thread_id}, page={page}, 錯誤={str(e)}"
                    )
                    logger.error(
                        f"抓取帖子內容錯誤: thread_id={thread_id}, page={page}, 錯誤={str(e)}, url={url}"
                    )
                    await asyncio.sleep(1)
                    break
            
            await asyncio.sleep(LIHKG_API["REQUEST_DELAY"])
            current_time = time.time()
            
            if total_replies and len(replies) >= total_replies:
                break
            if len(replies) >= max_replies:
                break
    
    result = {
        "replies": replies[:max_replies],
        "title": thread_title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "request_counter_increment": request_counter_increment,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
    
    st.session_state.thread_content_cache[cache_key] = {
        "data": result,
        "timestamp": time.time()
    }
    
    logger.info(f"Fetched {len(replies)} replies for thread_id={thread_id}, pages={len(pages_fetched)}, total_replies={total_replies}")
    return result
