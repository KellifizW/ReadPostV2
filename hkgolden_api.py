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
        rate_limit_info.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API 速率限制中，請在 {datetime.fromtimestamp(rate_limit_until)} 後重試")
        logger.warning(f"API 速率限制中，需等待至 {datetime.fromtimestamp(rate_limit_until)}")
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
                "rate_limit_until": datetime.fromtimestamp(rate_limit_until).strftime("%Y-%m-%d %H:%M:%S") if rate_limit_until > time.time() else "無"
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
                                f"{current_time} - 伺服器速率限制: cat_id={cat_id}, page={page}, "
                                f"狀態碼=429, 第 {attempt+1} 次重試，等待 {wait_time:.2f} 秒, "
                                f"Retry-After={retry_after}, 請求計數={request_counter}"
                            )
                            logger.warning(
                                f"伺服器速率限制: cat_id={cat_id}, page={page}, 狀態碼=429, "
                                f"等待 {wait_time:.2f} 秒, Retry-After={retry_after}"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - 抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={response.status}"
                            )
                            logger.error(
                                f"抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={response.status}, 條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data = await response.json()
                        if not data.get("success", True):
                            error_message = data.get("error_message", "未知錯誤")
                            rate_limit_info.append(
                                f"{current_time} - API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}"
                            )
                            logger.error(
                                f"API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}, 條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        new_items = data.get("data", [])
                        if not new_items:
                            break
                        
                        standardized_items = [
                            {
                                "thread_id": item["id"],
                                "title": item.get("title", "未知標題"),
                                "no_of_reply": item.get("replies", 0),
                                "last_reply_time": item.get("last_reply_time", 0),
                                "like_count": item.get("like_count", 0),
                                "dislike_count": item.get("dislike_count", 0)
                            }
                            for item in new_items
                        ]
                        
                        logger.info(
                            f"成功抓取: cat_id={cat_id}, page={page}, 帖子數={len(new_items)}, "
                            f"條件={fetch_conditions}"
                        )
                        
                        items.extend(standardized_items)
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - 抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}"
                    )
                    logger.error(
                        f"抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}, 條件={fetch_conditions}"
                    )
                    await asyncio.sleep(1)
                    break
            
            await asyncio.sleep(HKGOLDEN_API["REQUEST_DELAY"])
            current_time = time.time()
    
    return {
 myśl

System: 以下是對你的請求的回應，繼續完成並更新受影響的腳本，確保與提供的 HKG API 參考程式碼的設定一致，同時修復 `test_page.py` 的語法錯誤，並保持現有功能不變。我會完成 `hkgolden_api.py` 的剩餘部分，並提供更新後的 `test_page.py`，確保所有腳本與你的需求對齊。

---

### **調整詳情**
1. **hkgolden_api.py 更新**：
   - 完成 `get_hkgolden_topic_list` 和 `get_hkgolden_thread_content` 的實現，加入參考程式碼中的 API 密鑰生成邏輯（`get_api_topics_list_key` 和 `get_api_topic_details_key`）。
   - 添加查詢參數（如 `user_id=0`、`sensormode=N`、`filterMode=N`、`block=Y`），模擬訪客模式。
   - 保留帖子內容緩存（TTL 60 秒）、速率限制管理（20 次/60 秒）、動態延遲（5 秒）。
   - 使用你提供的 API 網址 `https://api.hkgolden.com/v1/`，並假設帖子內容端點為 `https://api.hkgolden.com/v1/topic/{thread_id}/messages?page={page}&count=100`（如不正確，請提供正確端點）。
   - 標準化響應數據格式，與 LIHKG API 一致，確保 `data_processor.py` 無需修改。
2. **test_page.py 修復與更新**：
   - 修正第 204 行的 f-string 語法錯誤（`logger.warning(f"查詢失敗` → `logger.warning(f"查詢失敗: thread_id={thread_id}")`）。
   - 完成被截斷的程式碼，完善查詢帖子回覆數功能。
   - 添加高登 API 的搜索支持，參考提供的程式碼中的 `getTopicList` 搜索邏輯，使用 `https://api.hkgolden.com/v1/topics/search` 端點。
   - 確保與 `config.py` 的參數一致（如 `MIN_REPLIES`），並支持帖子內容緩存。
3. **不變的腳本**：
   - `app.py`、`chat_page.py`、`config.py`、`grok3_client.py`、`lihkg_api.py`、`data_processor.py`、`utils.py` 保持不變，因為它們不受 HKG API 調整或 `test_page.py` 修復影響。
4. **假設與限制**：
   - 假設高登 API 響應格式為 `{"success": true, "data": [...]}`，包含 `id`、`title`、`replies` 等字段。如果實際格式不同，請提供範例。
   - 假設搜索 API 端點為 `https://api.hkgolden.com/v1/topics/search?q={thread_id}&page=1&count=30`，參考提供的程式碼。
   - 使用訪客模式（`user_id=%GUEST%`），不實現登錄功能。
5. **requirements.txt**：
   - 添加 `python-md5` 模組以支持 MD5 加密，確保與參考程式碼的密鑰生成邏輯一致。

---

### **受影響的腳本**

#### **1. hkgolden_api.py**
**更新內容**：
- 完成 `get_hkgolden_topic_list` 和 `get_hkgolden_thread_content`，加入 MD5 密鑰生成邏輯。
- 添加查詢參數，確保與參考程式碼一致。
- 保留緩存、速率限制和日誌記錄邏輯。
- 使用你提供的 API 網址，並標準化數據格式。

<xaiArtifact artifact_id="65ba5ac5-3e4a-4ead-b45f-bd8250dd2916" artifact_version_id="96c730d2-2aa6-4d11-88c1-128c9f2333b1" title="hkgolden_api.py" contentType="text/python">
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
        rate_limit_info.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} - API 速率限制中，請在 {datetime.fromtimestamp(rate_limit_until)} 後重試")
        logger.warning(f"API 速率限制中，需等待至 {datetime.fromtimestamp(rate_limit_until)}")
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
                "rate_limit_until": datetime.fromtimestamp(rate_limit_until).strftime("%Y-%m-%d %H:%M:%S") if rate_limit_until > time.time() else "無"
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
                                f"{current_time} - 伺服器速率限制: cat_id={cat_id}, page={page}, "
                                f"狀態碼=429, 第 {attempt+1} 次重試，等待 {wait_time:.2f} 秒, "
                                f"Retry-After={retry_after}, 請求計數={request_counter}"
                            )
                            logger.warning(
                                f"伺服器速率限制: cat_id={cat_id}, page={page}, 狀態碼=429, "
                                f"等待 {wait_time:.2f} 秒, Retry-After={retry_after}"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - 抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={response.status}"
                            )
                            logger.error(
                                f"抓取失敗: cat_id={cat_id}, page={page}, 狀態碼={response.status}, 條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data = await response.json()
                        if not data.get("success", True):
                            error_message = data.get("error_message", "未知錯誤")
                            rate_limit_info.append(
                                f"{current_time} - API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}"
                            )
                            logger.error(
                                f"API 返回失敗: cat_id={cat_id}, page={page}, 錯誤={error_message}, 條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        new_items = data.get("data", [])
                        if not new_items:
                            break
                        
                        standardized_items = [
                            {
                                "thread_id": item["id"],
                                "title": item.get("title", "未知標題"),
                                "no_of_reply": item.get("replies", 0),
                                "last_reply_time": item.get("last_reply_time", 0),
                                "like_count": item.get("like_count", 0),
                                "dislike_count": item.get("dislike_count", 0)
                            }
                            for item in new_items
                        ]
                        
                        logger.info(
                            f"成功抓取: cat_id={cat_id}, page={page}, 帖子數={len(new_items)}, "
                            f"條件={fetch_conditions}"
                        )
                        
                        items.extend(standardized_items)
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - 抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}"
                    )
                    logger.error(
                        f"抓取錯誤: cat_id={cat_id}, page={page}, 錯誤={str(e)}, 條件={fetch_conditions}"
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
            logger.info(f"使用帖子內容緩存: thread_id={thread_id}")
            return cache_data["data"]
    
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{HKGOLDEN_API['BASE_URL']}/topic/{thread_id}",
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
                "count": "100",
                "user_id": "0",
                "block": "Y",
                "sensormode": "N",
                "filterMode": "N",
                "returntype": "json"
            }
            url = f"{HKGOLDEN_API['BASE_URL']}/v1/topic/{thread_id}/messages"
            
            fetch_conditions = {
                "thread_id": thread_id,
                "cat_id": cat_id if cat_id else "無",
                "page": page,
                "user_agent": headers["User-Agent"],
                "device_id": device_id,
                "request_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "request_counter": request_counter,
                "last_reset": datetime.fromtimestamp(last_reset).strftime("%Y-%m-%d %H:%M:%S"),
                "rate_limit_until": datetime.fromtimestamp(rate_limit_until).strftime("%Y-%m-%d %H:%M:%S") if rate_limit_until > time.time() else "無"
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
                                f"{current_time} - 伺服器速率限制: thread_id={thread_id}, page={page}, "
                                f"狀態碼=429, 第 {attempt+1} 次重試，等待 {wait_time:.2f} 秒, "
                                f"Retry-After={retry_after}, 請求計數={request_counter}"
                            )
                            logger.warning(
                                f"伺服器速率限制: thread_id={thread_id}, page={page}, 狀態碼=429, "
                                f"等待 {wait_time:.2f} 秒, Retry-After={retry_after}"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        
                        if response.status != 200:
                            rate_limit_info.append(
                                f"{current_time} - 抓取帖子內容失敗: thread_id={thread_id}, page={page}, 狀態碼={response.status}"
                            )
                            logger.error(
                                f"抓取帖子內容失敗: thread_id={thread_id}, page={page}, 狀態碼={response.status}, "
                                f"條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        data = await response.json()
                        if not data.get("success", True):
                            error_message = data.get("error_message", "未知錯誤")
                            rate_limit_info.append(
                                f"{current_time} - API 返回失敗: thread_id={thread_id}, page={page}, 錯誤={error_message}"
                            )
                            logger.error(
                                f"API 返回失敗: thread_id={thread_id}, page={page}, 錯誤={error_message}, 條件={fetch_conditions}"
                            )
                            await asyncio.sleep(1)
                            break
                        
                        if page == 1:
                            thread_title = data.get("title", "未知標題")
                            total_replies = data.get("total_replies", 0)
                        
                        new_replies = data.get("messages", [])
                        if not new_replies:
                            break
                        
                        standardized_replies = [
                            {
                                "msg": reply.get("content", ""),
                                "like_count": reply.get("like_count", 0),
                                "dislike_count": reply.get("dislike_count", 0)
                            }
                            for reply in new_replies
                        ]
                        
                        logger.info(
                            f"成功抓取帖子回覆: thread_id={thread_id}, page={page}, "
                            f"回覆數={len(new_replies)}, 條件={fetch_conditions}"
                        )
                        
                        replies.extend(standardized_replies)
                        page += 1
                        break
                    
                except Exception as e:
                    rate_limit_info.append(
                        f"{current_time} - 抓取帖子內容錯誤: thread_id={thread_id}, page={page}, 錯誤={str(e)}"
                    )
                    logger.error(
                        f"抓取帖子內容錯誤: thread_id={thread_id}, page={page}, 錯誤={str(e)}, "
                        f"條件={fetch_conditions}"
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
