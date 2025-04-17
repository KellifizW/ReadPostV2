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
MAX_PROMPT_LENGTH = 30000  # 假設 Grok 3 API 輸入限制為 30,000 字元
THREAD_ID_CACHE_DURATION = 300  # 帖子 ID 緩存有效期：5 分鐘（300 秒）

def clean_expired_cache(platform):
    """清理過期緩存，包括查詢緩存和帖子 ID 緩存"""
    cache_duration = LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
    current_time = time.time()
    # 清理查詢緩存
    for cache_key in list(st.session_state.thread_content_cache.keys()):
        if current_time - st.session_state.thread_content_cache[cache_key]["timestamp"] > cache_duration:
            del st.session_state.thread_content_cache[cache_key]
    # 清理帖子 ID 緩存
    for thread_id in list(st.session_state.thread_id_cache.keys()):
        if current_time - st.session_state.thread_id_cache[thread_id]["timestamp"] > THREAD_ID_CACHE_DURATION:
            del st.session_state.thread_id_cache[thread_id]

def clean_reply_text(text):
    """清理回覆中的 HTML 標籤並檢查長度，忽略 ≤ 5 個字的回覆"""
    # 清理 HTML 和表情符號
    text = re.sub(r'<img[^>]+alt="\[([^\]]+)\]"[^>]*>', r'[\1]', text)
    text = clean_html(text)
    text = ' '.join(text.split())
    # 檢查長度（中文字符計為 1 個字）
    if len(text) <= 5:
        return None  # 回覆太短，忽略
    return text

async def analyze_user_question(question, platform):
    """使用 Grok 3 分析用戶問題，決定需要抓取的數據類型和數量"""
    prompt = f"""
你是一個智能助手，分析用戶問題以決定從討論區（{platform}）抓取哪些元數據。
用戶問題："{question}"

請回答以下問題：
1. 問題的主要意圖是什麼？（例如：尋找特定話題、分享熱門帖子、推薦今日必睇帖子等）
2. 需要抓取哪些類型的數據？（例如：帖子標題、回覆數量、最後回覆時間、帖子點讚數、帖子負評數、回覆內容）
3. 建議抓取的帖子數量（1-5個，根據問題需求明確指定）？
4. 建議抓取的回覆數量或閱讀策略？（例如：「全部回覆」「最多100條」「最新50條」「根據內容相關性選擇」）
5. 有無關鍵字或條件用於篩選帖子？（例如：包含特定詞語、按回覆數排序、今日發佈）

回應格式：
- 意圖: [描述]
- 數據類型: [列表]
- 帖子數量: [數字]
- 回覆策略: [描述，例如「全部回覆」或「最新50條」]
- 篩選條件: [描述或"無"]
"""
    logger.info(f"Analyzing user question: question={question}, platform={platform}")
    api_result = await call_grok3_api(prompt)
    
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
    
    # 解析回應
    intent = "unknown"
    data_types = ["title", "no_of_reply", "replies"]
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
    
    # 改進意圖解析
    if "分享" in question.lower() and "個" in question:
        match = re.search(r'(\d+)個', question)
        if match:
            num_threads = min(max(int(match.group(1)), 1), 5)
            intent = f"分享{num_threads}個指定帖子"
    if "今日" in question.lower() or "今日必睇" in question.lower():
        intent = "分享今日必睇帖子"
        filter_condition = "優先選擇今日發佈或最後回覆時間在今日的帖子，若不足則選擇回覆數最多的帖子"
        data_types.extend(["last_reply_time", "like_count", "dislike_count"])
    if "最多人睇" in question.lower():
        intent = "分享最多人觀看的帖子"
        filter_condition = "按回覆數量或點讚數排序，選擇最熱門的帖子"
        data_types.extend(["like_count", "dislike_count"])
    
    return {
        "intent": intent,
        "data_types": data_types,
        "num_threads": num_threads,
        "reply_strategy": reply_strategy,
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
    
    # 初始化帖子 ID 緩存
    if "thread_id_cache" not in st.session_state:
        st.session_state.thread_id_cache = {}
    
    clean_expired_cache(platform)
    
    # 分析用戶問題
    analysis = await analyze_user_question(question, platform)
    logger.info(f"Question analysis: intent={analysis['intent']}, num_threads={analysis['num_threads']}, reply_strategy={analysis['reply_strategy']}")
    
    cat_id = cat_id_map[selected_cat]
    max_pages = max(5, analysis["num_threads"])  # 確保抓取足夠的帖子
    logger.info(f"Fetching threads with reply_strategy={analysis['reply_strategy']}")
    
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
        with processing_lock:
            active_requests[request_key]["result"] = result
            return result
    
    # 根據篩選條件選擇帖子
    selected_items = []
    filter_condition = analysis["filter_condition"].lower()
    today = datetime.now().date()
    
    if filter_condition != "none":
        # 檢查今日帖子（若要求今日相關）
        today_items = []
        other_items = []
        for item in items:
            last_reply_time = item.get("last_reply_time", 0)
            if last_reply_time:
                try:
                    reply_date = datetime.fromtimestamp(last_reply_time).date()
                    if reply_date == today:
                        today_items.append(item)
                    else:
                        other_items.append(item)
                except ValueError:
                    other_items.append(item)
            else:
                other_items.append(item)
        
        # 優先選擇今日帖子（若要求）
        if "今日" in filter_condition:
            selected_items = sorted(
                today_items,
                key=lambda x: x.get("no_of_reply", 0) + x.get("like_count", 0) - x.get("dislike_count", 0),
                reverse=True
            )
        
        # 若今日帖子不足，補充其他熱門帖子
        if len(selected_items) < analysis["num_threads"]:
            selected_items.extend(
                sorted(
                    other_items,
                    key=lambda x: x.get("no_of_reply", 0) + x.get("like_count", 0) - x.get("dislike_count", 0),
                    reverse=True
                )
            )
        
        selected_items = selected_items[:analysis["num_threads"]]
    
    # 若無符合條件的帖子，選擇回覆數最多的帖子
    if not selected_items:
        selected_items = sorted(
            [item for item in items if item.get("no_of_reply", 0) > 0],
            key=lambda x: x.get("no_of_reply", 0) + x.get("like_count", 0) - x.get("dislike_count", 0),
            reverse=True
        )[:analysis["num_threads"]]
    
    if not selected_items:
        selected_items = items[:analysis["num_threads"]]
    
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
        
        # 檢查帖子 ID 緩存
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
            # 根據回覆策略設置 max_replies
            thread_max_replies = no_of_reply
            if "最新" in analysis["reply_strategy"].lower():
                match = re.search(r'最新(\d+)條', analysis["reply_strategy"])
                if match:
                    thread_max_replies = min(no_of_reply, int(match.group(1)))
            
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
            
            # 篩選回覆（忽略 ≤ 5 個字的回覆）
            valid_replies = []
            for reply in replies:
                cleaned_text = clean_reply_text(reply["msg"])
                if cleaned_text:
                    valid_replies.append({"content": cleaned_text})
            
            # 保存到帖子 ID 緩存
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
    
    # 計算動態回應字數
    prompt_length = 0  # 將在生成提示後計算
    if prompt_length <= 1000:
        share_text_limit = 150
        reason_limit = 50
    elif prompt_length >= 20000:
        share_text_limit = 1500
        reason_limit = 500
    else:
        # 線性插值
        share_text_limit = int(150 + (prompt_length - 1000) * (1500 - 150) / (20000 - 1000))
        reason_limit = int(50 + (prompt_length - 1000) * (500 - 50) / (20000 - 1000))
    
    # 生成提示，動態截斷回覆以適應輸入限制
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
- 最後回覆時間：{datetime.fromtimestamp(thread['last_reply_time']).strftime('%Y-%m-%d %H:%M:%S') if thread['last_reply_time'] else '未知'}
- 點讚數：{thread['like_count']}
- 負評數：{thread['dislike_count']}
- 回覆（共 {len(thread['replies'])} 條，篩選後忽略 ≤ 5 個字的回覆）：
"""
        # 動態截斷回覆
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
    
    # 更新 prompt_length
    prompt_length = len(prompt)
    
    # 重新計算回應字數（基於實際 prompt_length）
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
1. 生成一段簡潔的分享文字，嚴格限制在 {share_text_limit} 字以內，分享 {analysis['num_threads']} 個帖子，突出每個帖子的吸引之處，包含標題、回覆數量和首條回覆的片段（若有）。可根據需要使用帖子 ID、最後回覆時間、點讚數或負評數來增強分享內容。
2. 提供一段簡短的選擇理由（{reason_limit} 字以內），解釋為何選擇這些帖子（例如話題性、幽默性、爭議性、回覆數量多等）。
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
    
    # 調用 Grok 3 生成回應
    start_api_time = time.time()
    api_result = await call_grok3_api(prompt)
    api_elapsed = time.time() - start_api_time
    logger.info(f"Grok 3 API call completed: elapsed={api_elapsed:.2f}s")
    
    if api_result.get("status") == "error":
        logger.warning(f"Falling back to local response due to Grok 3 API failure: {api_result['content']}")
        response = f"""
熱門帖子來自 {platform}！問題意圖：{analysis['intent']}。
"""
        for thread in threads_data[:analysis["num_threads"]]:
            first_reply = thread["replies"][0]["content"][:50] if thread["replies"] else "無回覆"
            response += f"""
- "{thread['title'][:50]}"（{thread['no_of_reply']} 條回覆）引發討論。首條回覆："{first_reply}"。
"""
        response = response[:share_text_limit]
        response += f"\n選擇理由：選擇這些帖子因其回覆數量多，話題具吸引力。"
    else:
        # 改進回應提取邏輯
        content = api_result["content"].strip()
        content = re.sub(r'\{\{ output \}\}', '', content).strip()
        content = re.sub(r'^\s*選擇理由\s*[:：]?\s*', '', content, flags=re.MULTILINE).strip()
        
        share_text = "無分享文字"
        reason = "無選擇理由"
        
        share_match = re.search(r'分享文字：\s*(.*?)(?=\n選擇理由：|$)', content, re.DOTALL)
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
    
    logger.info(f"Processed question: question={question}, platform={platform}, response_length={len(response)}")
    logger.info(f"Selected {len(selected_items)} threads, total replies fetched: {sum(len(thread['replies']) for thread in threads_data)}")
    
    return result
