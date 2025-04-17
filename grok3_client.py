import aiohttp
import asyncio
import streamlit.logger
import uuid
from config import GROK3_API

logger = streamlit.logger.get_logger(__name__)

async def call_grok3_api(prompt):
    """調用Grok 3 API，確保單次請求並記錄詳細日誌"""
    request_id = str(uuid.uuid4())
    logger.info(f"Preparing Grok 3 API request: request_id={request_id}, prompt_length={len(prompt)}")
    
    try:
        api_key = st.secrets["grok3key"]
    except KeyError:
        logger.error(f"Grok 3 API key missing: request_id={request_id}")
        return {"status": "error", "content": "Grok 3 API key is missing"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": "grok-3",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": GROK3_API["MAX_TOKENS"],
        "stream": False
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"Sending Grok 3 API request: request_id={request_id}, url={GROK3_API['BASE_URL']}/chat/completions")
            async with session.post(
                f"{GROK3_API['BASE_URL']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=180
            ) as response:
                response_text = await response.text()
                if response.status != 200:
                    logger.error(f"Grok 3 API failed: request_id={request_id}, status={response.status}, response={response_text}")
                    return {"status": "error", "content": f"API request failed with status {response.status}"}
                
                data = await response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "No content")
                logger.info(f"Grok 3 API succeeded: request_id={request_id}, content_length={len(content)}")
                return {"status": "success", "content": content}
        except Exception as e:
            logger.error(f"Grok 3 API error: request_id={request_id}, error={str(e)}")
            return {"status": "error", "content": str(e)}
