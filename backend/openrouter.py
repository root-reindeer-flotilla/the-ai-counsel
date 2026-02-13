"""OpenRouter API client for making LLM requests."""

import asyncio
import httpx
from typing import List, Dict, Any, Optional
from .config import get_openrouter_api_key, OPENROUTER_API_URL

# Retry configuration
MAX_RETRIES = 2
INITIAL_RETRY_DELAY = 1.0  # seconds

# Known broken/deprecated models that OpenRouter lists but don't actually work
BROKEN_MODELS = {
    "openai/gpt-oss-120b:free",   # Returns 404
    "openai/gpt-oss-20b:free",    # Returns 404
    "moonshotai/kimi-k2:free",    # Returns 404 - superseded by kimi-k2-0905
}

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _strip_openrouter_prefix(model_id: str) -> str:
    """Ensure we never send 'openrouter:...' to the OpenRouter API."""
    if not model_id:
        return model_id
    return model_id.removeprefix("openrouter:")


def _is_openrouter_gemini3_reasoning_target(model_id: str) -> bool:
    """True when model should force high reasoning effort on OpenRouter."""
    base_model = (model_id or "").split(":", 1)[0]
    return base_model in {
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview",
    }


def _is_openrouter_deepseek_reasoning_target(model_id: str) -> bool:
    """True when OpenRouter model is DeepSeek V3.2 (or variant) that supports reasoning via enabled flag."""
    model = _strip_openrouter_prefix(model_id or "")
    if not model.startswith("deepseek/"):
        return False
    # deepseek-v3.2, deepseek-v3.2-exp, deepseek-v3.2-speciale, etc.
    return "deepseek-v3.2" in model


def _extract_error_info(response: httpx.Response) -> Dict[str, Any]:
    """Best-effort extraction of OpenRouter error details."""
    default_message = f"HTTP {response.status_code}"
    info = {
        "message": default_message,
        "code": None,
        "param": None,
    }
    try:
        data = response.json()
        error = data.get("error")
        if isinstance(error, dict):
            info["message"] = error.get("message") or default_message
            info["code"] = error.get("code")
            info["param"] = error.get("param")
    except Exception:
        pass
    return info


def is_context_overflow_error(error_code: Any, error_message: Any) -> bool:
    """True when error indicates prompt/message/token limits were exceeded."""
    code = str(error_code or "").lower()
    message = str(error_message or "").lower()

    code_markers = {
        "context_length_exceeded",
        "max_tokens_exceeded",
        "token_limit_exceeded",
        "string_too_long",
    }
    if code in code_markers:
        return True

    message_markers = (
        "context length",
        "maximum context length",
        "prompt exceeds model context length",
        "prompt exceeds",
        "enable middle-out compression",
        "token limit exceeded",
        "maximum number of messages",
        "too many messages",
    )
    return any(marker in message for marker in message_markers)


def is_context_overflow_response(response_data: Optional[Dict[str, Any]]) -> bool:
    """Check normalized OpenRouter response dict for overflow flag."""
    if not isinstance(response_data, dict):
        return False
    if response_data.get("is_context_overflow") is True:
        return True
    return is_context_overflow_error(
        response_data.get("error_code"),
        response_data.get("error_message"),
    )


async def _resolve_to_canonical_slug(model_id: str) -> Optional[str]:
    """Resolve OpenRouter model id to canonical_slug (required for chat/completions)."""
    model_id = _strip_openrouter_prefix(model_id)
    api_key = get_openrouter_api_key()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code != 200:
                return None
            data = response.json()
            for item in data.get("data", []):
                rid = item.get("id") or ""
                rslug = item.get("canonical_slug") or rid
                if rid == model_id or rslug == model_id:
                    return rslug
    except Exception:
        pass
    return None


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    temperature: float = 0.7,
    transforms: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API with retry logic for rate limits.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds
        temperature: Model temperature

    Returns:
        Response dict with 'content', optional 'reasoning_details', and 'error' if failed
    """
    model = _strip_openrouter_prefix(model or "")
    api_key = get_openrouter_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        # Disable OpenRouter's native web search; we do our own search and inject context in the prompt.
        "plugins": [{"id": "web", "enabled": False}],
    }
    if _is_openrouter_gemini3_reasoning_target(model):
        payload["reasoning"] = {"effort": "high"}
    elif _is_openrouter_deepseek_reasoning_target(model_id=model):
        # DeepSeek V3.2: enable reasoning (thinking) via OpenRouter; response includes reasoning/reasoning_details
        payload["reasoning"] = {"enabled": True}
    if transforms is not None:
        payload["transforms"] = transforms

    last_error = None
    last_error_code = None
    last_error_message = None

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=payload
                )

                # Handle rate limiting with retry
                if response.status_code == 429:
                    retry_delay = INITIAL_RETRY_DELAY * (2 ** attempt)
                    print(f"Rate limited on {model}, retrying in {retry_delay}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    last_error = "rate_limited"
                    last_error_code = "rate_limit_exceeded"
                    last_error_message = "Rate limited - too many requests"
                    await asyncio.sleep(retry_delay)
                    continue

                # Handle 404: OpenRouter may require canonical_slug instead of id; resolve and retry once
                if response.status_code == 404:
                    canonical = await _resolve_to_canonical_slug(model)
                    if canonical and canonical != model:
                        payload["model"] = canonical
                        response = await client.post(
                            OPENROUTER_API_URL,
                            headers=headers,
                            json=payload
                        )
                        if response.status_code == 200:
                            data = response.json()
                            message = data["choices"][0]["message"]
                            usage = data.get("usage") or {}
                            return {
                                "content": message.get("content"),
                                "reasoning": message.get("reasoning"),
                                "reasoning_details": message.get("reasoning_details"),
                                "usage": usage,
                                "response_id": data.get("id"),
                                "total_tokens": usage.get("total_tokens"),
                                "error": None,
                                "error_code": None,
                                "is_context_overflow": False,
                            }
                    print(f"Model not found (404) on OpenRouter: {model}")
                    return {
                        "content": None,
                        "error": "model_not_found",
                        "error_message": f"Model '{model}' not found on OpenRouter.",
                        "error_code": "model_not_found",
                        "is_context_overflow": False,
                    }

                # Handle other client errors without retry
                if response.status_code == 400:
                    error_info = _extract_error_info(response)
                    error_message = error_info["message"] or "bad_request"
                    error_code = error_info["code"]
                    overflow = is_context_overflow_error(error_code, error_message)
                    print(f"Bad request for {model}: {error_message}")
                    return {
                        'content': None,
                        'error': 'bad_request',
                        'error_message': f"Model returned error: {error_message}",
                        'error_code': error_code,
                        'is_context_overflow': overflow,
                    }

                response.raise_for_status()

                data = response.json()
                message = data['choices'][0]['message']
                usage = data.get('usage') or {}

                return {
                    'content': message.get('content'),
                    'reasoning': message.get('reasoning'), # Capture reasoning field (common in DeepSeek R1/reasoning models)
                    'reasoning_details': message.get('reasoning_details'),
                    'usage': usage,
                    'response_id': data.get('id'),
                    'total_tokens': usage.get('total_tokens'),
                    'error': None,
                    'error_code': None,
                    'is_context_overflow': False,
                }

        except httpx.HTTPStatusError as e:
            print(f"HTTP error querying model {model}: {e}")
            last_error = f"http_{e.response.status_code}"
            info = _extract_error_info(e.response)
            last_error_code = info.get("code")
            last_error_message = info.get("message")
        except httpx.RemoteProtocolError as e:
            # This handles "peer closed connection without sending complete message body"
            retry_delay = INITIAL_RETRY_DELAY * (2 ** attempt)
            print(f"Remote protocol error (disconnect) on {model}: {e}. Retrying in {retry_delay}s...")
            last_error = "protocol_error"
            last_error_code = "protocol_error"
            last_error_message = str(e)
            await asyncio.sleep(retry_delay)
            continue
        except httpx.TimeoutException:
            print(f"Timeout querying model {model}")
            last_error = "timeout"
            last_error_code = "timeout"
            last_error_message = "Request timed out"
        except Exception as e:
            print(f"Error querying model {model}: {e}")
            last_error = str(e)
            last_error_code = "unknown_error"
            last_error_message = str(e)
            break  # Don't retry on unknown errors

    # All retries exhausted or non-retryable error
    error_messages = {
        "rate_limited": "Rate limited - too many requests",
        "timeout": "Request timed out",
    }
    return {
        'content': None,
        'error': last_error,
        'error_message': last_error_message or error_messages.get(last_error, f"Error: {last_error}"),
        'error_code': last_error_code,
        'is_context_overflow': is_context_overflow_error(last_error_code, last_error_message),
    }


async def fetch_generation(generation_id: str) -> Dict[str, Any]:
    """
    Fetch OpenRouter generation stats for a completion id.

    This endpoint provides authoritative cost/token accounting.
    """
    api_key = get_openrouter_api_key()
    if not api_key:
        return {"error": True, "error_message": "OpenRouter API key not configured"}
    if not generation_id:
        return {"error": True, "error_message": "generation_id is required"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/generation",
                params={"id": generation_id},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code != 200:
                return {
                    "error": True,
                    "error_message": f"OpenRouter generation API error: {response.status_code} - {response.text}",
                }
            return {"error": False, "data": response.json()}
    except Exception as e:
        return {"error": True, "error_message": str(e)}


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    temperature: float = 0.7
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel with batching for rate limit protection.
    
    For 6+ models, processes in batches of 3 to avoid hitting rate limits.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Normalize: never send "openrouter:..." to the API
    models = [_strip_openrouter_prefix(m or "") for m in models]

    # For 6+ models, use batching to avoid rate limits
    BATCH_SIZE = 3
    if len(models) >= 6:
        print(f"Batching {len(models)} models into groups of {BATCH_SIZE} to avoid rate limits")
        results = {}
        
        # Process in batches
        for i in range(0, len(models), BATCH_SIZE):
            batch = models[i:i + BATCH_SIZE]
            print(f"Processing batch {i//BATCH_SIZE + 1}: {batch}")
            
            # Create tasks for this batch
            tasks = [query_model(model, messages, temperature=temperature) for model in batch]
            
            # Wait for batch to complete
            batch_responses = await asyncio.gather(*tasks)
            
            # Map batch results
            for model, response in zip(batch, batch_responses):
                results[model] = response
            
            # Small delay between batches (except for last batch)
            if i + BATCH_SIZE < len(models):
                await asyncio.sleep(0.5)
        
        return results
    
    # For 5 or fewer models, send all at once (original behavior)
    tasks = [query_model(model, messages, temperature=temperature) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}


async def fetch_models() -> List[Dict[str, Any]]:
    """
    Fetch available models from OpenRouter API.
    Returns a list of model definitions compatible with the frontend.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(OPENROUTER_MODELS_URL)
            if response.status_code != 200:
                print(f"Failed to fetch OpenRouter models: {response.status_code}")
                return []
            
            data = response.json()
            models = []
            for item in data.get("data", []):
                raw_id = item.get("id", "")
                model_id = item.get("canonical_slug") or raw_id
                if not model_id:
                    continue
                if raw_id in BROKEN_MODELS:
                    continue
                
                # Determine if free based on pricing
                pricing = item.get("pricing", {})
                is_free = (
                    float(pricing.get("prompt", 0)) == 0 and 
                    float(pricing.get("completion", 0)) == 0
                )
                
                # Extract provider from ID (e.g. "google/gemini" -> "Google")
                provider = "OpenRouter" # Default
                if "/" in model_id:
                    provider_slug = model_id.split("/")[0]
                    # Capitalize nicely
                    if provider_slug == "openai": provider = "OpenAI"
                    elif provider_slug == "anthropic": provider = "Anthropic"
                    elif provider_slug == "google": provider = "Google"
                    elif provider_slug == "meta-llama": provider = "Meta"
                    elif provider_slug == "mistralai": provider = "Mistral"
                    elif provider_slug == "deepseek": provider = "DeepSeek"
                    else: provider = provider_slug.title()
                
                models.append({
                    "id": model_id,
                    "name": item.get("name"),
                    "provider": provider, 
                    "source": "openrouter", # Explicitly set for frontend grouping
                    "context_length": item.get("context_length"),
                    "is_free": is_free,
                    "pricing": pricing
                })
            
            return models
    except Exception as e:
        print(f"Error fetching OpenRouter models: {e}")
        return []
