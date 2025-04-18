import aiohttp
import asyncio
import streamlit.logger
import time
import traceback
from config import HKGOLDEN_API

logger = streamlit.logger.get_logger(__name__)

async def get_hkgolden_topic_list(cat_id, sub_cat_id, start_page, max_pages, request_counter=0, last_reset=0, rate_limit_until=0):
    if time.time() < rate_limit_until:
        logger.warning(f"Rate limit in effect until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rate_limit_until))}")
        return {
            "items": [],
            "rate_limit_info": [f"Rate limit until {rate_limit_until}"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    current_time = time.time()
    if current_time - last_reset > HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]:
        request_counter = 0
        last_reset = current_time
    
    if request_counter >= HKGOLDEN_API["RATE_LIMIT"]["MAX_REQUESTS"]:
        rate_limit_until = last_reset + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
        logger.warning(f"Rate limit reached: {request_counter}/{HKGOLDEN_API['RATE_LIMIT']['MAX_REQUESTS']} requests")
        return {
            "items": [],
            "rate_limit_info": [f"Rate limit reached: {request_counter}/{HKGOLDEN_API['RATE_LIMIT']['MAX_REQUESTS']} requests"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    items = []
    rate_limit_info = []
    headers = {
        "User-Agent": "Streamlit-App/1.0",
        "Accept": "application/json"
    }
    if HKGOLDEN_API.get("API_KEY"):
        headers["Authorization"] = f"Bearer {HKGOLDEN_API['API_KEY']}"
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            url = f"{HKGOLDEN_API['BASE_URL']}/topics?cat_id={cat_id}&page={page}&sub_cat_id={sub_cat_id}"
            logger.info(f"Fetching cat_id={cat_id}, page={page}, url={url}")
            try:
                async with session.get(url, headers=headers, timeout=10) as response:
                    request_counter += 1
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success"):
                            items.extend(data.get("items", []))
                            logger.info(f"Fetched {len(data['items'])} items for cat_id={cat_id}, page={page}")
                        else:
                            error_msg = data.get("error", "Unknown error")
                            logger.error(f"API returned failure: cat_id={cat_id}, page={page}, error={error_msg}, response={data}")
                            rate_limit_info.append(f"API error: {error_msg}")
                            break
                    elif response.status == 429:
                        rate_limit_until = time.time() + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
                        logger.warning(f"Rate limit hit: cat_id={cat_id}, page={page}, status=429")
                        rate_limit_info.append("Rate limit hit (429)")
                        break
                    elif response.status == 404:
                        error_msg = f"HTTP 404: Category ID {cat_id} not found"
                        logger.error(f"API request failed: cat_id={cat_id}, page={page}, status={response.status}, error={error_msg}")
                        rate_limit_info.append(error_msg)
                        break
                    else:
                        error_msg = f"HTTP {response.status}: {await response.text()[:200]}"
                        logger.error(f"API request failed: cat_id={cat_id}, page={page}, status={response.status}, error={error_msg}")
                        rate_limit_info.append(f"HTTP {response.status}: {error_msg}")
                        break
            except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
                logger.error(f"Failed to fetch cat_id={cat_id}, page={page}, error={str(e)}, traceback={traceback.format_exc()}")
                rate_limit_info.append(f"Request failed: {str(e)}")
                break
            except Exception as e:
                logger.error(f"Unexpected error fetching cat_id={cat_id}, page={page}, error={str(e)}, traceback={traceback.format_exc()}")
                rate_limit_info.append(f"Unexpected error: {str(e)}")
                break
            await asyncio.sleep(HKGOLDEN_API["REQUEST_DELAY"])
    
    return {
        "items": items,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }

async def get_hkgolden_thread_content(thread_id, cat_id, request_counter=0, last_reset=0, rate_limit_until=0, max_replies=100):
    if time.time() < rate_limit_until:
        logger.warning(f"Rate limit in effect until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rate_limit_until))}")
        return {
            "replies": [],
            "title": "",
            "total_replies": 0,
            "rate_limit_info": [f"Rate limit until {rate_limit_until}"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    current_time = time.time()
    if current_time - last_reset > HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]:
        request_counter = 0
        last_reset = current_time
    
    if request_counter >= HKGOLDEN_API["RATE_LIMIT"]["MAX_REQUESTS"]:
        rate_limit_until = last_reset + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
        logger.warning(f"Rate limit reached: {request_counter}/{HKGOLDEN_API['RATE_LIMIT']['MAX_REQUESTS']} requests")
        return {
            "replies": [],
            "title": "",
            "total_replies": 0,
            "rate_limit_info": [f"Rate limit reached: {request_counter}/{HKGOLDEN_API['RATE_LIMIT']['MAX_REQUESTS']} requests"],
            "request_counter": request_counter,
            "last_reset": last_reset,
            "rate_limit_until": rate_limit_until
        }
    
    replies = []
    rate_limit_info = []
    headers = {
        "User-Agent": "Streamlit-App/1.0",
        "Accept": "application/json"
    }
    if HKGOLDEN_API.get("API_KEY"):
        headers["Authorization"] = f"Bearer {HKGOLDEN_API['API_KEY']}"
    
    async with aiohttp.ClientSession() as session:
        url = f"{HKGOLDEN_API['BASE_URL']}/thread/{thread_id}?cat_id={cat_id}"
        logger.info(f"Fetching thread_id={thread_id}, url={url}")
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                request_counter += 1
                if response.status == 200:
                    data = await response.json()
                    if data.get("success"):
                        replies = data.get("replies", [])[:max_replies]
                        title = data.get("title", "")
                        total_replies = data.get("total_replies", len(replies))
                        logger.info(f"Fetched {len(replies)} replies for thread_id={thread_id}")
                    else:
                        error_msg = data.get("error", "Unknown error")
                        logger.error(f"API returned failure: thread_id={thread_id}, error={error_msg}, response={data}")
                        rate_limit_info.append(f"API error: {error_msg}")
                elif response.status == 429:
                    rate_limit_until = time.time() + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
                    logger.warning(f"Rate limit hit: thread_id={thread_id}, status=429")
                    rate_limit_info.append("Rate limit hit (429)")
                else:
                    error_msg = f"HTTP {response.status}: {await response.text()[:200]}"
                    logger.error(f"API request failed: thread_id={thread_id}, status={response.status}, error={error_msg}")
                    rate_limit_info.append(f"HTTP {response.status}: {error_msg}")
        except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            logger.error(f"Failed to fetch thread_id={thread_id}, error={str(e)}, traceback={traceback.format_exc()}")
            rate_limit_info.append(f"Request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error fetching thread_id={thread_id}, error={str(e)}, traceback={traceback.format_exc()}")
            rate_limit_info.append(f"Unexpected error: {str(e)}")
    
    return {
        "replies": replies,
        "title": title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
