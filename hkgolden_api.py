import aiohttp
import asyncio
import streamlit.logger
import time
import traceback
from config import HKGOLDEN_API

logger = streamlit.logger.get_logger(__name__)

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
    headers = {
        "User-Agent": HKGOLDEN_API.get("USER_AGENT", "Streamlit-App/1.0"),
        "Accept": "application/json"
    }
    items = []
    rate_limit_info = []
    api_key = HKGOLDEN_API.get("API_KEY")
    rate_limit_window = HKGOLDEN_API.get("RATE_LIMIT_WINDOW", 3600)  # 預設 1 小時
    rate_limit_requests = HKGOLDEN_API.get("RATE_LIMIT_REQUESTS", 100)  # 預設 100 次

    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            query_params = {
                "thumb": "Y",
                "sort": "0",
                "sensormode": "Y",
                "filtermodeS": "N",
                "hideblock": "N",
                "page": str(page),
                "limit": "-1"
            }
            if api_key:
                query_params["access_token"] = api_key

            endpoint = f"{base_url}/v1/topics/{cat_id}/{page}"
            data, status = await fetch_with_retry(session, endpoint, headers, query_params)
            logger.debug(f"Tried endpoint {endpoint}, status={status}, data={data}")

            if data and data.get("data"):
                request_counter += 1
                if request_counter >= rate_limit_requests:
                    rate_limit_until = time.time() + rate_limit_window
                    rate_limit_info.append(f"Rate limit reached: {request_counter} requests")

                for post in data["data"]:
                    try:
                        items.append({
                            "id": post.get("thread_id", post.get("id", "")),
                            "title": post.get("title", ""),
                            "no_of_reply": int(post.get("no_of_reply", 0)),
                            "create_time": int(post.get("create_time", 0)) / 1000,
                            "last_reply_time": int(post.get("orderDate", 0)) / 1000,
                            "like_count": int(post.get("like_count", 0)),
                            "dislike_count": int(post.get("dislike_count", 0))
                        })
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid post data: {post}, error={str(e)}")
                        continue
            else:
                error_msg = f"No posts found in response for cat_id={cat_id}, page={page}, endpoint={endpoint}, status={status}"
                logger.error(error_msg)
                rate_limit_info.append(error_msg)

            if len(items) >= 10:
                break

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
    headers = {
        "User-Agent": HKGOLDEN_API.get("USER_AGENT", "Streamlit-App/1.0"),
        "Accept": "application/json"
    }
    rate_limit_info = []
    api_key = HKGOLDEN_API.get("API_KEY")
    rate_limit_window = HKGOLDEN_API.get("RATE_LIMIT_WINDOW", 3600)  # 預設 1 小時
    rate_limit_requests = HKGOLDEN_API.get("RATE_LIMIT_REQUESTS", 100)  # 預設 100 次
    replies = []
    title = ""
    total_replies = 0

    async with aiohttp.ClientSession() as session:
        page = 1
        while True:
            query_params = {
                "sensormode": "Y",
                "hideblock": "N",
                "page": str(page)
            }
            if api_key:
                query_params["access_token"] = api_key

            endpoint = f"{base_url}/v1/view/{thread_id}/{page}"
            data, status = await fetch_with_retry(session, endpoint, headers, query_params)
            logger.debug(f"Tried thread endpoint {endpoint}, status={status}, data={data}")

            if data and data.get("data"):
                request_counter += 1
                if request_counter >= rate_limit_requests:
                    rate_limit_until = time.time() + rate_limit_window
                    rate_limit_info.append(f"Rate limit reached: {request_counter} requests")

                try:
                    title = data["data"].get("title", title)
                    total_replies = int(data["data"].get("no_of_reply", total_replies))
                    for reply in data["data"].get("replies", []):
                        msg = reply.get("message", reply.get("msg", ""))
                        if msg:
                            replies.append({
                                "msg": msg,
                                "reply_time": int(reply.get("time", 0)) / 1000
                            })
                    if len(replies) >= max_replies or not data["data"].get("has_next_page", False):
                        break
                    page += 1
                except (ValueError, TypeError, KeyError) as e:
                    logger.error(f"Invalid thread data for thread_id={thread_id}: {str(e)}, data={data}")
                    rate_limit_info.append(f"Invalid thread data: {str(e)}")
                    break
            else:
                error_msg = f"Thread fetch failed for thread_id={thread_id}, endpoint={endpoint}, status={status}"
                logger.error(error_msg)
                rate_limit_info.append(error_msg)
                break

        if time.time() - last_reset > rate_limit_window:
            request_counter = 0
            last_reset = time.time()

    return {
        "replies": replies[:max_replies] if max_replies > 0 else replies,
        "title": title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
