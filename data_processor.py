import asyncio
import aiohttp
import re
import json
import time
import random
import uuid
import traceback
from datetime import datetime, timedelta
import pytz
import streamlit as st
import streamlit.logger
from threading import Lock
from config import LIHKG_API, HKGOLDEN_API, GENERAL
from grok3_client import call_grok3_api
from lihkg_api import get_lihkg_topic_list, get_lihkg_thread_content
from hkgolden_api import get_hkgolden_topic_list, get_hkgolden_thread_content
from utils import clean_html

logger = streamlit.logger.get_logger(__name__)
processing_lock = Lock()
active_requests = {}
MAX_PROMPT_LENGTH = GENERAL.get("MAX_PROMPT_LENGTH", 30000)
THREAD_ID_CACHE_DURATION = 300
HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])

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
    prompt = """
你是一個智能助手，分析用戶問題以決定從討論區（{platform}）抓取哪些元數據。
用戶問題："{question}"

請回答以下問題：
1. 問題的主要意圖是什麼？（例如：尋找特定話題、分享熱門帖子、推薦最新帖子、綜合回覆意見等）
2. 需要抓取哪些類型的數據？（例如：帖子標題、回覆數量、最後回覆時間、帖子點讚數、帖子負評數、回覆內容）
3. 建議抓取的帖子數量（1-10個，根據問題需求明確指定，預設1個）？
4. 建議抓取的回覆數量或閱讀策略？（例如：「全部回覆」「最多100條」「最新50條」「根據內容相關性選擇」「總結回覆意見」）
5. 有無關鍵字或條件用於篩選帖子？（例如：包含特定詞語、按回覆數排序、按最後回覆時間排序）

回應格式：
- 意圖: [描述]
- 數據類型: [列表]
- 帖子數量: [數字]
- 回覆策略: [描述，例如「全部回覆」或「最新50條」]
- 篩選條件: [描述或"無"]
""".format(platform=platform, question=question)
    logger.info(f"Analyzing user question: question={question}, platform={platform}")
    api_result = await call_grok3_api(prompt, stream=False)
    
    if api_result.get("status") == "error":
        logger.error(f"Failed to analyze question: {api_result['content']}, traceback={traceback.format_exc()}")
        return {
            "intent": "share_post",
            "data_types": ["title", "no_of_reply", "last_reply_time", "like_count", "dislike_count", "replies"],
            "num_threads": 1,
            "reply_strategy": "最新10條",
            "filter_condition": "按最後回覆時間排序，選擇近期活躍帖子"
        }
    
    content = api_result["content"].strip()
    logger.info(f"Analysis result: {content[:200]}...")
    
    intent = "share_post"
    data_types = ["title", "no_of_reply", "last_reply_time", "like_count", "dislike_count", "replies"]
    num_threads = 1  # 預設1個帖子
    reply_strategy = "最新10條"
    filter_condition = "按最後回覆時間排序，選擇近期活躍帖子"
    
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("意圖:"):
            intent = line.replace("意圖:", "").strip()
        elif line.startswith("數據類型:"):
            data_types = [t.strip() for t in line.replace("數據類型:", "").strip().split(",")]
        elif line.startswith("帖子數量:"):
            try:
                num_threads = min(max(int(line.replace("帖子數量:", "").strip()), 1), 10)
            except ValueError:
                pass
        elif line.startswith("回覆策略:"):
            reply_strategy = line.replace("回覆策略:", "").strip()
        elif line.startswith("篩選條件:"):
            filter_condition = line.replace("篩選條件:", "").strip()
    
    # 改進解析邏輯，確保「一個」返回1個帖子
    question_lower = question.strip().lower()
    if "測試" in question_lower:
        intent = "測試功能"
        num_threads = 1
        reply_strategy = "最新10條"
        filter_condition = "按最後回覆時間排序，選擇近期活躍帖子"
    elif re.search(r'分享.*(一個|一篇|一帖|1個|1篇|1帖)', question_lower):
        intent = "分享單個帖子"
        num_threads = 1
        reply_strategy = "最新10條"
        filter_condition = "按最後回覆時間排序，選擇近期活躍帖子"
    elif "分享" in question_lower or "排列" in question_lower:
        match = re.search(r'(\d+)[個个]', question_lower)
        if match:
            num_threads = min(max(int(match.group(1)), 1), 10)
            intent = "分享最新帖子" if "分享" in question_lower else "排列最新帖子"
        elif "幾個" in question_lower:
            num_threads = max(num_threads, 3)
            intent = "分享最新帖子"
    elif "最新" in question_lower or "時間" in question_lower:
        intent = "排列最新帖子"
        filter_condition = "按最後回覆時間排序，選擇最新的帖子"
        reply_strategy = "無需抓取回覆內容" if "排列" in question_lower else "最新50條"
    elif "on9" in question_lower:
        intent = "尋找on9帖子"
        data_types.extend(["like_count", "dislike_count"])
        num_threads = 5
        reply_strategy = "最新20條"
        filter_condition = "按回覆數量排序，標題或回覆包含‘on9’或搞笑、荒謬、惡搞、迷因、傻、無語、荒唐相關內容，優先今日帖子但允許最近三天"
    
    if "今日" in question_lower:
        filter_condition = f"{filter_condition}; 優先選擇今日發布的帖子，若無則放寬至最近七天"
    
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
    if "thread_content_cache" not in st.session_state:
        st.session_state.thread_content_cache = {}
    
    clean_expired_cache(platform)
    
    analysis = await analyze_user_question(question, platform)
    logger.info(f"Question analysis: intent={analysis['intent']}, num_threads={analysis['num_threads']}, reply_strategy={analysis['reply_strategy']}")
    
    try:
        cat_id = cat_id_map[selected_cat]
        if not isinstance(cat_id, (int, str)) or not cat_id:
            raise ValueError(f"Invalid cat_id: {cat_id}")
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid cat_id: cat_id_map={cat_id_map}, selected_cat={selected_cat}, error={str(e)}, traceback={traceback.format_exc()}")
        result = {
            "response": f"無效分類 ID：{selected_cat}（{cat_id_map.get(selected_cat, '未知')}）。請檢查 {platform} 分類配置。",
            "rate_limit_info": [],
            "processed_data": [],
            "analysis": analysis
        }
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    max_pages = max(HKGOLDEN_API["MAX_PAGES"] if platform == "高登討論區" else LIHKG_API["MAX_PAGES"], analysis["num_threads"] // 10 + 1)
    logger.info(f"Fetching threads with reply_strategy={analysis['reply_strategy']}, cat_id={cat_id}")
    
    start_fetch_time = time.time()
    try:
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
    except Exception as e:
        logger.error(f"Failed to fetch topics: platform={platform}, cat_id={cat_id}, error={str(e)}, traceback={traceback.format_exc()}")
        error_message = f"無法抓取帖子，API 錯誤：{str(e)}。"
        if platform == "高登討論區":
            error_message += f"\n可能原因：分類 ID {cat_id} 無效或高登討論區 API 配置錯誤，請檢查 config.py 的 HKGOLDEN_API['CATEGORIES']。"
        result = {
            "response": error_message,
            "rate_limit_info": [],
            "processed_data": [],
            "analysis": analysis
        }
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    items = result["items"]
    rate_limit_info = result["rate_limit_info"]
    st.session_state.request_counter = result["request_counter"]
    st.session_state.last_reset = result["last_reset"]
    st.session_state.rate_limit_until = result["rate_limit_until"]
    
    logger.info(f"Fetch completed: platform={platform}, category={selected_cat}, total_items={len(items)}, elapsed={time.time() - start_fetch_time:.2f}s")
    
    if not items:
        logger.error(f"No items fetched from API: platform={platform}, cat_id={cat_id}")
        error_message = f"無法抓取帖子，API 返回空數據。請檢查分類（{selected_cat}，ID={cat_id}）或稍後重試。"
        if platform == "高登討論區":
            error_message += f"\n可能原因：分類 ID {cat_id} 無效或高登討論區 API 配置錯誤，請檢查 config.py 的 HKGOLDEN_API['CATEGORIES']。"
        result = {
            "response": error_message,
            "rate_limit_info": rate_limit_info,
            "processed_data": [],
            "analysis": analysis
        }
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    selected_items = []
    filter_condition = analysis["filter_condition"].lower()
    today = datetime.now(HONG_KONG_TZ).date()
    seven_days_ago = today - timedelta(days=7)
    current_timestamp = int(time.time())
    one_year_ago = current_timestamp - 365 * 24 * 3600  # 2024 年
    
    filtered_items = []
    for item in items:
        create_time = item.get("create_time", 0)
        last_reply_time = item.get("last_reply_time", 0)
        no_of_reply = item.get("no_of_reply", 0)
        title = item.get("title", "").lower()
        thread_id = item.get("id", item.get("thread_id", ""))
        
        try:
            parsed_time = datetime.fromtimestamp(last_reply_time, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')
            logger.debug(f"Thread ID={thread_id}, title={title}, last_reply_time={last_reply_time}, parsed_time={parsed_time}")
        except (ValueError, OSError) as e:
            logger.warning(f"Invalid timestamp for thread ID={thread_id}: last_reply_time={last_reply_time}, error={str(e)}")
            continue
        
        if last_reply_time < one_year_ago:
            logger.warning(f"Thread ID={thread_id} filtered out: last_reply_time={parsed_time} is too old (before 2024)")
            continue
        if no_of_reply == 0:
            logger.warning(f"Thread ID={thread_id} filtered out: no_of_reply=0")
            continue
        if "優先選擇今日發布的帖子" in filter_condition:
            create_date = datetime.fromtimestamp(create_time, tz=HONG_KONG_TZ).date() if create_time else today
            if create_date < seven_days_ago:
                logger.debug(f"Thread ID={thread_id} filtered out: create_date={create_date} is older than 7 days")
                continue
        if "on9" in filter_condition and not ("on9" in title or any(kw in title for kw in ["搞笑", "荒謬", "無語", "惡搞", "迷因", "傻", "荒唐"])):
            logger.debug(f"Thread ID={thread_id} filtered out: title does not match on9 keywords")
            continue
        
        filtered_items.append(item)
    
    logger.info(f"Filtered {len(filtered_items)} threads after applying conditions")
    
    if "按回覆數量排序" in filter_condition:
        selected_items = sorted(
            filtered_items,
            key=lambda x: (x.get("no_of_reply", 0), x.get("last_reply_time", 0)),
            reverse=True
        )
    else:
        selected_items = sorted(
            filtered_items,
            key=lambda x: x.get("last_reply_time", 0),
            reverse=True
        )
    
    selected_items = selected_items[:analysis["num_threads"]]
    
    if not selected_items:
        logger.error("No threads match filter conditions")
        error_message = f"無符合條件的帖子（分類：{selected_cat}，ID={cat_id}）。"
        error_message += "\n可能原因："
        error_message += "\n- 所有帖子無回覆（no_of_reply=0）"
        error_message += "\n- 帖子時間過舊（早於 2024 年或超過 7 天）"
        error_message += "\n- 標題不匹配關鍵字（若要求 on9 相關內容）"
        error_message += f"\n請檢查 {platform} API 數據或放寬篩選條件（例如移除‘今日’限制）。"
        result = {
            "response": error_message,
            "rate_limit_info": rate_limit_info,
            "processed_data": [],
            "analysis": analysis
        }
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    logger.info(f"Selected {len(selected_items)} threads: {[item.get('id') for item in selected_items]}")
    
    processed_data = []
    threads_data = []
    valid_threads = 0
    
    for selected_item in selected_items:
        try:
            thread_id = selected_item["thread_id"] if platform == "LIHKG" else selected_item["id"]
        except KeyError as e:
            logger.error(f"Missing thread_id or id in selected_item: {selected_item}, error={str(e)}, traceback={traceback.format_exc()}")
            continue
        
        thread_title = selected_item["title"]
        no_of_reply = selected_item.get("no_of_reply", 0)
        last_reply_time = selected_item.get("last_reply_time", 0)
        logger.info(f"Selected thread: thread_id={thread_id}, title={thread_title}, no_of_reply={no_of_reply}, last_reply_time={datetime.fromtimestamp(last_reply_time, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if last_reply_time else '未知'}")
        
        replies = []
        total_replies = no_of_reply
        if analysis["reply_strategy"] != "無需抓取回覆內容":
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
                thread_max_replies = min(no_of_reply, 10) if "最新10條" in analysis["reply_strategy"] else \
                                    min(no_of_reply, 50) if "最新50條" in analysis["reply_strategy"] else no_of_reply
                logger.info(f"Fetching thread content: thread_id={thread_id}, platform={platform}, max_replies={thread_max_replies}")
                
                try:
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
                except Exception as e:
                    logger.error(f"Failed to fetch thread content: thread_id={thread_id}, error={str(e)}, traceback={traceback.format_exc()}")
                    rate_limit_info.append(f"Thread fetch failed: thread_id={thread_id}, error={str(e)}")
                    continue
                
                replies = thread_result["replies"]
                thread_title = thread_result["title"] or thread_title
                total_replies = thread_result.get("total_replies", no_of_reply)
                rate_limit_info.extend(thread_result["rate_limit_info"])
                st.session_state.request_counter = thread_result["request_counter"]
                st.session_state.last_reset = thread_result["last_reset"]
                st.session_state.rate_limit_until = thread_result["rate_limit_until"]
                
                logger.info(f"Thread content fetched: thread_id={thread_id}, title={thread_title}, replies={len(replies)}")
                
                valid_replies = []
                for reply in replies:
                    cleaned_text = clean_reply_text(reply["msg"])
                    if cleaned_text:
                        if len(valid_replies) < 5 or any(kw in cleaned_text.lower() for kw in ["on9", "搞笑", "荒謬", "無語", "惡搞", "迷因", "傻", "荒唐"]):
                            valid_replies.append({"content": cleaned_text})
                
                if not valid_replies:
                    logger.warning(f"No valid replies for thread_id={thread_id}, skipping thread")
                    rate_limit_info.append(f"No valid replies for thread_id={thread_id}")
                    continue
                
                thread_data = {
                    "thread_id": thread_id,
                    "title": thread_title,
                    "no_of_reply": no_of_reply,
                    "last_reply_time": last_reply_time,
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
        
        valid_threads += 1
        threads_data.append({
            "thread_id": thread_id,
            "title": thread_title,
            "no_of_reply": no_of_reply,
            "last_reply_time": last_reply_time,
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
    
    if valid_threads == 0:
        logger.error("No valid threads with replies found")
        error_message = f"無法找到有效帖子（分類：{selected_cat}，ID={cat_id}）。"
        error_message += "\n可能原因："
        error_message += "\n- 帖子內容端點無效（HTTP 404）"
        error_message += "\n- 所有帖子無有效回覆"
        error_message += "\n- API 數據過舊或配置錯誤"
        error_message += f"\n請檢查 {platform} API 端點（例如 /thread/{{thread_id}}）或聯繫高登討論區管理員。"
        result = {
            "response": error_message,
            "rate_limit_info": rate_limit_info,
            "processed_data": [],
            "analysis": analysis
        }
        with processing_lock:
            active_requests[request_key]["result"] = result
        return result
    
    prompt_length = 0
    share_text_limit = 1500
    reason_limit = 500
    
    prompt = []
    prompt.append(f"你是一個智能助手，從 {platform}（{selected_cat} 分類）分享或排列有趣的帖子。以下是用戶問題和分析結果，以及選定的帖子數據。")
    prompt.append(f"\n用戶問題：\"{question}\"")
    prompt.append("分析結果：")
    prompt.append(f"- 意圖：{analysis['intent']}")
    prompt.append(f"- 數據類型：{', '.join(analysis['data_types'])}")
    prompt.append(f"- 帖子數量：{min(analysis['num_threads'], valid_threads)}")
    prompt.append(f"- 回覆策略：{analysis['reply_strategy']}")
    prompt.append(f"- 篩選條件：{analysis['filter_condition']}")
    prompt.append("\n帖子數據：")
    
    for thread in threads_data:
        last_reply_str = datetime.fromtimestamp(thread['last_reply_time'], tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if thread['last_reply_time'] else '未知'
        thread_info = [
            f"- 帖子 ID：{thread['thread_id']}",
            f"- 標題：{thread['title']}",
            f"- 回覆數量：{thread['no_of_reply']}",
            f"- 總回覆數量：{thread['total_replies']}",
            f"- 最後回覆時間：{last_reply_str}",
            f"- 點讚數：{thread['like_count']}",
            f"- 負評數：{thread['dislike_count']}"
        ]
        prompt.extend(thread_info)
        
        if thread["replies"]:
            prompt.append(f"- 回覆（共 {len(thread['replies'])} 條，篩選後保留相關內容）：")
            max_replies = len(thread["replies"])
            reply_count = 0
            for reply in thread["replies"]:
                content = reply["content"][:100] + '...' if len(reply["content"]) > 100 else reply["content"]
                reply_line = f"  - {content}"
                if len(''.join(prompt)) + len(reply_line) > MAX_PROMPT_LENGTH:
                    break
                prompt.append(reply_line)
                reply_count += 1
            if reply_count < max_replies:
                prompt.append(f"  - [因長度限制，僅顯示前 {reply_count} 條回覆，總共 {max_replies} 條]")
        else:
            prompt.append("- 回覆：無（未找到符合條件的回覆或內容抓取失敗）")
    
    prompt.append("\n請完成以下任務：")
    prompt.append(f"1. 生成一段簡潔的分享或排列文字，嚴格限制在 {share_text_limit} 字以內，列出 {min(analysis['num_threads'], valid_threads)} 個帖子，按最後回覆時間降序排列，包含每個帖子的標題、回覆數量、最後回覆時間、點讚數和負評數。若有回覆內容，綜合不同回覆的意見，總結主要觀點、情緒或熱門話題（若有回覆），而非僅引用單一回覆。若無回覆內容，僅列出帖子元數據（標題、回覆數等），並註明「無可用回覆」。")
    prompt.append(f"2. 提供一段簡短的選擇理由（{reason_limit} 字以內），解釋為何選擇這些帖子（例如話題性、最新性、回覆數量多等）。")
    prompt.append(f"3. 若回覆數量過多，根據回覆策略（{analysis['reply_strategy']}）優先總結最新或最相關的回覆內容。")
    prompt.append("4. 嚴禁生成假數據或虛構內容，所有信息必須基於提供的帖子數據。")
    prompt.append("\n回應格式：")
    prompt.append("{{ output }}")
    prompt.append("分享文字：[分享文字]")
    prompt.append("選擇理由：[選擇理由]")
    prompt.append("{{ output }}")
    
    prompt = "\n".join(prompt)
    prompt_length = len(prompt)
    logger.info(f"Generated prompt (length={prompt_length} chars)")
    
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
    
    async def stream_response(max_retries=3):
        attempt = 0
        while attempt < max_retries:
            try:
                logger.debug(f"Streaming response attempt {attempt + 1}/{max_retries}, request_key={request_key}")
                api_result = await call_grok3_api(prompt, stream=True)
                if api_result.get("status") == "error":
                    logger.warning(f"Stream failed: request_key={request_key}, error={api_result['content']}")
                    raise aiohttp.ClientConnectionError(f"API error: {api_result['content']}")
                
                share_text = []
                reason = []
                current_section = None
                
                async for chunk in api_result["content"]:
                    if chunk:
                        logger.debug(f"Received chunk: {chunk[:50]}...")
                        chunk = re.sub(r'\{\{ output \}\}|\{ output \}', '', chunk).strip()
                        if not chunk:
                            continue
                        
                        if "分享文字：" in chunk:
                            current_section = "share"
                            share_text.append(chunk.split("分享文字：")[-1].strip())
                        elif "選擇理由：" in chunk:
                            current_section = "reason"
                            reason.append(chunk.split("選擇理由：")[-1].strip())
                        elif current_section == "share":
                            share_text.append(chunk)
                        elif current_section == "reason":
                            reason.append(chunk)
                        
                        if share_text and current_section == "share":
                            yield f"**分享文字**：{' '.join(share_text)[:share_text_limit]}\n"
                        if reason and current_section == "reason":
                            yield f"**選擇理由**：{' '.join(reason)[:reason_limit]}\n"
                
                if share_text and not reason:
                    yield f"**分享文字**：{' '.join(share_text)[:share_text_limit]}\n"
                if reason:
                    yield f"**選擇理由**：{' '.join(reason)[:reason_limit]}\n"
                
                logger.info(f"Stream response succeeded: request_key={request_key}")
                return
            
            except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
                attempt += 1
                logger.warning(f"Stream attempt {attempt}/{max_retries} failed: request_key={request_key}, error={str(e)}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.debug(f"Retrying after {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"All stream attempts failed: request_key={request_key}, falling back to non-stream")
                    try:
                        api_result_sync = await call_grok3_api(prompt, stream=False)
                        api_elapsed = time.time() - start_fetch_time
                        logger.info(f"Grok 3 API retry completed: elapsed={api_elapsed:.2f}s")
                        
                        if api_result_sync.get("status") == "error":
                            logger.error(f"Non-stream retry failed: request_key={request_key}, error={api_result_sync['content']}")
                            yield f"無法生成回應，API 連接失敗：{api_result_sync['content']}"
                            return
                        
                        content = api_result_sync["content"]
                        content = re.sub(r'\{\{ output \}\}|\{ output \}', '', content).strip()
                        
                        share_text = "無分享文字"
                        reason = "無選擇理由"
                        
                        share_match = re.search(r'分享文字：\s*(.*?)(?=\n選擇理由：|\Z)', content, re.DOTALL)
                        reason_match = re.search(r'選擇理由：\s*(.*)', content, re.DOTALL)
                        
                        if share_match:
                            share_text = share_match.group(1).strip()[:share_text_limit]
                            yield f"**分享文字**：{share_text}\n"
                        if reason_match:
                            reason = reason_match.group(1).strip()[:reason_limit]
                            yield f"**選擇理由**：{reason}\n"
                    except Exception as e:
                        logger.error(f"Non-stream fallback failed: request_key={request_key}, error={str(e)}")
                        yield f"無法生成回應，API 連接失敗：{str(e)}"
            except Exception as e:
                logger.error(f"Unexpected error in stream_response: request_key={request_key}, error={str(e)}")
                yield f"無法生成回應，意外錯誤：{str(e)}"
                return
    
    result = {
        "response": stream_response(),
        "rate_limit_info": rate_limit_info,
        "processed_data": processed_data,
        "analysis": analysis
    }
    
    with processing_lock:
        active_requests[request_key]["result"] = result
        if current_time - active_requests[request_key]["timestamp"] > 30:
            del active_requests[request_key]
    
    logger.info(f"Processed question: question={question}, platform={platform}, hk_time={datetime.now(HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Selected {len(selected_items)} threads, total replies fetched: {sum(len(thread['replies']) for thread in threads_data)}")
    
    return result
