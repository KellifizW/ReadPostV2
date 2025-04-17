import streamlit as st
import streamlit.logger
import aiohttp
import asyncio
import json
from typing import AsyncGenerator
from config import GROK3_API

logger = streamlit.logger.get_logger(__name__)

async def stream_grok3_response(prompt: str) -> AsyncGenerator[str, None]:
    """Stream response from Grok 3 API"""
    logger.info(f"Sending Grok 3 prompt: {prompt}")
    
    # 動態從 st.secrets 獲取 API 密鑰
    try:
        api_key = st.secrets["grok3key"]
    except KeyError:
        logger.error("Grok 3 API key is missing in Streamlit secrets (grok3key).")
        yield "Error: Grok 3 API key is missing. Please configure 'grok3key' in Streamlit secrets."
        return
    
    if not api_key:
        logger.error("Grok 3 API key is empty in Streamlit secrets (grok3key).")
        yield "Error: Grok 3 API key is empty. Please configure a valid 'grok3key' in Streamlit secrets."
        return
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": GROK3_API["MODEL"],
        "prompt": prompt,
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": True
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{GROK3_API['BASE_URL']}/completions",
                headers=headers,
                json=payload,
                timeout=30
            ) as response:
                if response.status != 200:
                    logger.error(f"Grok 3 API request failed: status={response.status}, reason={response.reason}")
                    yield f"Error: API request failed with status {response.status}"
                    return
                
                async for line in response.content:
                    if line:
                        try:
                            decoded_line = line.decode('utf-8').strip()
                            if decoded_line.startswith("data: "):
                                json_data = json.loads(decoded_line[6:])
                                if "choices" in json_data and json_data["choices"]:
                                    content = json_data["choices"][0].get("text", "")
                                    if content:
                                        yield content
                        except Exception as e:
                            logger.error(f"Error parsing Grok 3 response: {str(e)}")
                            yield f"Error: Failed to parse response - {str(e)}"
                            return
        except Exception as e:
            logger.error(f"Grok 3 API connection error: {str(e)}")
            yield f"Error: API connection failed - {str(e)}"
