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
    start_time = time.time()
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{HKGOLDEN_API['BASE_URL']}/topics/{cat_id}",
        "hkgauth": "null"
    }
    
    items = []
    rate_limit_info = []
    data_structure_errors = []
    max_retries = 3
    total_requests = 0
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            current_time = time.time()
            if current_time < rate_limit_until:
                rate_limit_info.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API rate limit active, retry after {datetime.fromtimestamp(rate_limit_until)}")
                logger.warning(f"API rate limit active, waiting until {datetime.fromtimestamp(rate_limit_until)}")
                break
            
            if current_time - last_reset >= 60:
                request_counter = 0
                last_reset = current_time
            
            api_key = get_api_topics_list_key(cat_id, page)
            params = {
                "thumb": "Y",
                "sort": "0",
                "sensormode": "Y",
                "filtermodeS": "N",
                "hideblock": "N",
                "s": api_key,
                "user_id": "0",
                "returntype": "json"
            }
            url = f"{HKGOLDEN_API['BASE_URL']}/v1/topics/{cat_id}/{page}"
            
            fetch_conditions = {
                "cat_id": cat_id,
                "page": page,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            }
            
            logger.info(f"Fetching cat_id={cat_id}, page={page}")
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    total_requests += 1
                    async with session.get(url, headers=headers, params=params, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        status = response.status
                        if status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - Server rate limit: cat_id={cat_id}, page={page}, "
                                f"status=429, attempt {attempt+1}, waiting {wait_time:.2f} seconds"
                            )
                            logger.warning(
                                f"Server rate limit: cat_id={cat_id}, page={page}, status=429, "
                                f"waiting {wait_time:.2f} seconds"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if status != 200:
                            rate_limit_info.append(
                                f"{current_time} - Fetch failed: cat_id={cat_id}, page={page}, status={status}"
                            )
                            logger.error(
                                f"Fetch failed: cat_id={cat_id}, page={page}, status={status}, url={url}"
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
                        
                        if not data.get("result", True):
                            error_message = data.get("error_message", "Unknown error")
                            rate_limit_info.append(
                                f"{current_time} - API returned failure: cat_id={cat_id}, page={page}, error={error_message}"
                            )
                            logger.error(
                                f"API returned failure: cat_id={cat_id}, page={page}, error={error_message}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        # 提取帖子列表
                        data_content = data.get("data", {})
                        logger.debug(f"API response for cat_id={cat_id}, page={page}: status={status}, data={data_content}")
                        
                        if isinstance(data_content, dict) and "maxPage" in data_content:
                            max_pages = min(max_pages, data_content["maxPage"], 10)
                            fetch_conditions["max_pages"] = max_pages
                        
                        new_items = data_content.get("list", [])
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
                                standardized_items.append({
                                    "id": item["id"],
                                    "title": item.get("title", "Unknown title"),
                                    "no_of_reply": item.get("totalReplies", 0),
                                    "last_reply_time": item.get("lastReplyDate", 0) / 1000,  # 毫秒轉秒
                                    "like_count": item.get("marksGood", 0),
                                    "dislike_count": item.get("marksBad", 0)
                                })
                            except (TypeError, KeyError) as e:
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
                        f"{current_time} - Fetch error: cat_id={cat_id}, page={page}, error={str(e)}"
                    )
                    logger.error(
                        f"Fetch error: cat_id={cat_id}, page={page}, error={str(e)}, url={url}"
                    )
                    await asyncio.sleep(1)
                    break
            
            delay = HKGOLDEN_API["REQUEST_DELAY"] * (1 + total_requests / 20)
            await asyncio.sleep(delay)
    
    elapsed_time = time.time() - start_time
    logger.info(f"Processed {len(items)} threads, total requests={total_requests}, time={elapsed_time:.2f}s")
    
    return {
        "items": items,
        "rate_limit_info": rate_limit_info,
        "data_structure_errors": data_structure_errors,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_hkgolden_thread_content(thread_id, cat_id=None, request_counter=0, last_reset=0, rate_limit_until=0, max_replies=50):
    cache_key = f"hkgolden_thread_{thread_id}"
    if cache_key in st.session_state.thread_content_cache:
        cache_data = st.session_state.thread_content_cache[cache_key]
        if time.time() - cache_data["timestamp"] < HKGOLDEN_API["CACHE_DURATION"]:
            logger.info(f"Cache hit for id={thread_id}, replies={len(cache_dat
