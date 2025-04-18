import asyncio
import streamlit as st
from data_processor import process_user_question
import streamlit.logger
import time
import traceback

logger = streamlit.logger.get_logger(__name__)

async def chat_page():
    st.title("討論區帖子分享")
    
    # 初始化 session_state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "request_counter" not in st.session_state:
        st.session_state.request_counter = 0
    if "last_reset" not in st.session_state:
        st.session_state.last_reset = time.time()
    if "rate_limit_until" not in st.session_state:
        st.session_state.rate_limit_until = 0
    
    # 平台選擇
    platform = st.sidebar.selectbox("選擇平台", ["高登討論區", "LIHKG"])
    
    # 分類映射（使用整數 cat_id）
    categories = {
        "高登討論區": {
            "聊天": 1,
            "熱門": 2,
            "時事": 3,
            "娛樂": 4,
            "財經": 5,
            "科技": 6,
            "運動": 7
        },
        "LIHKG": {
            "吹水台": 1,
            "熱門台": 2,
            "時事台": 5,
            "上班台": 14,
            "財經台": 15,
            "成人台": 29,
            "創意台": 31
        }
    }
    cat_id_map = categories[platform]
    category = st.sidebar.selectbox("選擇分類", list(cat_id_map.keys()))
    
    # 顯示聊天記錄
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    user_input = st.chat_input("輸入你的問題（例如：分享最on9既post?）")
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)
        
        with st.chat_message("assistant"):
            placeholder = st.empty()
            try:
                # 檢查速率限制
                if time.time() < st.session_state.rate_limit_until:
                    error_message = (
                        f"API 速率限制中，請在 "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.session_state.rate_limit_until))} 後重試。"
                    )
                    placeholder.markdown(error_message)
                    st.session_state.messages.append({"role": "assistant", "content": error_message})
                    logger.warning(f"Rate limit: question={user_input}, until={st.session_state.rate_limit_until}")
                    return
                
                logger.info(f"Calling process_user_question: question={user_input}, platform={platform}, category={category}, cat_id={cat_id_map[category]}")
                result = await process_user_question(
                    user_input,
                    platform=platform,
                    cat_id_map=cat_id_map,
                    selected_cat=category,
                    request_counter=st.session_state.request_counter,
                    last_reset=st.session_state.last_reset,
                    rate_limit_until=st.session_state.rate_limit_until
                )
                
                # 更新速率限制狀態
                st.session_state.request_counter = result.get("request_counter", st.session_state.request_counter)
                st.session_state.last_reset = result.get("last_reset", st.session_state.last_reset)
                st.session_state.rate_limit_until = result.get("rate_limit_until", st.session_state.rate_limit_until)
                
                response = result["response"]
                if isinstance(response, str):
                    placeholder.markdown(response)
                    st.session_state.messages.append({"role": "assistant", "content": response})
                else:
                    full_response = ""
                    chunk_count = 0
                    try:
                        async for chunk in response:
                            chunk_count += 1
                            full_response += chunk
                            placeholder.markdown(full_response)
                            logger.debug(f"Stream chunk {chunk_count}: content={chunk[:50]}...")
                            await asyncio.sleep(0.05)
                        logger.info(f"Stream completed: question={user_input}, platform={platform}, chunk_count={chunk_count}, response_length={len(full_response)}")
                        st.session_state.messages.append({"role": "assistant", "content": full_response})
                    except Exception as e:
                        debug_info = [
                            f"#### 調試信息：",
                            f"- 流式錯誤: 原因={str(e)}",
                            f"- 塊計數: {chunk_count}",
                            f"- 堆棧跟踪: {traceback.format_exc()}"
                        ]
                        error_message = f"流式回應失敗：{str(e)}"
                        placeholder.markdown(error_message + "\n\n" + "\n".join(debug_info))
                        st.session_state.messages.append({"role": "assistant", "content": error_message})
                        logger.error(
                            f"Stream error: question={user_input}, platform={platform}, chunk_count={chunk_count}, "
                            f"error={str(e)}, traceback={traceback.format_exc()}"
                        )
            except Exception as e:
                debug_info = [
                    f"#### 調試信息：",
                    f"- 處理錯誤: 原因={str(e)}",
                    f"- 堆棧跟踪: {traceback.format_exc()}"
                ]
                result = result if 'result' in locals() else {"rate_limit_info": []}
                if result.get("rate_limit_info"):
                    debug_info.append("- 速率限制或錯誤記錄：")
                    debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
                error_message = f"處理失敗，原因：{str(e)}。請稍後重試或聯繫支持。"
                placeholder.markdown(error_message + "\n\n" + "\n".join(debug_info))
                st.session_state.messages.append({"role": "assistant", "content": error_message})
                logger.error(
                    f"Processing failed: question={user_input}, platform={platform}, category={category}, "
                    f"error={str(e)}, traceback={traceback.format_exc()}"
                )

if __name__ == "__main__":
    asyncio.run(chat_page())
