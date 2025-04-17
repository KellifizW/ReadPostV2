import streamlit as st
import asyncio
import nest_asyncio
from chat_page import chat_page
from test_page import test_page

nest_asyncio.apply()

async def main():
    st.set_page_config(page_title="討論區數據分析", layout="wide")
    
    page = st.sidebar.selectbox("選擇頁面", ["聊天介面", "測試頁面"])
    
    if page == "聊天介面":
        await chat_page()
    elif page == "測試頁面":
        await test_page()

if __name__ == "__main__":
    asyncio.run(main())
