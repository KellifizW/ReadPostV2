import streamlit as st
import asyncio
from datetime import datetime
import pytz
import re
from data_processor import process_user_question
import time
from config import LIHKG_API, HKGOLDEN_API, GENERAL
import streamlit.logger
import traceback

logger = streamlit.logger.get_logger(__name__)
HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])

async def chat_page():
    st.title("討論區聊天介面")
    
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "rate_limit_until" not in st.session_state:
        st.session_state.rate_limit_until = 0
    if "request_counter" not in st.session_state:
        st.session_state.request_counter = 0
    if "last_reset" not in st.session_state:
        st.session_state.last_reset = time.time()
    if "thread_content_cache" not in st.session_state:
        st.session_state.thread_content_cache = {}
    if "thread_id_cache" not in st.session_state:
        st.session_state.thread_id_cache = {}
    if "last_submit_key" not in st.session_state:
        st.session_state.last_submit_key = None
    if "last_submit_time" not in st.session_state:
        st.session_state.last_submit_time = 0
    if "processing_request" not in st.session_state:
        st.session_state.processing_request = False

    if time.time() < st.session_state.rate_limit_until:
        st.error(f"API 速率限制中，請在 {datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')} 後重試。")
        return
    
    platform = st.selectbox("選擇討論區平台", ["LIHKG", "高登討論區"], index=0)
    if platform == "LIHKG":
        categories = LIHKG_API["CATEGORIES"]
    else:
        categories = HKGOLDEN_API["CATEGORIES"]
    
    selected_cat = st.selectbox("選擇分類", options=list(categories.keys()), index=0)
    
    st.markdown("### 聊天記錄")
    for chat in st.session_state.chat_history:
        with st.chat_message("user"):
            st.markdown(f"**用戶**：{chat['question']}")
        with st.chat_message("assistant"):
            if chat.get("is_preview"):
                st.markdown("**提示預覽**：")
                st.code(chat['response'], language="text")
            else:
                response = chat['response']
                if isinstance(response, str):
                    response = response.strip()
                    response = re.sub(r'\{\{ output \}\}|\{ output \}', '', response).strip()
                    
                    share_text = "無分享文字"
                    reason = "無選擇理由"
                    
                    share_match = re.search(r'分享文字：\s*(.*?)(?=\n選擇理由：|\Z)', response, re.DOTALL)
                    reason_match = re.search(r'選擇理由：\s*(.*)', response, re.DOTALL)
                    
                    if share_match:
                        share_text = share_match.group(1).strip()
                        st.markdown(f"**分享文字**：{share_text}")
                    if reason_match:
                        reason = reason_match.group(1).strip()
                        st.markdown(f"**選擇理由**：{reason}")
                else:
                    st.write_stream(response)
            
            if chat.get("debug_info") or chat.get("analysis"):
                with st.expander("調試信息"):
                    if chat.get("analysis"):
                        analysis = chat["analysis"]
                        st.markdown("#### 問題分析：")
                        st.markdown(f"- 意圖：{analysis['intent']}")
                        st.markdown(f"- 數據類型：{', '.join(analysis['data_types'])}")
                        st.markdown(f"- 帖子數量：{analysis['num_threads']}")
                        st.markdown(f"- 回覆策略：{analysis['reply_strategy']}")
                        st.markdown(f"- 篩選條件：{analysis['filter_condition']}")
                    if chat.get("debug_info"):
                        for info in chat["debug_info"]:
                            st.markdown(info)
    
    user_input = st.chat_input("輸入你的問題或要求（例如：分享任何帖文）", key="chat_page_chat_input")
    preview_button = st.button("預覽", key="preview_button")

    if st.session_state.processing_request:
        st.warning("正在處理請求，請稍候。")
        return

    if preview_button and user_input:
        logger.info(f"Preview button clicked: question={user_input}, platform={platform}, category={selected_cat}")
        
        submit_key = f"preview:{user_input}:{platform}:{selected_cat}"
        current_time = time.time()
        if (st.session_state.last_submit_key == submit_key and 
            current_time - st.session_state.last_submit_time < 5):
            logger.warning("Skipping duplicate preview submission")
            st.warning("請勿重複提交相同預覽請求，請稍後再試。")
            return
        
        st.session_state.processing_request = True
        st.session_state.last_submit_key = submit_key
        st.session_state.last_submit_time = current_time

        with st.chat_message("user"):
            st.markdown(f"**用戶**：{user_input}")
        
        with st.chat_message("assistant"):
            placeholder = st.empty()
            debug_info = []
            
            cache_key = f"{platform}_{selected_cat}_{user_input}"
            use_cache = cache_key in st.session_state.thread_content_cache and \
                        time.time() - st.session_state.thread_content_cache[cache_key]["timestamp"] < \
                        (LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"])
            
            if use_cache:
                logger.info(f"Using cache for preview: platform={platform}, category={selected_cat}, question={user_input}")
                result = st.session_state.thread_content_cache[cache_key]["data"]
            else:
                try:
                    logger.info(f"Calling process_user_question for preview: question={user_input}, platform={platform}, category={selected_cat}")
                    result = await process_user_question(
                        user_input,
                        platform=platform,
                        cat_id_map=categories,
                        selected_cat=selected_cat,
                        return_prompt=True
                    )
                    st.session_state.thread_content_cache[cache_key] = {
                        "data": result,
                        "timestamp": time.time()
                    }
                except Exception as e:
                    debug_info = [
                        f"#### 調試信息：",
                        f"- 處理錯誤: 原因={str(e)}",
                        f"- 堆棧跟踪: {traceback.format_exc()}"
                    ]
                    error_message = f"生成提示失敗，原因：{str(e)}。請稍後重試或檢查 API 配置。"
                    if platform == "高登討論區" and "cat_id" in str(e).lower():
                        error_message += f"\n可能原因：分類 ID {categories.get(selected_cat, '未知')} 無效，請檢查 config.py 的 HKGOLDEN_API['CATEGORIES']。"
                    placeholder.markdown(error_message)
                    st.session_state.chat_history.append({
                        "question": user_input,
                        "response": error_message,
                        "debug_info": debug_info,
                        "is_preview": True,
                        "timestamp": current_time
                    })
                    logger.error(f"Preview failed: question={user_input}, platform={platform}, error={str(e)}, traceback={traceback.format_exc()}")
                    st.session_state.processing_request = False
                    return
            
            prompt = result.get("response", "無提示內容")
            placeholder.markdown("**提示預覽**：")
            placeholder.code(prompt, language="text")
            
            if result.get("rate_limit_info"):
                debug_info.append("#### 調試信息：")
                debug_info.append("- 速率限制或錯誤記錄：")
                debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
            
            if not any(
                chat["question"] == user_input and 
                chat["response"] == prompt and 
                chat.get("is_preview") and 
                abs(chat["timestamp"] - current_time) < 1
                for chat in st.session_state.chat_history
            ):
                st.session_state.chat_history.append({
                    "question": user_input,
                    "response": prompt,
                    "debug_info": debug_info if debug_info else None,
                    "analysis": result.get("analysis"),
                    "is_preview": True,
                    "timestamp": current_time
                })
            
            logger.info(f"Completed preview: question={user_input}, platform={platform}, prompt_length={len(prompt)}, chat_history_length={len(st.session_state.chat_history)}")
            st.session_state.processing_request = False
    
    if user_input and not preview_button and not st.session_state.processing_request:
        logger.info(f"Processing user input: question={user_input}, platform={platform}, category={selected_cat}")
        
        submit_key = f"{user_input}:{platform}:{selected_cat}"
        current_time = time.time()
        if (st.session_state.last_submit_key == submit_key and 
            current_time - st.session_state.last_submit_time < 5):
            logger.warning("Skipping duplicate chat submission")
            st.warning("請勿重複提交相同請求，請稍後再試。")
            return
        
        st.session_state.processing_request = True
        st.session_state.last_submit_key = submit_key
        st.session_state.last_submit_time = current_time

        with st.chat_message("user"):
            st.markdown(f"**用戶**：{user_input}")
        
        with st.chat_message("assistant"):
            placeholder = st.empty()
            debug_info = []
            
            cache_key = f"{platform}_{selected_cat}_{user_input}"
            use_cache = cache_key in st.session_state.thread_content_cache and \
                        time.time() - st.session_state.thread_content_cache[cache_key]["timestamp"] < \
                        (LIHKG_API["CACHE_DURATION"] if platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"])
            
            if use_cache:
                logger.info(f"Using cache: platform={platform}, category={selected_cat}, question={user_input}")
                result = st.session_state.thread_content_cache[cache_key]["data"]
            else:
                try:
                    logger.info(f"Calling process_user_question: question={user_input}, platform={platform}, category={selected_cat}")
                    result = await process_user_question(
                        user_input,
                        platform=platform,
                        cat_id_map=categories,
                        selected_cat=selected_cat
                    )
                    st.session_state.thread_content_cache[cache_key] = {
                        "data": result,
                        "timestamp": time.time()
                    }
                except Exception as e:
                    debug_info = [
                        f"#### 調試信息：",
                        f"- 處理錯誤: 原因={str(e)}",
                        f"- 堆棧跟踪: {traceback.format_exc()}"
                    ]
                    error_message = f"處理失敗，原因：{str(e)}。請稍後重試或檢查 API 配置。"
                    if platform == "高登討論區" and "cat_id" in str(e).lower():
                        error_message += f"\n可能原因：分類 ID {categories.get(selected_cat, '未知')} 無效，請檢查 config.py 的 HKGOLDEN_API['CATEGORIES']。"
                    placeholder.markdown(error_message)
                    st.session_state.chat_history.append({
                        "question": user_input,
                        "response": error_message,
                        "debug_info": debug_info,
                        "timestamp": current_time
                    })
                    logger.error(f"Processing failed: question={user_input}, platform={platform}, error={str(e)}, traceback={traceback.format_exc()}")
                    st.session_state.processing_request = False
                    return
            
            response = result.get("response")
            if isinstance(response, str):
                response = response.strip()
                response = re.sub(r'\{\{ output \}\}|\{ output \}', '', response).strip()
                
                share_text = "無分享文字"
                reason = "無選擇理由"
                
                share_match = re.search(r'分享文字：\s*(.*?)(?=\n選擇理由：|\Z)', response, re.DOTALL)
                reason_match = re.search(r'選擇理由：\s*(.*)', response, re.DOTALL)
                
                if share_match:
                    share_text = share_match.group(1).strip()
                    placeholder.markdown(f"**分享文字**：{share_text}")
                if reason_match:
                    reason = reason_match.group(1).strip()
                    placeholder.markdown(f"**選擇理由**：{reason}")
            else:
                placeholder.write_stream(response)
            
            if result.get("rate_limit_info"):
                debug_info.append("#### 調試信息：")
                debug_info.append("- 速率限制或錯誤記錄：")
                debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
            
            if not any(
                chat["question"] == user_input and 
                chat["response"] == response and 
                not chat.get("is_preview") and 
                abs(chat["timestamp"] - current_time) < 1
                for chat in st.session_state.chat_history
            ):
                st.session_state.chat_history.append({
                    "question": user_input,
                    "response": response,
                    "debug_info": debug_info if debug_info else None,
                    "analysis": result.get("analysis"),
                    "timestamp": current_time
                })
            
            logger.info(f"Completed processing: question={user_input}, platform={platform}, chat_history_length={len(st.session_state.chat_history)}")
            st.session_state.processing_request = False

if __name__ == "__main__":
    asyncio.run(chat_page())
