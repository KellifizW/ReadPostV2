import streamlit as st
import streamlit.logger
import aiohttp
import asyncio
import json
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

async def call_grok3_api(prompt: str) -> dict:
    """非流式調用 Grok 3 API"""
    logger.info(f"Sending Grok 3 prompt (length={len(prompt)} chars):")
    for i in range(0, len(prompt), 1000):
        logger.info(prompt[i:i+1000])
    log_prompt_to_file(prompt)
    
    max_prompt_length = GROK3_API["MAX_TOKENS"] - 100
    if len(prompt) > max_prompt_length:
        logger.warning(f"Prompt length ({len(prompt)}) exceeds MAX_TOKENS ({GROK3_API['MAX_TOKENS']}), truncating to {max_prompt_length} chars")
        prompt = prompt[:max_prompt_length] + "... [TRUNCATED]"
    
    try:
        api_key = st.secrets["grok3key"]
    except KeyError:
        logger.error("Grok 3 API key is missing in Streamlit secrets (grok3key).")
        return {"content": "Error: Grok 3 API key is missing.", "status": "error"}
    
    if not api_key:
        logger.error("Grok 3 API key is empty in Streamlit secrets (grok3key).")
        return {"content": "Error: Grok 3 API key is empty.", "status": "error"}
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": GROK3_API["MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": False
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            start_time = time.time()
            logger.info(f"Sending Grok 3 API request: url={GROK3_API['BASE_URL']}/chat/completions")
            async with session.post(
                f"{GROK3_API['BASE_URL']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=300  # 增加超時到 300 秒
            ) as response:
                elapsed_time = time.time() - start_time
                response_text = await response.text()
                logger.info(f"Grok 3 API response: status={response.status}, elapsed={elapsed_time:.2f}s, body={response_text[:100]}...")
                if response.status != 200:
                    logger.error(f"Grok 3 API request failed: status={response.status}, reason={response_text}")
                    return {"content": f"Error: API request failed with status {response.status}: {response_text}", "status": "error"}
                data = json.loads(response_text)
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "No content")
                return {"content": content, "status": "success"}
        except aiohttp.ClientPayloadError as e:
            logger.error(f"Grok 3 API payload error: type={type(e).__name__}, message={str(e)}")
            return {"content": f"Error: API payload error - {str(e)}", "status": "error"}
        except aiohttp.ClientError as e:
            logger.error(f"Grok 3 API connection error: type={type(e).__name__}, message={str(e)}")
            return {"content": f"Error: API connection failed - {str(e)}", "status": "error"}
        except asyncio.TimeoutError as e:
            logger.error(f"Grok 3 API timeout error: {str(e)}")
            return {"content": f"Error: API request timed out - {str(e)}", "status": "error"}
        except Exception as e:
            logger.error(f"Grok 3 API unexpected error: type={type(e).__name__}, message={str(e)}")
            return {"content": f"Error: API unexpected error - {str(e)}", "status": "error"}
