import streamlit as st
import asyncio
from datetime import datetime
import pytz
from data_processor import process_user_question
from grok3_client import call_grok3_api
import time
from config import LIHKG_API, HKGOLDEN_API, GENERAL
import streamlit.logger

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
    if "last_submit_key" not in st.session_state:
        st.session_state.last_submit_key = None
    if "last_submit_time" not in st.session_state:
        st.session_state.last_submit_time = 0
    
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
            st.markdown(f"**回應**：{chat['response']}")
            if chat.get("debug_info"):
                with st.expander("調試信息"):
                    for info in chat["debug_info"]:
                        st.markdown(info)
    
    user_input = st.chat_input("輸入你的問題或要求（例如：分享任何帖文）")
    
    if user_input:
        submit_key = f"{user_input}:{platform}:{selected_cat}"
        current_time = time.time()
        if (st.session_state.last_submit_key == submit_key and 
            current_time - st.session_state.last_submit_time < 5):
            logger.warning("Skipping duplicate chat submission")
            st.warning("請勿重複提交相同請求，請稍後再試。")
            return
        
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
                    logger.info(f"Starting to process question: question={user_input}, platform={platform}, category={selected_cat}")
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
                    result = {}
                    debug_info = [f"#### 調試信息：\n- 處理錯誤: 原因={repr(e)}"]
                    if result.get("rate_limit_info"):
                        debug_info.append("- 速率限制或錯誤記錄：")
                        debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
                    error_message = f"處理失敗，原因：{repr(e)}"
                    placeholder.markdown(error_message)
                    st.session_state.chat_history.append({
                        "question": user_input,
                        "response": error_message,
                        "debug_info": debug_info
                    })
                    logger.error(f"Processing failed: question={user_input}, platform={platform}, error={repr(e)}")
                    return
            
            response = result.get("response", "無回應內容")
            placeholder.markdown(response)
            
            if result.get("rate_limit_info"):
                debug_info.append("#### 調試信息：")
                debug_info.append("- 速率限制或錯誤記錄：")
                debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
            
            st.session_state.chat_history.append({
                "question": user_input,
                "response": response,
                "debug_info": debug_info if debug_info else None
            })
            st.session_state.last_submit_key = submit_key
            st.session_state.last_submit_time = current_time
            
            logger.info(f"Processed question: question={user_input}, platform={platform}, response_length={len(response)}")
