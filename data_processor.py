import asyncio
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html
import streamlit as st
import streamlit.logger
import time
import random
from datetime import datetime
from config import LIHKG_API, HKGOLDEN_API
from grok3_client import call_grok3_api
import re
from threading import Lock

logger = streamlit.logger.get_logger(__name__)
processing_lock = Lock()
active_requests = {}

def clean_expired_cache(platform):
    """清理過期緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]

def clean_reply_text(text):
    """清理回覆中的 HTML 標籤，保留純文字"""
    text = re.sub(r'<img[^>]+alt="\[([^\]]+)\]"[^>]*>', r'[\1]', text)
    text = clean_html(text)
    text = ' '.join(text.split())
    return text

async def analyze_user_question(question, platform):
    """使用Grok 3分析用戶問題，決定需要抓取的數據類型和數量"""
    prompt = f"""
你是一個智能助手，分析用戶問題以決定從討論區（{platform}）抓取哪些元數據。
用戶問題："{question}"

請回答以下問題：
1. 問題的主要意圖是什麼？（例如：尋找特定話題、分析熱門帖子、查詢回覆數量等）
2. 需要抓取哪些類型的數據？（例如：帖子標題、回覆內容、點讚數）
3. 建議抓取的帖子數量（1-5個）？
4. 每個帖子的回覆數量（1-50條）？
5. 有無關鍵字或條件用於篩選帖子？（例如：包含特定詞語、按點讚數排序）

回應格式：
- 意圖: [描述]
- 數據類型: [列表]
- 帖子數量: [數字]
- 回覆數量: [數字]
- 篩選條件: [描述或"無"]
"""
    logger.info(f"Analyzing user question: question={question}, platform={platform}")
    api_result = await call_grok3_api(prompt)
    
    if api_result.get("status") == "error":
        logger.error(f"Failed to analyze question: {api_result['content']}")
        return {
            "intent": "unknown",
            "data_types": ["title", "replies"],
            "num_threads": 1,
            "num_replies": 3,
            "filter_condition": "none"
        }
    
    content = api_result["content"].strip()
    logger.info(f"Analysis result: {content[:200]}...")
    
    # 解析回應
    intent = "unknown"
    data_types = ["title", "replies"]
    num_threads = 1
    num_replies = 3
    filter_condition = "none"
    
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("意圖:"):
            intent = line.replace("意圖:", "").strip()
        elif line.startswith("數據類型:"):
            data_types = [t.strip() for t in line.replace("數據類型:", "").strip().split(",")]
        elif line.startswith("帖子數量:"):
            try:
                num_threads = min(max(int(line.replace("帖子數量:", "").strip()), 1), 5)
            except ValueError:
                pass
        elif line.startswith("回覆數量:"):
            try:
                num_replies = min(max(int(line.replace("回覆數量:", "").strip()), 1), 50)
            except ValueError:
                pass
        elif line.startswith("篩選條件:"):
            filter_condition = line.replace("篩選條件:", "").strip()
    
    return {
        "intent": intent,
        "data_types": data_types,
        "num_threads": num_threads,
        "num_replies": num_replies,
        "filter_condition": filter_condition
    }

async def process_user_question(question, platform, cat_id_map, selected_cat, return_prompt=False):
    """處理用戶問題並返回相關帖子數據或原始 prompt"""
    request_key = f"{question}:{platform}:{selected_cat}:{'preview' if return_prompt else 'normal'}"
    current_time = time.time()
    
    with processing_lock:
        if request_key in active_requests and current_time - active_requests[request_key]["timestamp"] < 30:
            logger.warning(f"Skipping duplicate request: request_key={request_key}")
            return active_requests[request_key]["result"]
        
        active_requests[request_key] = {"timestamp": current_time, "result": None}
    
    logger.info(f"Starting to process question: request_key={request_key}")
    
    clean_expired_cache(platform)
    
    # 分析用戶問題
    analysis = await analyze_user_question(question, platform)
    logger.info(f"Question analysis: intent={analysis['intent']}, num_threads={analysis['num_threads']}, num_replies={analysis['num_replies']}")
    
    cat_id = cat_id_map[selected_cat]
    max_pages = max(1, analysis["num_threads"])
    max_replies = analysis["num_replies"]
    
    # 抓取帖子列表
    start_fetch_time = time.time()
    if platform == "LIHKG":
        result = await get_lihkg_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=max_pages,
            request_counter=st.session_state.get("request_counter", 0),
            last_reset=st.session_state.get("last_reset", time.time()),
            rate_limit_until=st.session_state.get("rate_limit_until", 0)
        )
    else:
        result = await get_hkgolden_topic_list(
            cat_id=cat_id,
            sub_cat_id=0,
            start_page=1,
            max_pages=max_pages,
            request_counter=st.session_state.get("request_counter", 0),
            last_reset=st.session_state.get("last_reset", time.time()),
            rate_limit_until=st.session_state.get("rate_limit_until", 0)
        )
    
    items = result["items"]
    rate_limit_info = result["rate_limit_info"]
    st.session_state.request_counter = result["request_counter"]
    st.session_state.last_reset = result["last_reset"]
    st.session_state.rate_limit_until = result["rate_limit_until"]
    
    logger.info(f"Fetch completed: platform={platform}, category={selected_cat}, total_items={len(items)}, elapsed={time.time() - start_fetch_time:.2f}s")
    
    if not items:
        logger.error("No items fetched from API")
        result = {
            "response": f"無法抓取帖子，API 返回空數據。請檢查分類（{selected_cat}）或稍後重試。",
            "rate_limit_info": rate_limit_info,
            "processed_data": [],
            "analysis": analysis
        }
        active_requests[request_key]["result"] = result
        return result
    
    # 根據篩選條件選擇帖子
    selected_items = []
    filter_condition = analysis["filter_condition"].lower()
    if filter_condition != "none":
        keywords = filter_condition.split()
        selected_items = [
            item for item in items
            if any(keyword.lower() in item["title"].lower() for keyword in keywords) and item.get("no_of_reply", 0) > 0
        ]
    if not selected_items:
        selected_items = [item for item in items if item.get("no_of_reply", 0) > 0]
    if not selected_items:
        selected_items = items
    
    # 隨機選擇指定數量的帖子
    num_threads = min(analysis["num_threads"], len(selected_items))
    selected_items = random.sample(selected_items, num_threads) if num_threads < len(selected_items) else selected_items
    
    processed_data = []
    threads_data = []
    
    # 抓取每個帖子的回覆
    for selected_item in selected_items:
        try:
            thread_id = selected_item["thread_id"] if platform == "LIHKG" else selected_item["id"]
        except KeyError as e:
            logger.error(f"Missing thread_id or id in selected_item: {selected_item}, error={str(e)}")
            continue
        
        thread_title = selected_item["title"]
        no_of_reply = selected_item.get("no_of_reply", 0)
        logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, no_of_reply={no_of_reply}")
        
        # 抓取帖子回覆
        logger.info(f"Fetching thread content: thread_id={thread_id}, platform={platform}")
        if platform == "LIHKG":
            thread_result = await get_lihkg_thread_content(
                thread_id=thread_id,
                cat_id=cat_id,
                request_counter=st.session_state.request_counter,
                last_reset=st.session_state.last_reset,
                rate_limit_until=st.session_state.rate_limit_until,
                max_replies=max_replies
            )
        else:
            thread_result = await get_hkgolden_thread_content(
                thread_id=thread_id,
                cat_id=cat_id,
                request_counter=st.session_state.request_counter,
                last_reset=st.session_state.last_reset,
                rate_limit_until=st.session_state.rate_limit_until,
                max_replies=max_replies
            )
        
        replies = thread_result["replies"]
        thread_title = thread_result["title"] or thread_title
        rate_limit_info.extend(thread_result["rate_limit_info"])
        st.session_state.request_counter = thread_result["request_counter"]
        st.session_state.rate_limit_until = thread_result["rate_limit_until"]
        
        logger.info(f"Thread content fetched: thread_id={thread_id}, title={thread_title}, replies={len(replies)}")
        
        thread_data = {
            "thread_id": thread_id,
            "title": thread_title,
            "replies": [
                {
                    "content": clean_reply_text(reply["msg"]),
                    "like_count": reply.get("like_count", 0),
                    "dislike_count": reply.get("dislike_count", 0)
                }
                for reply in replies
            ]
        }
        threads_data.append(thread_data)
        
        processed_data.extend([
            {
                "thread_id": thread_id,
                "title": thread_title,
                "content": clean_reply_text(reply["msg"]),
                "like_count": reply.get("like_count", 0),
                "dislike_count": reply.get("dislike_count", 0)
            }
            for reply in replies
        ])
    
    # 生成提示
    prompt = f"""
你是一個智能助手，從 {platform}（{selected_cat} 分類）分享有趣的帖子。以下是用戶問題和分析結果，以及選定的帖子數據。

用戶問題："{question}"
分析結果：
- 意圖：{analysis['intent']}
- 數據類型：{', '.join(analysis['data_types'])}
- 篩選條件：{analysis['filter_condition']}

帖子數據：
"""
    for thread in threads_data:
        prompt += f"""
- 帖子 ID：{thread['thread_id']}
- 標題：{thread['title']}
- 回覆（前 {len(thread['replies'])} 條）：
"""
        for reply in thread["replies"]:
            content = reply["content"][:100] + '...' if len(reply["content"]) > 100 else reply["content"]
            prompt += f"  - {content}（正評：{reply['like_count']}，負評：{reply['dislike_count']}）\n"
    
    prompt += f"""
根據用戶問題和帖子數據，生成一段簡潔的分享文字（必須嚴格限制在280字以內，僅包含 {{ output }} 部分的內容），突出帖子的吸引之處，並包含標題和首條回覆的片段（若有）。

範例：
{{ output }}
熱門 {platform} 帖子！"{threads_data[0]['title'][:50] if threads_data else '無標題'}" 引發熱議。首條回覆："{threads_data[0]['replies'][0]['content'][:50] if threads_data and threads_data[0]['replies'] else '無回覆'}"。快來看看！
{{ output }}
"""
    
    logger.info(f"Generated prompt (length={len(prompt)} chars)")
    
    if return_prompt:
        result = {
            "response": prompt,
            "rate_limit_info": rate_limit_info,
            "processed_data": processed_data,
            "analysis": analysis
        }
        active_requests[request_key]["result"] = result
        return result
    
    # 調用Grok 3生成回應
    start_api_time = time.time()
    api_result = await call_grok3_api(prompt)
    api_elapsed = time.time() - start_api_time
    logger.info(f"Grok 3 API call completed: elapsed={api_elapsed:.2f}s")
    
    if api_result.get("status") == "error":
        logger.warning(f"Falling back to local response due to Grok 3 API failure: {api_result['content']}")
        response = f"""
熱門帖子來自 {platform}！問題意圖：{analysis['intent']}。
"""
        for thread in threads_data[:1]:
            first_reply = thread["replies"][0]["content"][:50] if thread["replies"] else "無回覆"
            response += f"""
- "{thread['title'][:50]}" 引發討論。首條回覆："{first_reply}"。
詳細：
- ID：{thread['thread_id']}
- 標題：{thread['title']}
"""
            for i, reply in enumerate(thread["replies"][:3], 1):
                response += f"\n回覆 {i}：{reply['content'][:50]}...\n正評：{reply['like_count']}，負評：{reply['dislike_count']}\n"
        response = response[:280]
    else:
        # 提取 {{ output }} 部分並截斷
        content = api_result["content"].strip()
        match = re.search(r'\{\{ output \}\}(.*?)\{\{ output \}\}', content, re.DOTALL)
        response = match.group(1).strip()[:280] if match else content[:280]
    
    result = {
        "response": response,
        "rate_limit_info": rate_limit_info,
        "processed_data": processed_data,
        "analysis": analysis
    }
    
    with processing_lock:
        active_requests[request_key]["result"] = result
        if current_time - active_requests[request_key]["timestamp"] > 30:
            del active_requests[request_key]
    
    logger.info(f"Processed question: question={question}, platform={platform}, response_length={len(response)}")
    
    return result
