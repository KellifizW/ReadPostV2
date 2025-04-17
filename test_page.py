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

logger = streamlit.logger.get_logger(__name__)
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
                if not data.get("result", True) and not data.get("success", True):
                    logger.error(f"Search thread API returned failure: platform={platform}, thread_id={thread_id}, error={data.get('error_message', 'Unknown error')}")
                    return None
                thread_data = data.get("data", {}) if platform == "HKGOLDEN" else data["response"].get("items", [{}])[0]
                if not thread_data:
                    logger.warning(f"Search thread no results: platform={platform}, thread_id={thread_id}")
                    return None
                return {
                    "thread_id": thread_id,
                    "title": thread_data.get("title", "Unknown title"),
                   
