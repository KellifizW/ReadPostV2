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

def filter_items(items, current_time_ms):
    """篩選帖子，根據熱度、質量、新鮮度"""
    filtered_items = []
    for item in items:
        try:
            # 篩選條件
            is_active = item["nor"] >= 10  # 回覆數 >= 10
            is_quality = item["rt"] >= 3   # 評分 >= 3
            is_recent = item["lrt"] * 1000 >= (current_time_ms - 24 * 3600 * 1000)  # 最近 24 小時
            if is_active and is_quality and is_recent:
                filtered_items.append(item)
            else:
                logger.debug(f"Filtered out: id={item['id']}, nor={item['nor']}, rt={item['rt']}, lrt={item['lrt']}")
        except (KeyError, TypeError) as e:
            logger.error(f"Filter error: item={item}, error={str(e)}")
            continue
    logger.info(f"Filtered items: total={len(items)}, filtered={len(filtered_items)}")
    return filtered_items

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
    current_time_ms = int(time.time() * 1000)  # 用於篩選
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
                "limit": "-1",
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
                        if response.status == 429:
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
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - Fetch failed: cat_id={cat_id}, page={page}, status={response.status}"
                            )
                            logger.error(
                                f"Fetch failed: cat_id={cat_id}, page={page}, status={response.status}"
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
                        logger.debug(f"Data content for cat_id={cat_id}, page={page}: type={type(data_content)}, keys={list(data_content.keys()) if isinstance(data_content, dict) else []}")
                        
                        # 動態調整 max_pages
                        if isinstance(data_content, dict) and "maxPage" in data_content:
                            max_pages = min(max_pages, data_content["maxPage"], 10)
                            fetch_conditions["max_pages"] = max_pages
                        
                        new_items = data_content.get("list", [])
                        if not new_items:
                            data_structure_errors.append(
                                f"{current_time} - Empty list: cat_id={cat_id}, page={page}"
                            )
                            logger.error(
                                f"Empty list: cat_id={cat_id}, page={page}"
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
                                    "nor": item.get("totalReplies", 0),
                                    "lrt": item.get("lastReplyDate", 0) / 1000,  # 毫秒轉秒
                                    "rt": item.get("marksGood", 0) - item.get("marksBad", 0)
                                })
                            except (TypeError, KeyError) as e:
                                data_structure_errors.append(
                                    f"{current_time} - Item parsing error: cat_id={cat_id}, page={page}, error={str(e)}"
                                )
                                logger.error(
                                    f"Item parsing error: cat_id={cat_id}, page={page}, error={str(e)}"
                                )
                                continue
                        
                        # 篩選帖子
                        filtered_items = filter_items(standardized_items, current_time_ms)
                        
                        # 並行獲取回覆（最多 5 個並發）
                        max_concurrent = 5
                        for i in range(0, len(filtered_items), max_concurrent):
                            batch = filtered_items[i:i + max_concurrent]
                            thread_tasks = [
                                get_hkgolden_thread_content(
                                    thread_id=item["id"],
                                    cat_id=cat_id,
                                    request_counter=request_counter,
                                    last_reset=last_reset,
                                    rate_limit_until=rate_limit_until,
                                    max_replies=50
                                ) for item in batch
                            ]
                            thread_results = await asyncio.gather(*thread_tasks, return_exceptions=True)
                            
                            for item, thread_data in zip(batch, thread_results):
                                if isinstance(thread_data, Exception):
                                    logger.error(f"Thread fetch error: id={item['id']}, error={str(thread_data)}")
                                    continue
                                no_of_reply = thread_data.get("tr", item["nor"])
                                if no_of_reply is None:
                                    logger.warning(f"Invalid no_of_reply for id={item['id']}, using nor={item['nor']}")
                                    no_of_reply = item["nor"]
                                item["no_of_reply"] = no_of_reply
                                item["replies"] = thread_data.get("replies", [])[:50]
                                request_counter = thread_data.get("request_counter", request_counter)
                                total_requests += thread_data.get("request_counter_increment", 0)
                                last_reset = thread_data.get("last_reset", last_reset)
                                rate_limit_until = thread_data.get("rate_limit_until", rate_limit_until)
                                rate_limit_info.extend(thread_data.get("rate_limit_info", []))
                        
                        # 過濾無效 no_of_reply 的帖子
                        valid_items = [item for item in filtered_items if item["no_of_reply"] is not None]
                        items.extend(valid_items)
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - Fetch error: cat_id={cat_id}, page={page}, error={str(e)}"
                    )
                    logger.error(
                        f"Fetch error: cat_id={cat_id}, page={page}, error={str(e)}"
                    )
                    await asyncio.sleep(1)
                    break
            
            # 動態延遲
            delay = HKGOLDEN_API["REQUEST_DELAY"] * (1 + total_requests / 20)
            await asyncio.sleep(delay)
    
    # 總結日誌
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
            logger.info(f"Using thread content cache: id={thread_id}")
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
    request_counter_increment = 0
    pages_fetched = []
    start_time = time.time()
    
    current_time = time.time()
    if current_time < rate_limit_until:
        rate_limit_info.append(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API rate limit active, retry after {datetime.fromtimestamp(rate_limit_until)}"
        )
        logger.warning(f"API rate limit active, waiting until {datetime.fromtimestamp(rate_limit_until)}")
        return {
            "replies": replies,
            "title": thread_title,
            "tr": total_replies,
            "rate_limit_info": rate_limit_info,
            "request_counter": request_counter,
            "request_counter_increment": request_counter_increment,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    async with aiohttp.ClientSession() as session:
        logger.info(f"Fetching id={thread_id}, pages=1-all")
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
                "page": page,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            }
            
            for attempt in range(max_retries):
                try:
                    await rate_limiter.acquire(context=fetch_conditions)
                    request_counter += 1
                    request_counter_increment += 1
                    async with session.get(url, headers=headers, params=params, timeout=10) as response:
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        if response.status == 429:
                            retry_after = response.headers.get("Retry-After", "5")
                            wait_time = int(retry_after) if retry_after.isdigit() else 5
                            wait_time = min(wait_time * (2 ** attempt), 60) + random.uniform(0, 0.1)
                            rate_limit_until = time.time() + wait_time
                            rate_limit_info.append(
                                f"{current_time} - Server rate limit: id={thread_id}, page={page}, "
                                f"status=429, attempt {attempt+1}, waiting {wait_time:.2f} seconds"
                            )
                            logger.warning(
                                f"Server rate limit: id={thread_id}, page={page}, status=429, "
                                f"waiting {wait_time:.2f} seconds"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - Fetch thread content failed: id={thread_id}, page={page}, status={response.status}"
                            )
                            logger.error(
                                f"Fetch thread content failed: id={thread_id}, page={page}, status={response.status}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data = await response.json()
                        if not data.get("result", True):
                            error_message = data.get("error_message", "Unknown error")
                            rate_limit_info.append(
                                f"{current_time} - API returned failure: id={thread_id}, page={page}, error={error_message}"
                            )
                            logger.error(
                                f"API returned failure: id={thread_id}, page={page}, error={error_message}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        thread_data = data.get("data", {})
                        if page == 1:
                            thread_title = thread_data.get("title", "Unknown title")
                            total_replies = thread_data.get("totalReplies", None)
                        
                        new_replies = thread_data.get("replies", [])
                        if not new_replies and page > 1:
                            break
                        
                        standardized_replies = [
                            {
                                "msg": reply.get("content", ""),
                                "like_count": reply.get("like_count", 0),  # 若 API 提供
                                "dislike_count": reply.get("dislike_count", 0)  # 若 API 提供
                            }
                            for reply in new_replies
                        ]
                        
                        replies.extend(standardized_replies)
                        pages_fetched.append(page)
                        page += 1
                        
                        # 檢查是否達到最大回覆數
                        if len(replies) >= max_replies:
                            break
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - Fetch thread content error: id={thread_id}, page={page}, error={str(e)}"
                    )
                    logger.error(
                        f"Fetch thread content error: id={thread_id}, page={page}, error={str(e)}"
                    )
                    await asyncio.sleep(1)
                    break
            
            # 動態延遲
            delay = HKGOLDEN_API["REQUEST_DELAY"] * (1 + request_counter_increment / 20)
            await asyncio.sleep(delay)
            current_time = time.time()
            
            if total_replies and len(replies) >= total_replies:
                break
            if len(replies) >= max_replies:
                break
        
        # 確保 tr 有效
        if total_replies is None:
            total_replies = len(replies)
            logger.warning(f"Missing totalReplies for id={thread_id}, using len(replies)={total_replies}")
        
        # 總結日誌
        pages_str = f"1-{max(pages_fetched)}" if pages_fetched else "none"
        logger.info(
            f"Fetched {len(replies)} replies for id={thread_id}, pages={pages_str}, tr={total_replies}, requests={request_counter_increment}"
        )
    
    result = {
        "replies": replies[:max_replies],
        "title": thread_title,
        "tr": total_replies,
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
    
    return result
