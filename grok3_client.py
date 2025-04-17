import streamlit as st
import streamlit.logger
import aiohttp
import asyncio
import json
from typing import AsyncGenerator
from config import GROK3_API

logger = streamlit.logger.get_logger(__name__)

async def stream_grok3_response(text: str, retries: int = GROK3_API["RETRIES"]) -> AsyncGenerator[str, None]:
    """以流式方式從 Grok 3 API 獲取回應"""
    try:
        GROK3_API_KEY = st.secrets["grok3key"]
    except KeyError:
        logger.error("Grok 3 API 密鑰缺失")
        st.error("未找到 Grok 3 API 密鑰，請檢查配置")
        yield "錯誤: 缺少 API 密鑰"
        return
    
    # 智能截斷
    if len(text) > GROK3_API["TOKEN_LIMIT"]:
        logger.warning(f"輸入超限: 字元數={len(text)}，將截斷")
        text = text[:GROK3_API["TOKEN_LIMIT"] - 100] + "\n[已截斷]"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROK3_API_KEY}"
    }
    payload = {
        "model": GROK3_API["MODEL"],
        "messages": [
            {"role": "system", "content": "你是 Grok 3，以繁體中文回答，確保回覆清晰、簡潔，僅基於提供數據。"},
            {"role": "user", "content": text}
        ],
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "temperature": GROK3_API["TEMPERATURE"],
        "stream": True
    }
    
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(GROK3_API["URL"], headers=headers, json=payload, timeout=GROK3_API["TIMEOUT"]) as response:
                    response.raise_for_status()
                    async for line in response.content:
                        if not line or line.isspace():
                            continue
                        line_str = line.decode('utf-8').strip()
                        if line_str == "data: [DONE]":
                            break
                        if line_str.startswith("data: "):
                            data = line_str[6:]
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                logger.warning(f"JSON 解析失敗: 數據={line_str}")
                                continue
            logger.info(f"Grok 3 流式完成: 輸入字元={len(text)}")
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Grok 3 請求失敗，第 {attempt+1} 次重試: 錯誤={str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.error(f"Grok 3 異常: 錯誤={str(e)}")
            yield f"錯誤: 連線失敗，請稍後重試或檢查網路"