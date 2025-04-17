```python
import streamlit as st
import asyncio
from datetime import datetime
import pytz
from data_processor import process_user_question
import time
from config import LIHKG_API, HKGOLDEN_API, GENERAL
import streamlit.logger

logger = streamlit.logger.get_logger(__name__)
HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])

async def prompt_page():
    st.title("Grok3看到的prompt")
    
    # 初始化 session_state
    if "prompt_history" not in st.session_state:
        st.session_state.prompt_history = []
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
    if "input_processed" not in st.session_state:
        st.session_state.input_processed = False

    # 檢查速率限制
    if time.time() < st.session_state.rate_limit_until:
        st.error(f"API 速率限制中，請在 {datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')} 後重試。")
        return
    
    # 平台與分類選擇
    platform = st.selectbox("選擇討論區平台", ["LIHKG", "高登討論區"], index=0)
    if platform == "LIHKG":
        categories = LIHKG_API["CATEGORIES"]
    else:
        categories = HKGOLDEN_API["CATEGORIES"]
    
    selected_cat = st.selectbox("選擇分類", options=list(categories.keys()), index=0)
    
    # 顯示 prompt 記錄
    st.markdown("### Prompt 記錄")
    for prompt_entry in st.session_state.prompt_history:
        with st.chat_message("user"):
            st.markdown(f"**用戶**：{prompt_entry['question']}")
        with st.chat_message("assistant"):
            st.markdown("**Prompt**：")
            st.code(prompt_entry['prompt'], language="text")
            if prompt_entry.get("debug_info"):
                with st.expander("調試信息"):
                    for info in prompt_entry["debug_info"]:
                        st.markdown(info)
    
    # 用戶輸入
    user_input = st.chat_input("輸入你的問題或要求（例如：分享任何帖文）")
    
    if user_input and not st.session_state.input_processed:
        logger.info(f"Processing user input: question={user_input}, platform={platform}, category={selected_cat}")
        
        # 檢查重複提交
        submit_key = f"{user_input}:{platform}:{selected_cat}"
        current_time = time.time()
        if (st.session_state.last_submit_key == submit_key and 
            current_time - st.session_state.last_submit_time < 5):
            logger.warning("Skipping duplicate prompt submission")
            st.warning("請勿重複提交相同請求，請稍後再試。")
            return
        
        # 標記輸入已處理
        st.session_state.input_processed = True
        st.session_state.last_submit_key = submit_key
        st.session_state.last_submit_time = current_time

        # 顯示用戶輸入
        with st.chat_message("user"):
            st.markdown(f"**用戶**：{user_input}")
        
        # 處理請求
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
                        selected_cat=selected_cat,
                        return_prompt=True
                    )
                    st.session_state.thread_content_cache[cache_key] = {
                        "data": result,
                        "timestamp": time.time()
                    }
                except Exception as e:
                    result = {}
                    debug_info = [f"#### 調試信息：\n- 處理錯誤: 原因={str(e)}"]
                    if result.get("rate_limit_info"):
                        debug_info.append("- 速率限制或錯誤記錄：")
                        debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
                    error_message = f"處理失敗，原因：{str(e)}。請稍後重試或檢查 API 配置。"
                    placeholder.markdown(error_message)
                    st.session_state.prompt_history.append({
                        "question": user_input,
                        "prompt": error_message,
                        "debug_info": debug_info
                    })
                    logger.error(f"Processing failed: question={user_input}, platform={platform}, error={str(e)}")
                    st.session_state.input_processed = False
                    return
            
            prompt = result.get("response", "無 Prompt 內容")
            placeholder.markdown("**Prompt**：")
            placeholder.code(prompt, language="text")
            
            if result.get("rate_limit_info"):
                debug_info.append("#### 調試信息：")
                debug_info.append("- 速率限制或錯誤記錄：")
                debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
            
            # 避免重複追加 Prompt 記錄
            if not any(entry["question"] == user_input and entry["prompt"] == prompt for entry in st.session_state.prompt_history):
                st.session_state.prompt_history.append({
                    "question": user_input,
                    "prompt": prompt,
                    "debug_info": debug_info if debug_info else None
                })
            
            logger.info(f"Completed processing: question={user_input}, platform={platform}, prompt_length={len(prompt)}, prompt_history_length={len(st.session_state.prompt_history)}")
            st.session_state.input_processed = False
    
    # 重置輸入狀態（在每次渲染結束後）
    if not user_input:
        st.session_state.input_processed = False
```