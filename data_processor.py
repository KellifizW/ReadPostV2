import asyncio
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html
import streamlit as st
import streamlit.logger
import time
import random
from datetime import datetime
import pytz
from config import LIHKG_API, HKGOLDEN_API
from grok3_client import call_grok3_api
import re
from threading import Lock

logger = streamlit.logger.get_logger(__name__)
processing_lock = Lock()
active_requests = {}
MAX_PROMPT_LENGTH = 30000
THREAD_ID_CACHE_DURATION = 300
HONG_KONG_TZ = pytz.timezone("Asia/Hong_Kong")

def clean_expired_cache(platform):
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]
    for thread_id in list(st.session_state.thread_id_cache.keys()):
        if current_time - st.session_state.thread_id_cache[thread_id]["timestamp"] > THREAD_ID_CACHE_DURATION:
            del st.session_state.thread_id_cache[thread_id]

def clean_reply_text(text):
    text = re.sub(r'<img[^>]+alt="\[([^\]]+)\]"[^>]*>', r'[\1]', text)
    text = clean_html(text)
    text = ' '.join(text.split())
    if len(text) <= 5:
        return None
    return text

async def analyze_user_question(question, platform):
    prompt = f"""
你是一個智能助手，分析用戶問題以決定從討論區（{platform}）抓取哪些元數據。
用戶問題："{question}"

請回答以下問題：
1. 問題的主要意圖是什麼？（例如：尋找特定話題、分享熱門帖子、推薦最新帖子、綜合回覆意見等）
2. 需要抓取哪些類型的數據？（例如：帖子標題、回覆數量、最後回覆時間、帖子點讚數、帖子負評數、回覆內容）
3. 建議抓取的帖子數量（1-5個，根據問題需求明確指定）？
4. 建議抓取的回覆數量或閱讀策略？（例如：「全部回覆」「最多100條」「最新50條」「根據內容相關性選擇」「總結回覆意見」）
5. 有無關鍵字或條件用於篩選帖子？（例如：包含特定詞語、按回覆數排序、按最後回覆時間排序）

回應格式：
- 意圖: [描述]
- 數據類型: [列表]
- 帖子數量: [數字]
- 回覆策略: [描述，例如「全部回覆」或「最新50條」]
- 篩選條件: [描述或"無"]
"""
    logger.info(f"Analyzing user question: question={question}, platform={platform}")
    api_result = await call_grok3_api(prompt, stream=False)
    
    if api_result.get("status") == "error":
        logger.error(f"Failed to analyze question: {api_result['content']}")
        return {
            "intent": "unknown",
            "data_types": ["title", "no_of_reply", "replies"],
            "num_threads": 1,
            "reply_strategy": "全部回覆",
            "filter_condition": "none"
        }
    
    content = api_result["content"].strip()
    logger.info(f"Analysis result: {content[:200]}...")
    
    intent = "unknown"
    data_types = ["title", "no_of_reply", "last_reply_time", "replies"]
    num_threads = 1
    reply_strategy = "全部回覆"
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
        elif line.startswith("回覆策略:"):
            reply_strategy = line.replace("回覆策略:", "").strip()
        elif line.startswith("篩選條件:"):
            filter_condition = line.replace("篩選條件:", "").strip()
    
    if "分享" in question.lower():
        if "幾個" in question.lower():
            num_threads = max(num_threads, 3)
            intent = "分享最新帖子"
        elif "個" in question:
            match = re.search(r'(\d+)個', question)
            if match:
                num_threads = min(max(int(match.group(1)), 1), 5)
                intent = f"分享{num_threads}個指定帖子"
    if "最新" in question.lower():
        intent = "分享最新帖子"
        filter_condition = "按最後回覆時間排序，選擇最新的帖子"
        data_types.extend(["last_reply_time", "like_count", "dislike_count"])
        reply_strategy = "總結最新50條回覆"
    
    return {
        "intent": intent,
        "data_types": data_types,
        "num_threads": num_threads,
        "reply_strategy": reply_strategy,
        "filter_condition": filter_condition
    }

async def process_user_question(question, platform, cat_id_map, selected_cat, return_prompt=False):
    request_key = f"{question}:{platform}:{selected_cat}:{'preview' if return_prompt else 'normal'}"
    current_time = time.time()
    
    with processing_lock:
        if request_key in active_requests and current_time - active_requests[request_key]["timestamp"] < 30:
            logger.warning(f"Skipping duplicate request: request_key={request_key}")
            return active_requests[request_key]["result"]
        
        active_requests[request_key] = {"timestamp": current_time, "result": None}
    
    logger.info(f"Starting to process question: request_key={request_key}, hk_time={datetime.now(HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    
    if "thread_id_cache" not in st.session_state:
        st.session_state.thread_id_cache = {}
    
    clean_expired_cache(platform)
    
    analysis = await analyze_user_question(question, platform)
    logger.info(f"Question analysis: intent={analysis['intent']}, num_threads={analysis['num_threads']}, reply_strategy={analysis['reply_strategy']}")
    
    cat_id = cat_id_map[selected_cat]
    max_pages = max(5, analysis["num_threads"])
    logger.info(f"Fetching threads with reply_strategy={analysis['reply_strategy']}")
    
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
        with processing_lock:
            active_requests[request_key]["result"] = result
            return result
    
    selected_items = []
    filter_condition = analysis["filter_condition"].lower()
    
    if filter_condition != "none":
        filtered_items = []
        for item in items:
            last_reply_time = item.get("last_reply_time", 0)
            no_of_reply = item.get("no_of_reply", 0)
            if last_reply_time and no_of_reply >= 10:
                filtered_items.append(item)
            else:
                logger.info(f"Filtered out thread: id={item.get('id')}, last_reply_time={last_reply_time}, no_of_reply={no_of_reply}")
        
        if "最新" in filter_condition:
            selected_items = sorted(
                filtered_items,
                key=lambda x: x.get("last_reply_time", 0),
                reverse=True
            )
        
        selected_items = selected_items[:analysis["num_threads"]]
    
    if not selected_items:
        selected_items = sorted(
            [item for item in items if item.get("no_of_reply", 0) >= 10],
            key=lambda x: x.get("last_reply_time", 0),
            reverse=True
        )[:analysis["num_threads"]]
    
    if not selected_items:
        selected_items = items[:analysis["num_threads"]]
    
    logger.info(f"Selected {len(selected_items)} threads: {[item.get('id') for item in selected_items]}")
    
    processed_data = []
    threads_data = []
    
    for selected_item in selected_items:
        try:
            thread_id = selected_item["thread_id"] if platform == "LIHKG" else selected_item["id"]
        except KeyError as e:
            logger.error(f"Missing thread_id or id in selected_item: {selected_item}, error={str(e)}")
            continue
        
        thread_title = selected_item["title"]
        no_of_reply = selected_item.get("no_of_reply", 0)
        logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, no_of_reply={no_of_reply}")
        
        use_cache = thread_id in st.session_state.thread_id_cache and \
                    current_time - st.session_state.thread_id_cache[thread_id]["timestamp"] < THREAD_ID_CACHE_DURATION
        if use_cache:
            logger.info(f"Using thread ID cache: thread_id={thread_id}")
            thread_data = st.session_state.thread_id_cache[thread_id]["data"]
            replies = thread_data["replies"]
            thread_title = thread_data["title"]
            total_replies = thread_data["total_replies"]
            rate_limit_info.extend(thread_data.get("rate_limit_info", []))
        else:
            thread_max_replies = min(no_of_reply, 50) if "最新50條" in analysis["reply_strategy"] else no_of_reply
            logger.info(f"Fetching thread content: thread_id={thread_id}, platform={platform}, max_replies={thread_max_replies}")
            
            if platform == "LIHKG":
                thread_result = await get_lihkg_thread_content(
                    thread_id=thread_id,
                    cat_id=cat_id,
                    request_counter=st.session_state.request_counter,
                    last_reset=st.session_state.last_reset,
                    rate_limit_until=st.session_state.rate_limit_until,
                    max_replies=thread_max_replies
                )
            else:
                thread_result = await get_hkgolden_thread_content(
                    thread_id=thread_id,
                    cat_id=cat_id,
                    request_counter=st.session_state.request_counter,
                    last_reset=st.session_state.last_reset,
                    rate_limit_until=st.session_state.rate_limit_until,
                    max_replies=thread_max_replies
                )
            
            replies = thread_result["replies"]
            thread_title = thread_result["title"] or thread_title
            total_replies = thread_result.get("total_replies", no_of_reply)
            rate_limit_info.extend(thread_result["rate_limit_info"])
            st.session_state.request_counter = thread_result["request_counter"]
            st.session_state.rate_limit_until = thread_result["rate_limit_until"]
            
            logger.info(f"Thread content fetched: thread_id={thread_id}, title={thread_title}, replies={len(replies)}")
            
            valid_replies = []
            for reply in replies:
                cleaned_text = clean_reply_text(reply["msg"])
                if cleaned_text:
                    valid_replies.append({"content": cleaned_text})
            
            thread_data = {
                "thread_id": thread_id,
                "title": thread_title,
                "no_of_reply": no_of_reply,
                "last_reply_time": selected_item.get("last_reply_time", 0),
                "like_count": selected_item.get("like_count", 0),
                "dislike_count": selected_item.get("dislike_count", 0),
                "total_replies": total_replies,
                "replies": valid_replies,
                "rate_limit_info": thread_result["rate_limit_info"],
                "timestamp": current_time
            }
            st.session_state.thread_id_cache[thread_id] = {
                "data": thread_data,
                "timestamp": current_time
            }
            replies = valid_replies
        
        threads_data.append({
            "thread_id": thread_id,
            "title": thread_title,
            "no_of_reply": no_of_reply,
            "last_reply_time": selected_item.get("last_reply_time", 0),
            "like_count": selected_item.get("like_count", 0),
            "dislike_count": selected_item.get("dislike_count", 0),
            "total_replies": total_replies,
            "replies": replies
        })
        
        processed_data.extend([
            {
                "thread_id": thread_id,
                "title": thread_title,
                "content": reply["content"]
            }
            for reply in replies
        ])
    
    prompt_length = 0
    if prompt_length <= 1000:
        share_text_limit = 150
        reason_limit = 50
    elif prompt_length >= 20000:
        share_text_limit = 1500
        reason_limit = 500
    else:
        share_text_limit = int(150 + (prompt_length - 1000) * (1500 - 150) / (20000 - 1000))
        reason_limit = int(50 + (prompt_length - 1000) * (500 - 50) / (20000 - 1000))
    
    prompt = f"""
你是一個智能助手，從 {platform}（{selected_cat} 分類）分享有趣的帖子。以下是用戶問題和分析結果，以及選定的帖子數據。

用戶問題："{question}"
分析結果：
- 意圖：{analysis['intent']}
- 數據類型：{', '.join(analysis['data_types'])}
- 帖子數量：{analysis['num_threads']}
- 回覆策略：{analysis['reply_strategy']}
- 篩選條件：{analysis['filter_condition']}

帖子數據：
"""
    for thread in threads_data:
        prompt += f"""
- 帖子 ID：{thread['thread_id']}
- 標題：{thread['title']}
- 回覆數量：{thread['no_of_reply']}
- 總回覆數量：{thread['total_replies']}
- 最後回覆時間：{datetime.fromtimestamp(thread['last_reply_time'], tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if thread['last_reply_time'] else '未知'}
- 點讚數：{thread['like_count']}
- 負評數：{thread['dislike_count']}
- 回覆（共 {len(thread['replies'])} 條，篩選後忽略 ≤ 5 個字的回覆）：
"""
        max_replies = len(thread["replies"])
        reply_count = 0
        for reply in thread["replies"]:
            content = reply["content"][:100] + '...' if len(reply["content"]) > 100 else reply["content"]
            reply_line = f"  - {content}\n"
            if len(prompt) + len(reply_line) > MAX_PROMPT_LENGTH:
                break
            prompt += reply_line
            reply_count += 1
        if reply_count < max_replies:
            prompt += f"  - [因長度限制，僅顯示前 {reply_count} 條回覆，篩選後總共 {max_replies} 條]\n"
    
    prompt_length = len(prompt)
    
    if prompt_length <= 1000:
        share_text_limit = 150
        reason_limit = 50
    elif prompt_length >= 20000:
        share_text_limit = 1500
        reason_limit = 500
    else:
        share_text_limit = int(150 + (prompt_length - 1000) * (1500 - 150) / (20000 - 1000))
        reason_limit = int(50 + (prompt_length - 1000) * (500 - 50) / (20000 - 1000))
    
    prompt += f"""
請完成以下任務：
1. 生成一段簡潔的分享文字，嚴格限制在 {share_text_limit} 字以內，分享 {analysis['num_threads']} 個帖子，突出每個帖子的吸引之處，包含標題、回覆數量、最後回覆時間、點讚數和負評數。綜合不同回覆的意見，總結回覆中的主要觀點、情緒或熱門話題（若有回覆），而非僅引用單一回覆。
2. 提供一段簡短的選擇理由（{reason_limit} 字以內），解釋為何選擇這些帖子（例如話題性、最新性、回覆數量多等）。
3. 若回覆數量過多，根據回覆策略（{analysis['reply_strategy']}）優先總結最新或最相關的回覆內容。

回應格式：
{{ output }}
分享文字：[分享文字]
選擇理由：[選擇理由]
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
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    start_api_time = time.time()
    api_result = await call_grok3_api(prompt, stream=True)
    api_elapsed = time.time() - start_api_time
    logger.info(f"Grok 3 API call completed: elapsed={api_elapsed:.2f}s")
    
    if api_result.get("status") == "error":
        logger.warning(f"Falling back to local response due to Grok 3 API failure: {api_result['content']}")
        response = f"""
熱門帖子來自 {platform}！問題意圖：{analysis['intent']}。
"""
        for thread in threads_data[:analysis["num_threads"]]:
            summary = "無回覆可總結" if not thread["replies"] else "網友討論熱烈，觀點多元化。"
            response += f"""
- "{thread['title'][:50]}"（{thread['no_of_reply']} 條回覆，更新至 {datetime.fromtimestamp(thread['last_reply_time'], tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if thread['last_reply_time'] else '未知'}）：{summary}
"""
        response = response[:share_text_limit]
        response += f"\n選擇理由：選擇這些帖子因其最新性和回覆數量多。"
    else:
        content = ""
        async for chunk in api_result["content"]:
            if chunk:
                content += chunk
                logger.debug(f"Received chunk: {chunk[:50]}...")
        
        logger.info(f"Raw API response: {content[:200]}...")
        
        content = re.sub(r'\{\{ output \}\}|\{ output \}', '', content).strip()
        
        share_text = "無分享文字"
        reason = "無選擇理由"
        
        share_match = re.search(r'分享文字：\s*(.*?)(?=\n選擇理由：|\Z)', content, re.DOTALL)
        reason_match = re.search(r'選擇理由：\s*(.*)', content, re.DOTALL)
        
        if share_match:
            share_text = share_match.group(1).strip()[:share_text_limit]
        if reason_match:
            reason = reason_match.group(1).strip()[:reason_limit]
        
        response = f"分享文字：{share_text}\n選擇理由：{reason}"
        logger.info(f"Extracted response: share_text={share_text[:100]}..., reason={reason[:100]}...")
    
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
    
    logger.info(f"Processed question: question={question}, platform={platform}, response_length={len(response)}, hk_time={datetime.now(HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Selected {len(selected_items)} threads, total replies fetched: {sum(len(thread['replies']) for thread in threads_data)}")
    
    return result
