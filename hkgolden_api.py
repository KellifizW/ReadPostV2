import aiohttp
import asyncio
import streamlit.logger
import time
import traceback
import hashlib
import uuid
import random
from datetime import datetime
from config import HKGOLDEN_API

logger = streamlit.logger.get_logger(__name__)

# 隨機化的 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"
]

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

async def fetch_with_retry(session, url, headers, params, retries=3, backoff_factor=1):
    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, params=params, timeout=10) as response:
                if response.status == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit hit for {url}, retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                data = await response.json()
                logger.debug(f"Raw response from {url}: {data}")
                return data, response.status
        except (aiohttp.ClientResponseError, aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            logger.warning(f"Attempt {attempt + 1}/{retries} failed for {url}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(backoff_factor * (2 ** attempt))
            else:
                logger.error(f"All {retries} attempts failed for {url}: {str(e)}, traceback={traceback.format_exc()}")
                return None, response.status if 'response' in locals() else 500
    return None, 500

async def get_hkgolden_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter, last_reset, rate_limit_until):
    if time.time() < rate_limit_until:
        logger.warning(f"Rate limit active until {time.ctime(rate_limit_until)}, skipping request")
        return {
            "items": [],
            "rate_limit_info": [f"Rate limit active until {time.ctime(rate_limit_until)}"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }

    base_url = HKGOLDEN_API.get("BASE_URL", "https://api.hkgolden.com")
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{base_url}/topics/{cat_id}",
        "hkgauth": "null"
    }
    if auth_token := HKGOLDEN_API.get("AUTH_TOKEN"):
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key := HKGOLDEN_API.get("API_KEY"):
        headers["HKGAuth"] = api_key

    items = []
    rate_limit_info = []
    rate_limit_window = HKGOLDEN_API.get("RATE_LIMIT_WINDOW", 3600)
    rate_limit_requests = HKGOLDEN_API.get("RATE_LIMIT_REQUESTS", 100)

    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            api_key = get_api_topics_list_key(cat_id, page)
            query_params = {
                "thumb": "Y",
                "sort": "0",
                "sensormode": "Y",
                "filtermodeS": "N",
                "hideblock": "N",
                "s": api_key,
                "user_id": "0",
                "returntype": "json"
            }
            endpoint = f"{base_url}/v1/topics/{cat_id}/{page}"
            data, status = await fetch_with_retry(session, endpoint, headers, query_params)
            logger.debug(f"Tried endpoint {endpoint}, status={status}, data={data}")

            if data and data.get("result", True):
                request_counter += 1
                if request_counter >= rate_limit_requests:
                    rate_limit_until = time.time() + rate_limit_window
                    rate_limit_info.append(f"Rate limit reached: {request_counter} requests")

                data_content = data.get("data", {})
                if isinstance(data_content, dict) and "maxPage" in data_content:
                    max_pages = min(max_pages, data_content["maxPage"], 10)

                new_items = data_content.get("list", [])
                if not isinstance(new_items, (list, tuple)):
                    error_msg = f"Unexpected data format for cat_id={cat_id}, page={page}, endpoint={endpoint}, data={data_content}"
                    logger.error(error_msg)
                    rate_limit_info.append(error_msg)
                    continue

                if not new_items:
                    error_msg = f"Empty post list for cat_id={cat_id}, page={page}, endpoint={endpoint}, data={data_content}"
                    logger.warning(error_msg)
                    rate_limit_info.append(error_msg)
                    continue

                for item in new_items:
                    try:
                        items.append({
                            "id": item.get("thread_id", item.get("id", "")),
                            "title": item.get("title", ""),
                            "no_of_reply": int(item.get("no_of_reply", item.get("totalReplies", 0))),
                            "create_time": int(item.get("create_time", item.get("messageDate", 0))) / 1000,
                            "last_reply_time": int(item.get("orderDate", item.get("lastReplyDate", 0))) / 1000,
                            "like_count": int(item.get("marksGood", 0)),
                            "dislike_count": int(item.get("marksBad", 0))
                        })
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning(f"Invalid post data: {item}, error={str(e)}")
                        continue
            else:
                error_msg = f"No posts found in response for cat_id={cat_id}, page={page}, endpoint={endpoint}, status={status}, data={data}"
                if data and not data.get("result", True):
                    error_msg += f", error_message={data.get('error_message', 'Unknown error')}"
                logger.error(error_msg)
                rate_limit_info.append(error_msg)

            if len(items) >= 10:
                break

            delay = HKGOLDEN_API.get("REQUEST_DELAY", 0.5)
            await asyncio.sleep(delay)

        if time.time() - last_reset > rate_limit_window:
            request_counter = 0
            last_reset = time.time()

    return {
        "items": items,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_hkgolden_thread_content(thread_id, cat_id, request_counter, last_reset, rate_limit_until, max_replies):
    if time.time() < rate_limit_until:
        logger.warning(f"Rate limit active until {time.ctime(rate_limit_until)}, skipping thread request")
        return {
            "replies": [],
            "title": "",
            "total_replies": 0,
            "rate_limit_info": [f"Rate limit active until {time.ctime(rate_limit_until)}"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }

    base_url = HKGOLDEN_API.get("BASE_URL", "https://api.hkgolden.com")
    device_id = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-DEVICE-ID": device_id,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{base_url}/view/{thread_id}",
        "hkgauth": "null"
    }
    if auth_token := HKGOLDEN_API.get("AUTH_TOKEN"):
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key := HKGOLDEN_API.get("API_KEY"):
        headers["HKGAuth"] = api_key

    rate_limit_info = []
    rate_limit_window = HKGOLDEN_API.get("RATE_LIMIT_WINDOW", 3600)
    rate_limit_requests = HKGOLDEN_API.get("RATE_LIMIT_REQUESTS", 100)
    replies = []
    title = ""
    total_replies = 0

    async with aiohttp.ClientSession() as session:
        page = 1
        while True:
            api_key = get_api_topic_details_key(thread_id, page)
            query_params = {
                "s": api_key,
                "message": str(thread_id),
                "page": str(page),
                "user_id": "0",
                "sensormode": "Y",
                "hideblock": "N",
                "returntype": "json"
            }
            endpoint = f"{base_url}/v1/view/{thread_id}/{page}"
            data, status = await fetch_with_retry(session, endpoint, headers, query_params)
            logger.debug(f"Tried thread endpoint {endpoint}, status={status}, data={data}")

            if data and data.get("result", True):
                request_counter += 1
                if request_counter >= rate_limit_requests:
                    rate_limit_until = time.time() + rate_limit_window
                    rate_limit_info.append(f"Rate limit reached: {request_counter} requests")

                thread_data = data.get("data", {})
                if page == 1:
                    title = thread_data.get("title", "")
                    total_replies = int(thread_data.get("totalReplies", thread_data.get("no_of_reply", 0)))

                new_replies = thread_data.get("replies", [])
                if not new_replies and page > 1:
                    break

                for reply in new_replies:
                    msg = reply.get("content", reply.get("msg", ""))
                    if msg.strip():
                        replies.append({
                            "msg": msg,
                            "reply_time": int(reply.get("time", 0)) / 1000,
                            "like_count": reply.get("like_count", 0),
                            "dislike_count": reply.get("dislike_count", 0)
                        })

                if len(replies) >= max_replies:
                    break
                page += 1
            else:
                error_msg = f"Thread fetch failed for thread_id={thread_id}, endpoint={endpoint}, status={status}, data={data}"
                if data and not data.get("result", True):
                    error_msg += f", error_message={data.get('error_message', 'Unknown error')}"
                logger.error(error_msg)
                rate_limit_info.append(error_msg)
                break

            delay = HKGOLDEN_API.get("REQUEST_DELAY", 0.5)
            await asyncio.sleep(delay)

        if time.time() - last_reset > rate_limit_window:
            request_counter = 0
            last_reset = time.time()

    return {
        "replies": replies[:max_replies],
        "title": title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
