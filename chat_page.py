import streamlit as st
import asyncio
from datetime import datetime
import pytz
import re
from data_processor import process_user_question
import time
from config import LIHKG_API, HKGOLDEN_API, GENERAL
import streamlit.logger

logger = streamlit.logger.get_logger(__name__)
HONG_KONG_TZ = pytz.timezone("Asia/Hong_Kong")

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
                    
                    share_match = re.search(r'分享文字：\s*(.*?)(?=\n*選擇理由：|\Z)', response, re.DOTALL)
                    reason_match = re.search(r'選擇理由：\s*(.*)', response, re.DOTALL)
                    
                    if share_match:
                        share_text = share_match.group(1).strip()
                    if reason_match:
                        reason = reason_match.group(1).strip()
                    
                    if share_text != "無分享文字":
                        st.markdown(f"**分享文字**：{share_text}")
                    if reason != "無選擇理由":
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
    
    user_input = st.chat_input("輸入你的
