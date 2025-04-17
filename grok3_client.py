import streamlit as st
import streamlit.logger
import aiohttp
import asyncio
import json
from typing import AsyncGenerator
from config import GROK3_API
import time

logger = streamlit.logger.get_logger(__name__)

def log_prompt_to_file(prompt: str):
    """將完整 prompt 寫入日誌文件"""
    try:
        with open("prompts.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Prompt:\n{prompt}\n{'-'*50}\n")
    except Exception as e:
        logger.error(f"Failed to write prompt to file: {str(e)}")

async def stream_grok3_response(prompt: str) -> AsyncGenerator[str, None]:
    """Stream response from Grok 3 API"""
    # 分段記錄長 prompt
    logger.info(f"Sending Grok 3 prompt (length={len(prompt)} chars):")
    for i in range(0, len(prompt), 1000):
        logger.info(prompt[i:i+1000])
    log_prompt_to_file(prompt)
    
    # 截斷 prompt 若超過 MAX_TOKENS
    max_prompt_length = GROK3_API["MAX_TOKENS"] - 100  # 保留空間給輸出
    if len(prompt) > max_prompt_length:
        logger.warning(f"Prompt length ({len(prompt)}) exceeds MAX_TOKENS ({GROK3_API['MAX_TOKENS']}), truncating to {max_prompt_length} chars")
        prompt = prompt[:max_prompt_length] + "... [TRUNCATED]"
    
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
                    error_msg = await response.text()
                    logger.error(f"Grok 3 API request failed: status={response.status}, reason={error_msg}")
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
