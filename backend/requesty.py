"""Requesty.ai API client for making LLM requests."""

import asyncio
import httpx
from typing import List, Dict, Any, Optional
from .config import get_requesty_api_key, REQUESTY_API_URL

MAX_RETRIES = 2
INITIAL_RETRY_DELAY = 1.0

REQUESTY_MODELS_URL = "https://router.requesty.ai/v1/models"


def _strip_requesty_prefix(model_id: str) -> str:
    """Ensure we never send 'requesty:...' to the Requesty API."""
    if not model_id:
        return model_id
    return model_id.removeprefix("requesty:")


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    temperature: float = 0.7
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via Requesty API with retry logic for rate limits.

    Args:
        model: Requesty model identifier (e.g. "anthropic/claude-sonnet-4")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds
        temperature: Model temperature

    Returns:
        Response dict with 'content' and optional 'error' if failed
    """
    model = _strip_requesty_prefix(model or "")
    api_key = get_requesty_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    REQUESTY_API_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    retry_delay = INITIAL_RETRY_DELAY * (2 ** attempt)
                    print(f"Requesty rate limited on {model}, retrying in {retry_delay}s")
                    last_error = "rate_limited"
                    await asyncio.sleep(retry_delay)
                    continue

                if response.status_code == 200:
                    data = response.json()
                    message = data["choices"][0]["message"]
                    usage = data.get("usage") or {}
                    return {
                        "content": message.get("content"),
                        "reasoning_details": message.get("reasoning_details"),
                        "usage": usage,
                        "response_id": data.get("id"),
                        "total_tokens": usage.get("total_tokens"),
                    }

                last_error = f"HTTP {response.status_code}"
                if response.text:
                    try:
                        err = response.json()
                        last_error = err.get("error", {}).get("message", last_error)
                    except Exception:
                        pass
                print(f"Requesty error for {model}: {last_error}")
                break

        except httpx.TimeoutException:
            print(f"Requesty timeout for {model}")
            last_error = "timeout"
        except Exception as e:
            print(f"Requesty error for {model}: {e}")
            last_error = str(e)
            break

    return {
        "content": None,
        "error": last_error,
        "error_message": last_error or "Unknown error",
    }


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple Requesty models in parallel with batching for rate limits.
    """
    models = [_strip_requesty_prefix(m or "") for m in models]
    BATCH_SIZE = 3
    if len(models) >= 6:
        results = {}
        for i in range(0, len(models), BATCH_SIZE):
            batch = models[i : i + BATCH_SIZE]
            tasks = [query_model(m, messages, temperature=temperature) for m in batch]
            batch_responses = await asyncio.gather(*tasks)
            for model, response in zip(batch, batch_responses):
                results[model] = response
            if i + BATCH_SIZE < len(models):
                await asyncio.sleep(0.5)
        return results

    tasks = [query_model(m, messages, temperature=temperature) for m in models]
    responses = await asyncio.gather(*tasks)
    return {m: r for m, r in zip(models, responses)}


async def fetch_models() -> List[Dict[str, Any]]:
    """
    Fetch available models from Requesty API.
    Returns a list of model definitions compatible with the frontend.
    """
    api_key = get_requesty_api_key()
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                REQUESTY_MODELS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code != 200:
                print(f"Requesty fetch models: {response.status_code}")
                return []
            data = response.json()
            models = []
            for item in data.get("data", []):
                model_id = item.get("id", "")
                if not model_id:
                    continue
                # Requesty uses input_price/output_price or similar
                inp = item.get("input_price") or 0
                out = item.get("output_price") or 0
                try:
                    inp = float(inp)
                    out = float(out)
                except (TypeError, ValueError):
                    inp = out = 0
                is_free = inp == 0 and out == 0
                provider = "Requesty"
                if "/" in model_id:
                    provider = model_id.split("/")[0].title()
                models.append({
                    "id": model_id,
                    "name": item.get("name") or model_id,
                    "provider": provider,
                    "source": "requesty",
                    "context_length": item.get("context_window"),
                    "is_free": is_free,
                })
            return models
    except Exception as e:
        print(f"Error fetching Requesty models: {e}")
        return []
