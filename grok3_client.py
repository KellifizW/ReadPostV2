import streamlit as st
import streamlit.logger
import aiohttp
import asyncio
import json
from typing import AsyncGenerator
from config import GROK3_API
import time
import aiohttp_retry

logger = streamlit.logger.get_logger(__name__)

def log_prompt_to_file(prompt: str):
    """將完整 prompt 寫入日誌文件"""
    try:
        with open("prompts.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Prompt:\n{prompt}\n{'-'*50}\n")
    except Exception as e:
        logger.error(f"Failed to write prompt to file: {str(e)}")

async def call_grok3_api(prompt: str) -> dict:
    """調用 Grok 3 API，返回完整響應"""
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
        return {"content": "Error: Grok 3 API key is missing. Please configure 'grok3key' in Streamlit secrets.", "status": "error"}
    
    if not api_key:
        logger.error("Grok 3 API key is empty in Streamlit secrets (grok3key).")
        return {"content": "Error: Grok 3 API key is empty. Please configure a valid 'grok3key' in Streamlit secrets.", "status": "error"}
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": GROK3_API["MODEL"],
        "prompt": prompt,
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": False
    }
    
    retry_options = aiohttp_retry.ExponentialRetry(attempts=5, start_timeout=30, factor=2.0, exceptions={aiohttp.ClientError, asyncio.TimeoutError})
    async with aiohttp.ClientSession() as session:
        retry_client = aiohttp_retry.RetryClient(client_session=session, retry_options=retry_options)
        try:
            logger.info(f"Sending Grok 3 API request: url={GROK3_API['BASE_URL']}/completions, payload={json.dumps(payload, ensure_ascii=False)[:100]}...")
            async with retry_client.post(
                f"{GROK3_API['BASE_URL']}/completions",
                headers=headers,
                json=payload,
                timeout=180
            ) as response:
                response_text = await response.text()
                logger.info(f"Grok 3 API response: status={response.status}, headers={response.headers}, body={response_text[:100]}...")
                if response.status != 200:
                    logger.error(f"Grok 3 API request failed: status={response.status}, reason={response_text}")
                    return {"content": f"Error: API request failed with status {response.status}: {response_text}", "status": "error"}
                
                data = json.loads(response_text)
                content = data.get("choices", [{}])[0].get("text", "No content")
                return {"content": content, "status": "success"}
        except aiohttp.ClientError as e:
            logger.error(f"Grok 3 API connection error: type={type(e).__name__}, message={str(e)}")
            return {"content": f"Error: API connection failed - {str(e)}", "status": "error"}
        except asyncio.TimeoutError as e:
            logger.error(f"Grok 3 API timeout error: {str(e)}")
            return {"content": f"Error: API request timed out - {str(e)}", "status": "error"}
        except Exception as e:
            logger.error(f"Grok 3 API unexpected error: type={type(e).__name__}, message={str(e)}")
            return {"content": f"Error: API unexpected error - {str(e)}", "status": "error"}
        finally:
            await retry_client.close()

async def stream_grok3_response(prompt: str) -> AsyncGenerator[dict, None]:
    """Stream response from Grok 3 API with retry"""
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
        yield {"content": "Error: Grok 3 API key is missing. Please configure 'grok3key' in Streamlit secrets.", "status": "error"}
        return
    
    if not api_key:
        logger.error("Grok 3 API key is empty in Streamlit secrets (grok3key).")
        yield {"content": "Error: Grok 3 API key is empty. Please configure a valid 'grok3key' in Streamlit secrets.", "status": "error"}
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
    
    retry_options = aiohttp_retry.ExponentialRetry(attempts=5, start_timeout=30, factor=2.0, exceptions={aiohttp.ClientError, asyncio.TimeoutError})
    async with aiohttp.ClientSession() as session:
        retry_client = aiohttp_retry.RetryClient(client_session=session, retry_options=retry_options)
        try:
            logger.info(f"Sending Grok 3 API request: url={GROK3_API['BASE_URL']}/completions, payload={json.dumps(payload, ensure_ascii=False)[:100]}...")
            async with retry_client.post(
                f"{GROK3_API['BASE_URL']}/completions",
                headers=headers,
                json=payload,
                timeout=180
            ) as response:
                response_text = await response.text()
                logger.info(f"Grok 3 API response: status={response.status}, headers={response.headers}, body={response_text[:100]}...")
                if response.status != 200:
                    logger.error(f"Grok 3 API request failed: status={response.status}, reason={response_text}")
                    yield {"content": f"Error: API request failed with status {response.status}: {response_text}", "status": "error"}
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
                                        yield {"content": content, "status": "success"}
                        except Exception as e:
                            logger.error(f"Error parsing Grok 3 response: {str(e)}")
                            yield {"content": f"Error: Failed to parse response - {str(e)}", "status": "error"}
                            return
        except aiohttp.ClientError as e:
            logger.error(f"Grok 3 API connection error: type={type(e).__name__}, message={str(e)}")
            yield {"content": f"Error: API connection failed - {str(e)}", "status": "error"}
        except asyncio.TimeoutError as e:
            logger.error(f"Grok 3 API timeout error: {str(e)}")
            yield {"content": f"Error: API request timed out - {str(e)}", "status": "error"}
        except Exception as e:
            logger.error(f"Grok 3 API unexpected error: type={type(e).__name__}, message={str(e)}")
            yield {"content": f"Error: API unexpected error - {str(e)}", "status": "error"}
        finally:
            await retry_client.close()
