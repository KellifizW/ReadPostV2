import streamlit as st
import asyncio
import time
from datetime import datetime
import random
import pytz
import aiohttp
import hashlib
import json
from lihkg_api import get_lihkg_topic_list
from hkgolden_api import get_hkgolden_topic_list
import streamlit.logger
from config import LIHKG_API, HKGOLDEN_API, GENERAL, GROK3_API
from threading import Lock

logger = st.logger.get_logger(__name__)
HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])
api_test_lock = Lock()

def get_api_search_key(query: str, page: int, user_id: str = "%GUEST%") -> str:
    """生成搜索API密鑰"""
    date_string = datetime.now().strftime("%Y%m%d")
    filter_mode = "N"
    return hashlib.md5(f"{date_string}_HKGOLDEN_{user_id}_$API#Android_1_2^{query}_{page}_{filter_mode}_N".encode()).hexdigest()

async def search_thread_by_id(thread_id, platform):
    """使用搜尋 API 查詢帖子詳細信息"""
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        ]),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{LIHKG_API['BASE_URL'] if platform == 'LIHKG' else HKGOLDEN_API['BASE_URL']}/",
        "hkgauth": "null"
    }
    
    if platform == "LIHKG":
        url = f"{LIHKG_API['BASE_URL']}/api_v2/thread/search?q={thread_id}&page=1&count=30&sort=score&type=thread"
        params = {}
    else:
        api_key = get_api_search_key(str(thread_id), 1)
        url = f"{HKGOLDEN_API['BASE_URL']}/v1/view/{thread_id}/1"
        params = {
            "s": api_key,
            "message": str(thread_id),
            "page": "1",
            "user_id": "0",
            "sensormode": "Y",
            "hideblock": "N",
            "returntype": "json"
        }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, params=params if platform == "HKGOLDEN" else None) as response:
                if response.status != 200:
                    logger.error(f"Search thread failed: platform={platform}, thread_id={thread_id}, status={response.status}")
                    return None
                data = await response.json()
                if not data.get("result", True):
                    logger.error(f"Search thread API returned failure: platform={platform}, thread_id={thread_id}, error={data.get('error_message', 'Unknown error')}")
                    return None
                thread_data = data.get("data", {}) if platform == "HKGOLDEN" else data["response"].get("items", [{}])[0]
                if not thread_data:
                    logger.warning(f"Search thread no results: platform={platform}, thread_id={thread_id}")
                    return None
                return {
                    "thread_id": thread_id,
                    "title": thread_data.get("title", "Unknown title"),
                    "no_of_reply": thread_data.get("totalReplies", 0),
                    "last_reply_time": thread_data.get("lastReplyDate", 0) / 1000,
                    "like_count": thread_data.get("marksGood", 0),
                    "dislike_count": thread_data.get("marksBad", 0)
                }
        except Exception as e:
            logger.error(f"Search thread error: platform={platform}, thread_id={thread_id}, error={str(e)}")
            return None

async def test_grok3_api(prompt, high_reasoning=False):
    """測試 Grok 3 API"""
    try:
        api_key = st.secrets["grok3key"]
    except KeyError:
        logger.error("Grok 3 API key is missing in Streamlit secrets (grok3key).")
        return {"error": "Grok 3 API key is missing. Please configure 'grok3key' in Streamlit secrets."}
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": "grok-3",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": False
    }
    
    if high_reasoning:
        payload["reasoning"] = {"effort": "high"}
    
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"Sending Grok 3 API test request: url={GROK3_API['BASE_URL']}/chat/completions, prompt={prompt[:100]}..., high_reasoning={high_reasoning}")
            async with session.post(
                f"{GROK3_API['BASE_URL']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=180
            ) as response:
                response_text = await response.text()
                if response.status != 200:
                    logger.error(f"Grok 3 API test failed: status={response.status}, reason={response_text}")
                    return {"error": f"API request failed with status {response.status}: {response_text}"}
                data = json.loads(response_text)
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "No content")
                logger.info(f"Grok 3 API test succeeded: content_length={len(content)}")
                return {"content": content, "request": {"url": f"{GROK3_API['BASE_URL']}/chat/completions", "payload": payload}}
        except aiohttp.ClientError as e:
            logger.error(f"Grok 3 API test connection error: type={type(e).__name__}, message={str(e)}")
            return {"error": f"API connection failed - {str(e)}"}
        except asyncio.TimeoutError as e:
            logger.error(f"Grok 3 API test timeout error: {str(e)}")
            return {"error": f"API request timed out - {str(e)}"}
        except Exception as e:
            logger.error(f"Grok 3 API test unexpected error: type={type(e).__name__}, message={str(e)}")
            return {"error": f"API unexpected error - {str(e)}"}

async def test_page():
    st.title("討論區數據測試頁面")
    
    if "rate_limit_until" not in st.session_state:
        st.session_state.rate_limit_until = 0
    if "request_counter" not in st.session_state:
        st.session_state.request_counter = 0
    if "last_reset" not in st.session_state:
        st.session_state.last_reset = time.time()
    if "thread_content_cache" not in st.session_state:
        st.session_state.thread_content_cache = {}
    if "last_api_test_time" not in st.session_state:
        st.session_state.last_api_test_time = 0
    
    if time.time() < st.session_state.rate_limit_until:
        st.error(f"API rate limit active, retry after {datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        return
    
    platform = st.selectbox("Choose discussion platform", ["LIHKG", "高登討論區"], index=0)
    if platform == "LIHKG":
        cat_id_map = LIHKG_API["CATEGORIES"]
        min_replies = LIHKG_API["MIN_REPLIES"]
    else:
        cat_id_map = HKGOLDEN_API["CATEGORIES"]
        min_replies = HKGOLDEN_API["MIN_REPLIES"]
    
    col1, col2 = st.columns([3, 1])
    with col2:
        selected_cat = st.selectbox(
            "Choose category",
            options=list(cat_id_map.keys()),
            index=0
        )
        cat_id = cat_id_map[selected_cat]
        max_pages = st.slider("Pages to fetch", 1, 5, LIHKG_API["MAX_PAGES"] if platform == "LIHKG" else HKGOLDEN_API["MAX_PAGES"])
    
    with col1:
        st.markdown("#### Rate Limit Status")
        st.markdown(f"- Current request count: {st.session_state.request_counter}")
        st.markdown(f"- Last reset time: {datetime.fromtimestamp(st.session_state.last_reset, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        st.markdown(
            f"- Rate limit expiry: "
            f"{datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if st.session_state.rate_limit_until > time.time() else 'None'}"
        )
        
        if st.button("Fetch Data"):
            with st.spinner("Fetching data..."):
                logger.info(f"Starting data fetch: platform={platform}, category={selected_cat}, cat_id={cat_id}, pages={max_pages}")
                
                if platform == "LIHKG":
                    result = await get_lihkg_topic_list(
                        cat_id,
                        sub_cat_id=0,
                        start_page=1,
                        max_pages=max_pages,
                        request_counter=st.session_state.request_counter,
                        last_reset=st.session_state.last_reset,
                        rate_limit_until=st.session_state.rate_limit_until
                    )
                else:
                    result = await get_hkgolden_topic_list(
                        cat_id,
                        sub_cat_id=0,
                        start_page=1,
                        max_pages=max_pages,
                        request_counter=st.session_state.request_counter,
                        last_reset=st.session_state.last_reset,
                        rate_limit_until=st.session_state.rate_limit_until
                    )
                
                items = result["items"]
                rate_limit_info = result["rate_limit_info"]
                st.session_state.request_counter = result["request_counter"]
                st.session_state.last_reset = result["last_reset"]
                st.session_state.rate_limit_until = result["rate_limit_until"]
                
                logger.info(
                    f"Fetch completed: platform={platform}, category={selected_cat}, cat_id={cat_id}, pages={max_pages}, "
                    f"items={len(items)}, success={len(items) > 0}, "
                    f"rate_limit_info={rate_limit_info if rate_limit_info else 'None'}"
                )
                
                expected_items = max_pages * 60
                if len(items) < expected_items * 0.5:
                    st.warning("Incomplete data fetched, possibly due to API rate limits. Try again later or reduce pages.")
                
                if rate_limit_info:
                    st.markdown("#### Rate Limit Debug Info")
                    for info in rate_limit_info:
                        st.markdown(f"- {info}")
                
                metadata_list = [
                    {
                        "thread_id": item["thread_id"],
                        "title": item["title"],
                        "no_of_reply": item["no_of_reply"],
                        "last_reply_time": (
                            datetime.fromtimestamp(int(item.get("last_reply_time", 0)), tz=HONG_KONG_TZ)
                            .strftime("%Y-%m-%d %H:%M:%S")
                            if item.get("last_reply_time")
                            else "Unknown"
                        ),
                        "like_count": item.get("like_count", 0),
                        "dislike_count": item.get("dislike_count", 0),
                    }
                    for item in items
                ]
                
                filtered_items = [item for item in metadata_list if item["no_of_reply"] >= min_replies]
                
                st.markdown(f"### Fetch Results (Platform: {platform}, Category: {selected_cat})")
                st.markdown(f"- Total threads fetched: {len(metadata_list)}")
                st.markdown(f"- Threads with replies ≥ {min_replies}: {len(filtered_items)}")
                
                if filtered_items:
                    st.markdown("#### Qualifying Threads:")
                    for item in filtered_items:
                        st.markdown(f"- Thread ID: {item['thread_id']}, Title: {item['title']}, Replies: {item['no_of_reply']}, Last Reply: {item['last_reply_time']}")
                else:
                    st.markdown("No qualifying threads found.")
                    logger.warning("No qualifying threads, possibly due to insufficient data or strict filters")
        
        st.markdown("---")
        st.markdown("### Query Thread Reply Count")
        thread_id_input = st.text_input("Enter Thread ID", placeholder="e.g., 7929703")
        if st.button("Query Reply Count"):
            if thread_id_input:
                try:
                    thread_id = int(thread_id_input)
                    with st.spinner(f"Querying thread {thread_id} info..."):
                        logger.info(f"Querying thread info: platform={platform}, thread_id={thread_id}")
                        thread_data = await search_thread_by_id(thread_id, platform)
                        
                        if thread_data:
                            thread_title = thread_data.get("title", "Unknown title")
                            reply_count = thread_data.get("no_of_reply", 0)
                            last_reply_time = (
                                datetime.fromtimestamp(int(thread_data.get("last_reply_time", 0)), tz=HONG_KONG_TZ)
                                .strftime("%Y-%m-%d %H:%M:%S")
                                if thread_data.get("last_reply_time")
                                else "Unknown"
                            )
                            
                            st.markdown("#### Query Results")
                            st.markdown(f"- Thread ID: {thread_id}")
                            st.markdown(f"- Title: {thread_title}")
                            st.markdown(f"- Reply Count: {reply_count}")
                            st.markdown(f"- Last Reply Time: {last_reply_time}")
                        else:
                            st.markdown(f"No info found for thread ID {thread_id}.")
                            logger.warning(f"Query failed: thread_id={thread_id}")
                except ValueError:
                    st.error("Please enter a valid thread ID (numeric).")
                    logger.error(f"Invalid thread ID: {thread_id_input}")
        
        st.markdown("---")
        st.markdown("### Test Grok 3 API")
        test_prompt = st.text_area("Enter test prompt", value="Explain quantum computing in simple terms", height=100)
        high_reasoning = st.checkbox("Enable high reasoning mode", value=False)
        if st.button("Test Grok 3 API"):
            with api_test_lock:
                if time.time() - st.session_state.last_api_test_time < 10:
                    st.warning("Please wait before submitting another API test.")
                    logger.warning("Duplicate API test request blocked")
                    return
                st.session_state.last_api_test_time = time.time()
            
            with st.spinner("Testing Grok 3 API..."):
                result = await test_grok3_api(test_prompt, high_reasoning)
                st.markdown("#### API Test Results")
                if "error" in result:
                    st.error(f"API Test Failed: {result['error']}")
                    logger.error(f"Grok 3 API test failed: error={result['error']}")
                else:
                    st.markdown(f"**Response:** {result['content']}")
                    st.markdown("**Request Details:**")
                    st.json(result['request'])
                    logger.info(f"Grok 3 API test succeeded: prompt={test_prompt[:100]}..., response_length={len(result['content'])}")
