import streamlit as st
import asyncio
from datetime import datetime
import pytz
from data_processor import process_user_question
from grok3_client import stream_grok3_response
import time
from config import LIHKG_API, HKGOLDEN_API, GENERAL

HONG_KONG_TZ = pytz.timezone(GENERAL["TIMEZONE"])
logger = st.logger.get_logger(__name__)

async def chat_page():
    st.title("討論區聊天介面")
    
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "topic_list_cache" not in st.session_state:
        st.session_state.topic_list_cache = {}  # {platform_cat_id: {"items": [...], "timestamp": float}}
    if "thread_content_cache" not in st.session_state:
        st.session_state.thread_content_cache = {}  # {thread_id: {"data": dict, "timestamp": float}}
    if "rate_limit_until" not in st.session_state:
        st.session_state.rate_limit_until = 0
    if "request_counter" not in st.session_state:
        st.session_state.request_counter = 0
    if "last_reset" not in st.session_state:
        st.session_state.last_reset = time.time()
    
    # 合併 LIHKG 和高登的分類選項
    platform_options = ["LIHKG", "高登討論區"]
    selected_platform = st.selectbox("選擇討論區平台", options=platform_options, index=0)
    
    if selected_platform == "LIHKG":
        categories = LIHKG_API["CATEGORIES"]
        min_replies = LIHKG_API["MIN_REPLIES"]
    else:
        categories = HKGOLDEN_API["CATEGORIES"]
        min_replies = HKGOLDEN_API["MIN_REPLIES"]
    
    selected_cat = st.selectbox("選擇分類", options=list(categories.keys()), index=0)
    
    # 顯示速率限制狀態
    st.markdown("#### 速率限制狀態")
    st.markdown(f"- 當前請求計數: {st.session_state.request_counter}")
    st.markdown(f"- 最後重置時間: {datetime.fromtimestamp(st.session_state.last_reset, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    st.markdown(
        f"- 速率限制解除時間: "
        f"{datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if st.session_state.rate_limit_until > time.time() else '無限制'}"
    )
    
    for chat in st.session_state.chat_history:
        with st.chat_message("user"):
            st.markdown(chat["question"])
        with st.chat_message("assistant"):
            st.markdown(chat["answer"])
    
    user_question = st.chat_input("請輸入您想查詢的話題（例如：有哪些搞笑話題？）")
    
    if user_question:
        with st.chat_message("user"):
            st.markdown(user_question)
        
        with st.spinner("正在處理您的問題..."):
            try:
                if time.time() < st.session_state.rate_limit_until:
                    error_message = (
                        f"API 速率限制中，請在 "
                        f"{datetime.fromtimestamp(st.session_state.rate_limit_until, tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S')} 後重試。"
                    )
                    with st.chat_message("assistant"):
                        st.markdown(error_message)
                    st.session_state.chat_history.append({
                        "question": user_question,
                        "answer": error_message
                    })
                    logger.warning(f"速率限制阻止請求: 問題={user_question}, 限制至={st.session_state.rate_limit_until}")
                    return
                
                st.session_state.last_user_query = user_question
                
                logger.info(f"開始處理問題: 問題={user_question}, 平台={selected_platform}, 分類={selected_cat}")
                
                # 檢查緩存
                cat_id = categories[selected_cat]
                for cat_name, cat_id_val in categories.items():
                    if cat_name in user_question:
                        selected_cat = cat_name
                        cat_id = cat_id_val
                        break
                
                cache_key = f"{selected_platform}_{cat_id}"
                cache_duration = LIHKG_API["CACHE_DURATION"] if selected_platform == "LIHKG" else HKGOLDEN_API["CACHE_DURATION"]
                use_cache = False
                if cache_key in st.session_state.topic_list_cache:
                    cache_data = st.session_state.topic_list_cache[cache_key]
                    if time.time() - cache_data["timestamp"] < cache_duration:
                        result = {
                            "items": cache_data["items"],
                            "rate_limit_info": [],
                            "request_counter": st.session_state.request_counter,
                            "last_reset": st.session_state.last_reset,
                            "rate_limit_until": st.session_state.rate_limit_until
                        }
                        use_cache = True
                        logger.info(f"使用緩存: platform={selected_platform}, cat_id={cat_id}, 帖子數={len(result['items'])}")
                
                if not use_cache:
                    result = await process_user_question(
                        user_question,
                        platform=selected_platform,
                        cat_id_map=categories,
                        selected_cat=selected_cat,
                        request_counter=st.session_state.request_counter,
                        last_reset=st.session_state.last_reset,
                        rate_limit_until=st.session_state.rate_limit_until
                    )
                    # 更新緩存
                    st.session_state.topic_list_cache[cache_key] = {
                        "items": result.get("items", []),
                        "timestamp": time.time()
                    }
                    logger.info(f"更新緩存: platform={selected_platform}, cat_id={cat_id}, 帖子數={len(result['items'])}")
                
                st.session_state.request_counter = result.get("request_counter", st.session_state.request_counter)
                st.session_state.last_reset = result.get("last_reset", st.session_state.last_reset)
                st.session_state.rate_limit_until = result.get("rate_limit_until", st.session_state.rate_limit_until)
                
                thread_data = result.get("thread_data", [])
                rate_limit_info = result.get("rate_limit_info", [])
                question_cat = result.get("selected_cat", selected_cat)
                
                # 記錄抓取總結
                logger.info(
                    f"抓取完成: 問題={user_question}, 平台={selected_platform}, 分類={question_cat}, "
                    f"帖子數={len(thread_data)}, 速率限制信息={rate_limit_info if rate_limit_info else '無'}, "
                    f"使用緩存={use_cache}"
                )
                
                # 檢查是否有數據
                if not thread_data:
                    answer = f"在 {selected_platform} 的 {question_cat} 中未找到回覆數 ≥ {min_replies} 的帖子。"
                    debug_info = []
                    if rate_limit_info:
                        debug_info.append("#### 調試信息：")
                        debug_info.extend(f"- {info}" for info in rate_limit_info)
                    with st.chat_message("assistant"):
                        st.markdown(answer)
                        with st.expander("查看調試信息"):
                            for info in debug_info:
                                st.markdown(info)
                    st.session_state.chat_history.append({
                        "question": user_question,
                        "answer": answer
                    })
                    logger.warning(f"無有效帖子數據: 問題={user_question}, 平台={selected_platform}, 分類={question_cat}")
                    return
                
                # 構建回應並添加調試資訊
                answer = f"### 來自 {selected_platform} 的 {question_cat} 話題（回覆數 ≥ {min_replies}）：\n"
                debug_info = ["#### 調試信息："]
                failed_threads = []
                successful_threads = []
                
                for item in thread_data:
                    thread_id = item["thread_id"]
                    if not item["replies"] and item["no_of_reply"] >= min_replies:
                        failed_threads.append(thread_id)
                        debug_info.append(f"- 帖子 ID {thread_id} 抓取失敗，可能無效或無權訪問。")
                    else:
                        successful_threads.append(thread_id)
                    answer += (
                        f"- 帖子 ID: {item['thread_id']}，標題: {item['title']}，"
                        f"回覆數: {item['no_of_reply']}，"
                        f"最後回覆時間: {datetime.fromtimestamp(int(item['last_reply_time']), tz=HONG_KONG_TZ).strftime('%Y-%m-%d %H:%M:%S') if item['last_reply_time'] else '未知'}，"
                        f"正評: {item['like_count']}，負評: {item['dislike_count']}\n"
                    )
                    if item["replies"]:
                        answer += "  回覆:\n"
                        for idx, reply in enumerate(item["replies"], 1):
                            answer += (
                                f"    - 回覆 {idx}: {reply['msg'][:100]}"
                                f"{'...' if len(reply['msg']) > 100 else ''} "
                                f"(正評: {reply['like_count']}, 負評: {reply['dislike_count']})\n"
                            )
                
                answer += f"\n共找到 {len(thread_data)} 篇符合條件的帖子。"
                debug_info.append(f"- 成功抓取帖子數: {len(successful_threads)}，ID: {successful_threads}")
                if failed_threads:
                    debug_info.append(f"- 失敗帖子數: {len(failed_threads)}，ID: {failed_threads}")
                    answer += f"\n警告：以下帖子無法獲取回覆：{', '.join(map(str, failed_threads))}。"
                
                if rate_limit_info:
                    debug_info.append("- 速率限制或錯誤記錄：")
                    debug_info.extend(f"  - {info}" for info in rate_limit_info)
                
                debug_info.append(f"- 使用緩存: {use_cache}")
                logger.info("\n".join(debug_info))
                
                # 整合 Grok 3 回應
                try:
                    grok_context = f"問題: {user_question}\n帖子數據:\n{answer}"
                    grok_response = ""
                    with st.chat_message("assistant"):
                        grok_container = st.empty()
                        async for chunk in stream_grok3_response(grok_context):
                            grok_response += chunk
                            grok_container.markdown(grok_response)
                        grok_container.markdown(grok_response + "\n\n" + "點擊下方查看調試信息")
                        with st.expander("查看調試信息"):
                            for info in debug_info:
                                st.markdown(info)
                    if grok_response and not grok_response.startswith("錯誤:"):
                        final_answer = grok_response
                    else:
                        final_answer = (grok_response or "無法獲取 Grok 3 建議。")
                except Exception as e:
                    logger.warning(f"Grok 3 增強失敗: 問題={user_question}, 錯誤={str(e)}")
                    final_answer = f"錯誤：無法獲取 Grok 3 建議，原因：{str(e)}"
                    if failed_threads:
                        final_answer += f"\n警告：以下帖子無法獲取回覆：{', '.join(map(str, failed_threads))}。"
                
                with st.chat_message("assistant"):
                    st.markdown(final_answer)
                    with st.expander("查看調試信息"):
                        for info in debug_info:
                            st.markdown(info)
                st.session_state.chat_history.append({
                    "question": user_question,
                    "answer": final_answer
                })
                
            except Exception as e:
                debug_info = [f"#### 調試信息：\n- 處理錯誤: 原因={str(e)}"]
                if result.get("rate_limit_info"):
                    debug_info.append("- 速率限制或錯誤記錄：")
                    debug_info.extend(f"  - {info}" for info in result["rate_limit_info"])
                error_message = f"處理失敗，原因：{str(e)}"
                with st.chat_message("assistant"):
                    st.markdown(error_message)
                    with st.expander("查看調試信息"):
                        for info in debug_info:
                            st.markdown(info)
                logger.error(f"處理錯誤: 問題={user_question}, 錯誤={str(e)}")
                st.session_state.chat_history.append({
                    "question": user_question,
                    "answer": error_message
                })