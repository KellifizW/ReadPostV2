import streamlit as st
import asyncio
import time
from datetime import datetime
import random
import pytz
import aiohttp
from lihkg_api import get_lihkg_topic_list
from hkgolden_api import get_hkgolden_topic_list
import streamlit.logger
from config import LIHKG_API, HKGOLDEN_API, GENERAL

logger = st.logger.get_logger(__name__)
HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])

async def search_thread_by_id(thread_id, platform):
    """使用搜尋 API 查詢帖子詳細信息"""
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        ]),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-HK,zh-Hant;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": f"{LIHKG_API['BASE_URL'] if platform == 'LIHKG' else HKGOLDEN_API['BASE_URL']}/",
    }
    
    url = (
        f"{LIHKG_API['BASE_URL']}/api_v2/thread/search?q={thread_id}&page=1&count=30&sort=score&type=thread"
        if platform == "LIHKG"
        else f"{HKGOLDEN_API['BASE_URL']}/v1/topics/search?q={thread_id}&page=1&count=30"
    )
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"搜尋帖子失敗: platform={platform}, thread_id={thread_id}, 狀態碼={response.status}")
                    return None
                data = await response.json()
                if not data.get("success", True):
                    logger.error(f"搜尋帖子 API 返回失敗: platform={platform}, thread_id={thread_id}, 錯誤={data.get('error_message', '未知錯誤')}")
                    return None
                items = data["response"].get("items", []) if platform == "LIHKG" else data.get("data", [])
                if not items:
                    logger.warning(f"搜尋帖子無結果: platform={platform}, thread_id={thread_id}")
                    return None
                return items[0]
        except Exception as e:
            logger.error(f"搜尋帖子錯誤: platform={platform}, thread_id={thread_id}, 錯誤={str(e)}")
            return None

async def test_page():
    st.title("討論區數據測試頁面")
    
    # 初始化速率限制狀態
    if "rate_limit_until" not in st.session_state:
        st.session_state.rate_limit_until = 0
    if "request_counter" not in st.session_state:
        st.session_state.request_counter = 0
    if "last_reset" not in st.session_state:
        st.session_state.last_reset = time.time()
    if "thread_content_cache" not in st.session_state:
        st.session_state.thread_content_cache = {}
    
    # 檢查是否處於速率限制中
    if time.time() < st.session_state.rate_limit_until:
        st.error(f"API 速率限制中，請在 {datetime.fromtimestamp(st.session_state.rate_limit_until)} 後重試。")
        return
    
    platform = st.selectbox("選擇討論區平台", ["LIHKG", "高登討論區"], index=0)
    if platform == "LIHKG":
        cat_id_map = LIHKG_API["CATEGORIES"]
        min_replies = LIHKG_API["MIN_REPLIES"]
    else:
        cat_id_map = HKGOLDEN_API["CATEGORIES"]
        min_replies = HKGOLDEN_API["MIN_REPLIES"]
    
    col1, col2 = st.columns([3, 1])
    with col2:
        selected_cat = st.selectbox(
            "選擇分類",
            options=list(cat_id_map.keys()),
            index=0
        )
        cat_id = cat_id_map[selected_cat]
        max_pages = st.slider("抓取頁數", 1, 5, LIHKG_API["MAX_PAGES"] if platform == "LIHKG" else HKGOLDEN_API["MAX_PAGES"])
    
    with col1:
        st.markdown("#### 速率限制狀態")
        st.markdown(f"- 當前請求計數: {st.session_state.request_counter}")
        st.markdown(f"- 最後重置時間: {datetime.fromtimestamp(st.session_state.last_reset)}")
        st.markdown(
            f"- 速率限制解除時間: "
            f"{datetime.fromtimestamp(st.session_state.rate_limit_until) if st.session_state.rate_limit_until > time.time() else '無限制'}"
        )
        
        if st.button("抓取數據"):
            with st.spinner("正在抓取數據..."):
                logger.info(f"開始抓取數據: platform={platform}, 分類={selected_cat}, cat_id={cat_id}, 頁數={max_pages}")
                
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
                    f"抓取完成: platform={platform}, 分類={selected_cat}, cat_id={cat_id}, 頁數={max_pages}, "
                    f"帖子數={len(items)}, 成功={len(items) > 0}, "
                    f"速率限制信息={rate_limit_info if rate_limit_info else '無'}"
                )
                
                expected_items = max_pages * 60
                if len(items) < expected_items * 0.5:
                    st.warning("抓取數據不完整，可能是因為 API 速率限制。請稍後重試，或減少抓取頁數。")
                
                if rate_limit_info:
                    st.markdown("#### 速率限制調試信息")
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
                            else "未知"
                        ),
                        "like_count": item.get("like_count", 0),
                        "dislike_count": item.get("dislike_count", 0),
                    }
                    for item in items
                ]
                
                filtered_items = [item for item in metadata_list if item["no_of_reply"] >= min_replies]
                
                st.markdown(f"### 抓取結果（平台：{platform}，分類：{selected_cat}）")
                st.markdown(f"- 總共抓取 {len(metadata_list)} 篇帖子")
                st.markdown(f"- 回覆數 ≥ {min_replies} 的帖子數：{len(filtered_items)} 篇")
                
                if filtered_items:
                    st.markdown("#### 符合條件的帖子：")
                    for item in filtered_items:
                        st.markdown(f"- 帖子 ID: {item['thread_id']}，標題: {item['title']}，回覆數: {item['no_of_reply']}，最後回覆時間: {item['last_reply_time']}")
                else:
                    st.markdown("無符合條件的帖子。")
                    logger.warning("無符合條件的帖子，可能數據不足或篩選條件過嚴")
        
        st.markdown("---")
        st.markdown("### 查詢帖子回覆數")
        thread_id_input = st.text_input("輸入帖子 ID", placeholder="例如：3913444")
        if st.button("查詢回覆數"):
            if thread_id_input:
                try:
                    thread_id = int(thread_id_input)
                    with st.spinner(f"正在查詢帖子 {thread_id} 的信息..."):
                        logger.info(f"查詢帖子信息: platform={platform}, thread_id={thread_id}")
                        thread_data = await search_thread_by_id(thread_id, platform)
                        
                        if thread_data:
                            thread_title = thread_data.get("title", "未知標題")
                            reply_count = thread_data.get("no_of_reply", 0)
                            last_reply_time = (
                                datetime.fromtimestamp(int(thread_data.get("last_reply_time", 0)), tz=HONG_KONG_TZ)
                                .strftime("%Y-%m-%d %H:%M:%S")
                                if thread_data.get("last_reply_time")
                                else "未知"
                            )
                            
                            st.markdown("#### 查詢結果")
                            st.markdown(f"- 帖子 ID: {thread_id}")
                            st.markdown(f"- 標題: {thread_title}")
                            st.markdown(f"- 回覆數: {reply_count}")
                            st.markdown(f"- 最後回覆時間: {last_reply_time}")
                        else:
                            st.markdown(f"未找到帖子 ID {thread_id} 的信息。")
                            logger.warning(f"查詢失敗