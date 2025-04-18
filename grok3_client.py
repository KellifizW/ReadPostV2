import aiohttp
import asyncio
import streamlit as st
import streamlit.logger
import uuid
import os
import json
from config import GROK3_API

logger = streamlit.logger.get_logger(__name__)

async def call_grok3_api(prompt, stream=False):
    """調用Grok 3 API，支持同步和流式回應，確保單次請求並記錄詳細日誌"""
    request_id = str(uuid.uuid4())
    logger.info(f"Preparing Grok 3 API request: request_id={request_id}, prompt_length={len(prompt)}, stream={stream}")
    
    try:
        api_key = st.secrets["grok3key"]
    except (KeyError, AttributeError):
        logger.warning(f"st.secrets['grok3key'] not found, trying environment variable: request_id={request_id}")
        api_key = os.getenv("GROK3_API_KEY")
        if not api_key:
            logger.error(f"Grok 3 API key missing: request_id={request_id}")
            return {"status": "error", "content": "Grok 3 API key is missing"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Streamlit-App/1.0"
    }
    
    payload = {
        "model": GROK3_API["MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": stream
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"Sending Grok 3 API request: request_id={request_id}, url={GROK3_API['BASE_URL']}/chat/completions, stream={stream}, payload={json.dumps(payload)[:200]}...")
            async with session.post(
                f"{GROK3_API['BASE_URL']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120
            ) as response:
                if response.status == 429:
                    logger.warning(f"Rate limit hit: request_id={request_id}, status=429")
                    return {"status": "error", "content": "API rate limit exceeded, please try again later"}
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"Grok 3 API failed: request_id={request_id}, status={response.status}, response={response_text[:200]}...")
                    return {"status": "error", "content": f"API request failed with status {response.status}: {response_text[:200]}"}
                
                if stream:
                    async def stream_content():
                        chunk_count = 0
                        total_length = 0
                        try:
                            async for line in response.content:
                                line = line.decode('utf-8').strip()
                                if not line or line == "data: [DONE]":
                                    continue
                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        content = data.get("choices", [{}])[0].get("delta", {}).get("content")
                                        if content:
                                            chunk_count += 1
                                            total_length += len(content)
                                            logger.debug(f"Stream chunk received: request_id={request_id}, chunk={content[:50]}..., chunk_count={chunk_count}")
                                            yield content
                                    except json.JSONDecodeError as e:
                                        logger.warning(f"Stream chunk parse error: request_id={request_id}, line={line[:50]}..., error={str(e)}")
                                        continue
                            logger.info(f"Stream completed: request_id={request_id}, chunk_count={chunk_count}, total_length={total_length}")
                        except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
                            logger.error(f"Stream connection error: request_id={request_id}, error={str(e)}")
                            raise
                        
                    return {"status": "success", "content": stream_content()}
                else:
                    response_text = await response.text()
                    data = await response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "No content")
                    logger.info(f"Grok 3 API succeeded: request_id={request_id}, content_length={len(content)}")
                    return {"status": "success", "content": content}
        
        except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            logger.error(f"Grok 3 API connection error: request_id={request_id}, error={str(e)}")
            return {"status": "error", "content": f"Connection error: {str(e)}"}
        except Exception as e:
            logger.error(f"Grok 3 API error: request_id={request_id}, error={str(e)}")
            return {"status": "error", "content": str(e)}
