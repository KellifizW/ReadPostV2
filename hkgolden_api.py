import aiohttp
import asyncio
import streamlit.logger
import time
import traceback
from config import HKGOLDEN_API
import streamlit as st
import json

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
    
    # 恢復原始查詢參數，匹配提供的 URL
    query_params = {
        "thumb": "Y",
        "sort": "0",
        "sensormode": "Y",
        "filtermodeS": "N",
        "hideblock": "N",
        "limit": "-1"
    }
    
    async with aiohttp.ClientSession() as session:
        for page in range(start_page, start_page + max_pages):
            url = f"{HKGOLDEN_API['BASE_URL']}/topics/{cat_id}/{page}"
            logger.info(f"Fetching cat_id={cat_id}, page={page}, url={url}")
            try:
                async with session.get(url, headers=headers, params=query_params, timeout=10) as response:
                    request_counter += 1
                    response_text = await response.text()
                    if response.status == 200:
                        data = await response.json()
                        logger.debug(f"API response: {json.dumps(data, ensure_ascii=False)[:1000]}...")  # 記錄完整回應
                        if data.get("result"):
                            post_list = data.get("data", {}).get("list", [])
                            logger.info(f"Found {len(post_list)} posts in post_list for cat_id={cat_id}, page={page}")
                            if not post_list:
                                error_msg = f"No posts found in data.data.list for cat_id={cat_id}, page={page}"
                                logger.warning(error_msg)
                                rate_limit_info.append(error_msg)
                                break
                            for post in post_list:
                                # 記錄原始帖子數據
                                logger.debug(f"Raw post data: {json.dumps(post, ensure_ascii=False)[:500]}...")
                                # 嘗試多種字段名稱，適應未知結構
                                item = {
                                    "id": post.get("id", post.get("post_id", "")),
                                    "title": post.get("title", post.get("subject", "")),
                                    "no_of_reply": post.get("no_of_reply", post.get("reply_count", 0)),
                                    "last_reply_time": post.get("orderDate", post.get("last_reply", 0)) // 1000,
                                    "like_count": post.get("like_count", post.get("likes", 0)),
                                    "dislike_count": post.get("dislike_count", post.get("dislikes", 0))
                                }
                                # 檢查是否缺少關鍵字段
                                if not item["id"] or not item["title"]:
                                    logger.warning(f"Missing key fields in post: id={item['id']}, title={item['title']}, raw_post={json.dumps(post, ensure_ascii=False)[:200]}")
                                    continue
                                items.append(item)
                            logger.info(f"Fetched {len(items)} valid items for cat_id={cat_id}, page={page}")
                        else:
                            error_msg = data.get("error", "Unknown error")
                            logger.error(f"API returned failure: cat_id={cat_id}, page={page}, error={error_msg}, response={response_text[:200]}")
                            rate_limit_info.append(f"API error: {error_msg}")
                            break
                    elif response.status == 429:
                        rate_limit_until = time.time() + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
                        logger.warning(f"Rate limit hit: cat_id={cat_id}, page={page}, status=429")
                        rate_limit_info.append("Rate limit hit (429)")
                        break
                    elif response.status == 404:
                        error_msg = f"HTTP 404: Category ID {cat_id} not found"
                        logger.error(f"API request failed: cat_id={cat_id}, page={page}, status={response.status}, error={error_msg}, response={response_text[:200]}")
                        rate_limit_info.append(error_msg)
                        break
                    else:
                        error_msg = f"HTTP {response.status}: {response_text[:200]}"
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
    
    if not items:
        error_msg = f"No valid posts fetched for cat_id={cat_id}. Check API response fields or query parameters."
        logger.error(error_msg)
        rate_limit_info.append(error_msg)
    
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
    
    # 查詢參數與話題列表一致
    query_params = {
        "thumb": "Y",
        "sort": "0",
        "sensormode": "Y",
        "filtermodeS": "N",
        "hideblock": "N",
        "limit": "-1"
    }
    
    async with aiohttp.ClientSession() as session:
        url = f"{HKGOLDEN_API['BASE_URL']}/thread/{thread_id}"
        logger.info(f"Fetching thread_id={thread_id}, url={url}")
        try:
            async with session.get(url, headers=headers, params=query_params, timeout=10) as response:
                request_counter += 1
                response_text = await response.text()
                if response.status == 200:
                    data = await response.json()
                    logger.debug(f"Thread API response: {json.dumps(data, ensure_ascii=False)[:1000]}...")
                    if data.get("result"):
                        reply_list = data.get("data", {}).get("replies", [])[:max_replies]
                        logger.info(f"Found {len(reply_list)} replies for thread_id={thread_id}")
                        for reply in reply_list:
                            reply_data = {
                                "msg": reply.get("content", reply.get("message", ""))
                            }
                            if not reply_data["msg"]:
                                logger.warning(f"Missing content in reply: raw_reply={json.dumps(reply, ensure_ascii=False)[:200]}")
                                continue
                            replies.append(reply_data)
                        title = data.get("data", {}).get("title", data.get("data", {}).get("subject", ""))
                        total_replies = data.get("data", {}).get("total_replies", len(replies))
                        logger.info(f"Fetched {len(replies)} valid replies for thread_id={thread_id}")
                    else:
                        error_msg = data.get("error", "Unknown error")
                        logger.error(f"API returned failure: thread_id={thread_id}, error={error_msg}, response={response_text[:200]}")
                        rate_limit_info.append(f"API error: {error_msg}")
                elif response.status == 429:
                    rate_limit_until = time.time() + HKGOLDEN_API["RATE_LIMIT"]["PERIOD"]
                    logger.warning(f"Rate limit hit: thread_id={thread_id}, status=429")
                    rate_limit_info.append("Rate limit hit (429)")
                else:
                    error_msg = f"HTTP {response.status}: {response_text[:200]}"
                    logger.error(f"API request failed: thread_id={thread_id}, status={response.status}, error={error_msg}")
                    rate_limit_info.append(f"HTTP {response.status}: {error_msg}")
        except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            logger.error(f"Failed to fetch thread_id={thread_id}, error={str(e)}, traceback={traceback.format_exc()}")
            rate_limit_info.append(f"Request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error fetching thread_id={thread_id}, error={str(e)}, traceback={traceback.format_exc()}")
            rate_limit_info.append(f"Unexpected error: {str(e)}")
    
    if not replies and data.get("result"):
        error_msg = f"No valid replies fetched for thread_id={thread_id}. Check API response fields."
        logger.warning(error_msg)
        rate_limit_info.append(error_msg)
    
    return {
        "replies": replies,
        "title": title,
        "total_replies": total_replies,
        "rate_limit_info": rate_limit_info,
        "request_counter": request_counter,
        "last_reset": last_reset,
        "rate_limit_until": rate_limit_until
    }
