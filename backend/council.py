"""3-stage LLM Council orchestration."""

from typing import List, Dict, Any, Tuple
import asyncio
import logging
import time
import re
from . import openrouter
from . import ollama_client
from .config import get_council_models, get_chairman_model
from .search import perform_web_search, SearchProvider
from .settings import get_settings

logger = logging.getLogger(__name__)

# Stage 2 aggregation policy:
# - Hard cap: discard ballots with completion below 25%
# - Soft cap: weight retained ballots by completion ratio
STAGE2_HARD_CAP_MIN_COMPLETION = 0.25


# Thinking-tag patterns used for display parsing and prompt-safe sanitization.
_THINK_BLOCK_PATTERNS = (
    re.compile(r"<think\b[^>]*>([\s\S]*?)</think>", re.IGNORECASE),
    re.compile(r"<thinking\b[^>]*>([\s\S]*?)</thinking>", re.IGNORECASE),
)


from .providers.openai import OpenAIProvider
from .providers.anthropic import AnthropicProvider
from .providers.google import GoogleProvider
from .providers.mistral import MistralProvider
from .providers.deepseek import DeepSeekProvider
from .providers.openrouter import OpenRouterProvider
from .providers.requesty import RequestyProvider
from .providers.ollama import OllamaProvider
from .providers.groq import GroqProvider
from .providers.custom_openai import CustomOpenAIProvider

# Initialize providers
PROVIDERS = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "google": GoogleProvider(),
    "mistral": MistralProvider(),
    "deepseek": DeepSeekProvider(),
    "groq": GroqProvider(),
    "openrouter": OpenRouterProvider(),
    "requesty": RequestyProvider(),
    "ollama": OllamaProvider(),
    "custom": CustomOpenAIProvider(),
}

# Models that should always run at temperature 1.0.
FORCED_TEMP_ONE_MODELS = {
    "google/gemini-3-pro-preview",
    "google/gemini-3-flash-preview",
    "google/gemini-2.5-flash",
    "x-ai/grok-4.1-fast",
    "z-ai/glm-5",
    "minimax/minimax-m2.5",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}

# Prefix-based matches for families/variants (e.g. ":free", "-exp", "-speciale").
FORCED_TEMP_ONE_PREFIXES = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
)


def _normalize_model_for_rules(model_id: str) -> str:
    """Strip internal provider prefix so rules can match on canonical model id."""
    if not model_id:
        return ""
    if ":" in model_id:
        maybe_provider, rest = model_id.split(":", 1)
        if maybe_provider in PROVIDERS:
            if "/" in rest:
                return rest
            # Direct provider IDs like "google:gemini-3-flash-preview" need provider slug restored.
            if maybe_provider in {"openai", "anthropic", "google", "mistral", "deepseek", "groq"}:
                return f"{maybe_provider}/{rest}"
            return rest
    return model_id


def _should_force_temperature_one(model_id: str) -> bool:
    """True when model should always use temperature=1.0."""
    normalized = _normalize_model_for_rules(model_id).lower()
    if normalized in FORCED_TEMP_ONE_MODELS:
        return True
    return any(normalized.startswith(prefix) for prefix in FORCED_TEMP_ONE_PREFIXES)


def get_provider_for_model(model_id: str) -> Any:
    """Determine the provider for a given model ID."""
    if ":" in model_id:
        provider_name = model_id.split(":")[0]
        if provider_name in PROVIDERS:
            return PROVIDERS[provider_name]

    # Default to OpenRouter for unprefixed models (legacy support)
    return PROVIDERS["openrouter"]


def _is_ollama_model(model_id: str) -> bool:
    """True if model is local Ollama (single-instance, run sequentially)."""
    return model_id.startswith("ollama:")

def _extract_total_tokens(response: Dict[str, Any]) -> Any:
    """Best-effort extraction of total token count from provider response."""
    if not isinstance(response, dict):
        return None
    total_tokens = response.get("total_tokens")
    if isinstance(total_tokens, int):
        return total_tokens

    usage = response.get("usage")
    if isinstance(usage, dict):
        if isinstance(usage.get("total_tokens"), int):
            return usage.get("total_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int):
            return int(prompt_tokens or 0) + int(completion_tokens or 0)

    return None

def _extract_usage(response: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort extraction of usage dict from provider response."""
    if not isinstance(response, dict):
        return {}
    usage = response.get("usage")
    return usage if isinstance(usage, dict) else {}


def _to_text(value: Any) -> str:
    """Convert arbitrary value to a safe string."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _extract_thinking_segments(text: str) -> List[str]:
    """Extract existing think/thinking blocks from response text."""
    segments: List[str] = []
    text = _to_text(text)
    for pattern in _THINK_BLOCK_PATTERNS:
        for match in pattern.findall(text):
            segment = _to_text(match).strip()
            if segment:
                segments.append(segment)
    return segments


def strip_thinking_tags(text: Any) -> str:
    """Remove think/thinking blocks from text for prompt-safe reuse."""
    cleaned = _to_text(text)
    for pattern in _THINK_BLOCK_PATTERNS:
        cleaned = pattern.sub("\n\n", cleaned)
    # Collapse accidental large gaps after stripping blocks.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _reasoning_details_to_text(reasoning_details: Any) -> str:
    """Convert provider-specific reasoning_details payloads to readable text."""
    if isinstance(reasoning_details, str):
        return reasoning_details.strip()
    if isinstance(reasoning_details, dict):
        # Handle single-object payloads.
        reasoning_details = [reasoning_details]
    if not isinstance(reasoning_details, list):
        return ""

    lines: List[str] = []
    for item in reasoning_details:
        if isinstance(item, str):
            if item.strip():
                lines.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        text_value = item.get("text")
        summary_value = item.get("summary")
        if isinstance(summary_value, list):
            for part in summary_value:
                if isinstance(part, str) and part.strip():
                    lines.append(part.strip())
        elif isinstance(summary_value, str) and summary_value.strip():
            lines.append(summary_value.strip())
        if isinstance(text_value, str) and text_value.strip():
            lines.append(text_value.strip())

    return "\n\n".join(lines).strip()


def normalize_thinking_content(
    content: Any,
    reasoning: Any = None,
    reasoning_details: Any = None,
) -> Dict[str, str]:
    """
    Return both user-display text (with think block) and prompt-safe text (stripped).

    Display text gets a single <think> block when thinking content exists from either:
    - existing tags in content
    - provider reasoning/reasoning_details fields
    """
    content_text = _to_text(content)
    prompt_safe_text = strip_thinking_tags(content_text)

    thinking_segments: List[str] = []
    thinking_segments.extend(_extract_thinking_segments(content_text))

    reasoning_text = _to_text(reasoning).strip()
    reasoning_details_text = _reasoning_details_to_text(reasoning_details)
    if reasoning_text:
        thinking_segments.append(reasoning_text)
    if reasoning_details_text:
        thinking_segments.append(reasoning_details_text)

    # Deduplicate while preserving order.
    deduped_segments: List[str] = []
    seen = set()
    for seg in thinking_segments:
        key = seg.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped_segments.append(key)

    if deduped_segments:
        joined_thinking = "\n\n".join(deduped_segments).strip()
        display_text = f"<think>\n{joined_thinking}\n</think>"
        if prompt_safe_text:
            display_text = f"{display_text}\n\n{prompt_safe_text}"
    else:
        display_text = prompt_safe_text

    return {
        "display_text": display_text.strip(),
        "prompt_safe_text": prompt_safe_text,
    }


async def query_model(model: str, messages: List[Dict[str, str]], timeout: float = 120.0, temperature: float = 0.7) -> Dict[str, Any]:
    """Dispatch query to appropriate provider."""
    provider = get_provider_for_model(model)
    effective_temperature = 1.0 if _should_force_temperature_one(model) else temperature
    return await provider.query(model, messages, timeout, effective_temperature)


async def query_models_parallel(models: List[str], messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Dispatch parallel query to appropriate providers."""
    tasks = []
    model_to_task_map = {}
    
    # Group models by provider to optimize batching if supported (mostly for OpenRouter/Ollama legacy)
    # But for simplicity and modularity, we'll just spawn individual tasks for now
    # OpenRouter and Ollama wrappers might handle their own internal concurrency if we called a batch method,
    # but the base interface is single query.
    # To maintain OpenRouter's batch efficiency if it exists, we could check type, but let's stick to simple asyncio.gather first.
    
    # Actually, the previous implementation used specific batch logic for Ollama and OpenRouter.
    # We should preserve that if possible, OR just rely on asyncio.gather which is fine for HTTP clients.
    # The previous `_query_ollama_batch` was just a helper to strip prefixes.
    # `openrouter.query_models_parallel` was doing the gather.
    
    # Let's just use asyncio.gather for all. It's clean and effective.
    
    async def _query_safe(m: str):
        try:
            return m, await query_model(m, messages)
        except Exception as e:
            return m, {"error": True, "error_message": str(e)}

    tasks = [_query_safe(m) for m in models]
    results = await asyncio.gather(*tasks)
    
    return dict(results)


async def stage1_collect_responses(user_query: str, search_context: str = "", request: Any = None) -> Any:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question
        search_context: Optional web search results to provide context
        request: FastAPI request object for checking disconnects

    Yields:
        - First yield: total_models (int)
        - Subsequent yields: Individual model results (dict)
    """
    settings = get_settings()

    # Build search context block if search results provided
    search_context_block = ""
    if search_context:
        from .prompts import STAGE1_SEARCH_CONTEXT_TEMPLATE
        search_context_block = STAGE1_SEARCH_CONTEXT_TEMPLATE.format(search_context=search_context)

    # Use customizable Stage 1 prompt
    try:
        prompt_template = settings.stage1_prompt
        if not prompt_template:
            from .prompts import STAGE1_PROMPT_DEFAULT
            prompt_template = STAGE1_PROMPT_DEFAULT

        prompt = prompt_template.format(
            user_query=user_query,
            search_context_block=search_context_block
        )
    except (KeyError, AttributeError, TypeError) as e:
        logger.warning(f"Error formatting Stage 1 prompt: {e}. Using fallback.")
        prompt = f"{search_context_block}Question: {user_query}" if search_context_block else user_query

    messages = [{"role": "user", "content": prompt}]

    # Prepare tasks for all models
    models = get_council_models()
    
    # Yield total count first
    yield len(models)

    council_temp = settings.council_temperature

    # Split Ollama (local, single-instance) from cloud/API models; run Ollama sequentially
    ollama_models = [m for m in models if _is_ollama_model(m)]
    cloud_models = [m for m in models if not _is_ollama_model(m)]

    async def _query_safe(m: str):
        started_at = time.perf_counter()
        try:
            response = await query_model(m, messages, temperature=council_temp)
            elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            return m, response, elapsed_ms
        except Exception as e:
            elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            return m, {"error": True, "error_message": str(e)}, elapsed_ms

    async def _run_ollama_sequential():
        """Run Ollama models one at a time (single local instance)."""
        out = []
        for m in ollama_models:
            if request and await request.is_disconnected():
                raise asyncio.CancelledError("Client disconnected")
            out.append(await _query_safe(m))
        return out

    def _make_result(model: str, response: dict, stage1_duration_ms: int) -> dict:
        if response is None:
            return None
        stage1_total_tokens = _extract_total_tokens(response)
        stage1_usage = _extract_usage(response)
        stage1_response_id = response.get("response_id") if isinstance(response.get("response_id"), str) else None
        if response.get('error'):
            return {
                "model": model,
                "response": None,
                "error": response.get('error'),
                "error_message": response.get('error_message', 'Unknown error'),
                "stage1_duration_ms": stage1_duration_ms,
                "stage1_total_tokens": stage1_total_tokens,
                "stage1_usage": stage1_usage,
                "stage1_response_id": stage1_response_id,
            }
        normalized_content = normalize_thinking_content(
            response.get("content", ""),
            response.get("reasoning"),
            response.get("reasoning_details"),
        )
        return {
            "model": model,
            "response": normalized_content["display_text"],
            "response_prompt_safe": normalized_content["prompt_safe_text"],
            "error": None,
            "stage1_duration_ms": stage1_duration_ms,
            "stage1_total_tokens": stage1_total_tokens,
            "stage1_usage": stage1_usage,
            "stage1_response_id": stage1_response_id,
        }

    # Cloud/API: one task per model (parallel). Ollama: one task that runs all sequentially.
    tasks = []
    ollama_task = None
    if ollama_models:
        ollama_task = asyncio.create_task(_run_ollama_sequential())
        tasks.append(ollama_task)
    tasks.extend(asyncio.create_task(_query_safe(m)) for m in cloud_models)

    pending = set(tasks)
    try:
        while pending:
            if request and await request.is_disconnected():
                logger.info("Client disconnected during Stage 1. Cancelling tasks...")
                for t in pending:
                    t.cancel()
                raise asyncio.CancelledError("Client disconnected")

            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=1.0)

            for task in done:
                try:
                    raw = await task
                    # Ollama task returns list of (model, response, elapsed_ms); cloud tasks return same tuple
                    if task is ollama_task and isinstance(raw, list):
                        for model, response, elapsed_ms in raw:
                            result = _make_result(model, response, elapsed_ms)
                            if result:
                                yield result
                    else:
                        model, response, elapsed_ms = raw
                        result = _make_result(model, response, elapsed_ms)
                        if result:
                            yield result
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error processing Stage 1 task result: {e}")

    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        raise


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    search_context: str = "",
    request: Any = None
) -> Any: # Returns an async generator
    """
    Stage 2: Collect peer rankings from all council models.
    
    Yields:
        - First yield: label_to_model mapping (dict)
        - Subsequent yields: Individual model results (dict)
    """
    settings = get_settings()

    # Filter to only successful responses for ranking
    successful_results = [r for r in stage1_results if not r.get('error')]

    # Create anonymized labels for responses (Response A, Response B, etc.)
    labels = [chr(65 + i) for i in range(len(successful_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, successful_results)
    }
    
    # Yield the mapping first so the caller has it
    yield label_to_model

    # Build the ranking prompt from prompt-safe Stage 1 text
    responses_text = "\n\n".join([
        f"Response {label}:\n{result.get('response_prompt_safe') or strip_thinking_tags(result.get('response', ''))}"
        for label, result in zip(labels, successful_results)
    ])

    search_context_block = ""
    if search_context:
        search_context_block = f"Context from Web Search:\n{search_context}\n"

    try:
        # Ensure prompt is not None
        prompt_template = settings.stage2_prompt
        if not prompt_template:
            from .prompts import STAGE2_PROMPT_DEFAULT
            prompt_template = STAGE2_PROMPT_DEFAULT

        ranking_prompt = prompt_template.format(
            user_query=user_query,
            responses_text=responses_text,
            search_context_block=search_context_block
        )
    except (KeyError, AttributeError, TypeError) as e:
        logger.warning(f"Error formatting Stage 2 prompt: {e}. Using fallback.")
        ranking_prompt = f"Question: {user_query}\n\n{responses_text}\n\nRank these responses."

    messages = [{"role": "user", "content": ranking_prompt}]

    # Only use models that successfully responded in Stage 1
    successful_models = [r['model'] for r in successful_results]

    # Use dedicated Stage 2 temperature (lower for consistent ranking output)
    stage2_temp = settings.stage2_temperature
    expected_count = len(successful_results)

    async def _query_safe(m: str):
        started_at = time.perf_counter()
        metadata = {
            "stage2_transform_applied": False,
            "stage2_retry_reason": None,
            "stage2_middle_out_mode": "retry_on_overflow",
        }
        try:
            provider = get_provider_for_model(m)
            if isinstance(provider, OpenRouterProvider):
                response = await openrouter.query_model(
                    m,
                    messages,
                    temperature=stage2_temp,
                )
                if openrouter.is_context_overflow_response(response):
                    metadata["stage2_retry_reason"] = "context_overflow"
                    metadata["stage2_transform_applied"] = True
                    response = await openrouter.query_model(
                        m,
                        messages,
                        temperature=stage2_temp,
                        transforms=["middle-out"],
                    )
            else:
                response = await query_model(m, messages, temperature=stage2_temp)
            elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            return m, response, elapsed_ms, metadata
        except Exception as e:
            elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            return m, {"error": True, "error_message": str(e)}, elapsed_ms, metadata

    # Run Ollama models sequentially; cloud models in parallel
    ollama_models = [m for m in successful_models if _is_ollama_model(m)]
    cloud_models = [m for m in successful_models if not _is_ollama_model(m)]

    async def _run_ollama_sequential():
        out = []
        for m in ollama_models:
            if request and await request.is_disconnected():
                raise asyncio.CancelledError("Client disconnected")
            out.append(await _query_safe(m))
        return out

    def _make_stage2_result(model: str, response: dict, stage2_duration_ms: int, metadata: dict = None) -> dict:
        if response is None:
            return None
        metadata = metadata or {}
        stage2_total_tokens = _extract_total_tokens(response)
        stage2_usage = _extract_usage(response)
        stage2_response_id = response.get("response_id") if isinstance(response.get("response_id"), str) else None
        if response.get('error'):
            return {
                "model": model,
                "ranking": None,
                "parsed_ranking": [],
                "error": response.get('error'),
                "error_message": response.get('error_message', 'Unknown error'),
                "stage2_duration_ms": stage2_duration_ms,
                "stage2_total_tokens": stage2_total_tokens,
                "stage2_usage": stage2_usage,
                "stage2_response_id": stage2_response_id,
                "stage2_transform_applied": metadata.get("stage2_transform_applied", False),
                "stage2_retry_reason": metadata.get("stage2_retry_reason"),
                "stage2_middle_out_mode": metadata.get("stage2_middle_out_mode"),
            }
        normalized_ranking = normalize_thinking_content(
            response.get("content", ""),
            response.get("reasoning"),
            response.get("reasoning_details"),
        )
        parsed = parse_ranking_from_text(normalized_ranking["prompt_safe_text"], expected_count=expected_count)
        return {
            "model": model,
            "ranking": normalized_ranking["display_text"],
            "ranking_prompt_safe": normalized_ranking["prompt_safe_text"],
            "parsed_ranking": parsed,
            "error": None,
            "stage2_duration_ms": stage2_duration_ms,
            "stage2_total_tokens": stage2_total_tokens,
            "stage2_usage": stage2_usage,
            "stage2_response_id": stage2_response_id,
            "stage2_transform_applied": metadata.get("stage2_transform_applied", False),
            "stage2_retry_reason": metadata.get("stage2_retry_reason"),
            "stage2_middle_out_mode": metadata.get("stage2_middle_out_mode"),
        }

    tasks = []
    ollama_task = None
    if ollama_models:
        ollama_task = asyncio.create_task(_run_ollama_sequential())
        tasks.append(ollama_task)
    tasks.extend(asyncio.create_task(_query_safe(m)) for m in cloud_models)

    pending = set(tasks)
    try:
        while pending:
            if request and await request.is_disconnected():
                logger.info("Client disconnected during Stage 2. Cancelling tasks...")
                for t in pending:
                    t.cancel()
                raise asyncio.CancelledError("Client disconnected")

            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=1.0)

            for task in done:
                try:
                    raw = await task
                    if task is ollama_task and isinstance(raw, list):
                        for model, response, elapsed_ms, metadata in raw:
                            result = _make_stage2_result(model, response, elapsed_ms, metadata)
                            if result:
                                yield result
                    else:
                        model, response, elapsed_ms, metadata = raw
                        result = _make_stage2_result(model, response, elapsed_ms, metadata)
                        if result:
                            yield result
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error processing task result: {e}")

    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        raise


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    search_context: str = ""
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2

    Returns:
        Dict with 'model' and 'response' keys
    """
    settings = get_settings()

    # Build comprehensive context for chairman (only include successful responses)
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result.get('response_prompt_safe') or strip_thinking_tags(result.get('response', 'No response'))}"
        for result in stage1_results
        if result.get('response') is not None
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result.get('ranking_prompt_safe') or strip_thinking_tags(result.get('ranking', 'No ranking'))}"
        for result in stage2_results
        if result.get('ranking') is not None
    ])

    search_context_block = ""
    if search_context:
        search_context_block = f"Context from Web Search:\n{search_context}\n"

    try:
        # Ensure prompt is not None
        prompt_template = settings.stage3_prompt
        if not prompt_template:
            from .prompts import STAGE3_PROMPT_DEFAULT
            prompt_template = STAGE3_PROMPT_DEFAULT

        chairman_prompt = prompt_template.format(
            user_query=user_query,
            stage1_text=stage1_text,
            stage2_text=stage2_text,
            search_context_block=search_context_block
        )
    except (KeyError, AttributeError, TypeError) as e:
        logger.warning(f"Error formatting Stage 3 prompt: {e}. Using fallback.")
        chairman_prompt = f"Question: {user_query}\n\nSynthesis required."

    # Determine message structure based on whether the prompt is default or custom
    from .prompts import STAGE3_PROMPT_DEFAULT
    
    # Check if we are using the default prompt (or if it's empty/None, which falls back to default)
    is_default_prompt = (not settings.stage3_prompt) or (settings.stage3_prompt.strip() == STAGE3_PROMPT_DEFAULT.strip())

    if is_default_prompt:
        # If using default, split into System (Persona) and User (Data) for better adherence at low temp
        messages = [
            {"role": "system", "content": "You are the Chairman of an LLM Council. Your task is to synthesize the provided model responses into a single, comprehensive answer."},
            {"role": "user", "content": chairman_prompt}
        ]
    else:
        # If custom prompt, send as single User message to respect user's custom persona/structure
        messages = [{"role": "user", "content": chairman_prompt}]

    # Query the chairman model with error handling
    chairman_model = get_chairman_model()
    chairman_temp = settings.chairman_temperature

    try:
        response = await query_model(chairman_model, messages, temperature=chairman_temp)

        # Check for error in response
        if response is None or response.get('error'):
            error_msg = response.get('error_message', 'Unknown error') if response else 'No response received'
            return {
                "model": chairman_model,
                "response": f"Error synthesizing final answer: {error_msg}",
                "error": True,
                "error_message": error_msg
            }

        normalized_final = normalize_thinking_content(
            response.get("content", ""),
            response.get("reasoning"),
            response.get("reasoning_details"),
        )
        final_response = normalized_final["display_text"]

        if not final_response:
             final_response = "No response generated by the Chairman."

        return {
            "model": chairman_model,
            "response": final_response,
            "error": False
        }

    except Exception as e:
        logger.error(f"Unexpected error in Stage 3 synthesis: {e}")
        return {
            "model": chairman_model,
            "response": f"Error: Unable to generate final synthesis due to unexpected error.",
            "error": True,
            "error_message": str(e)
        }


def parse_ranking_from_text(ranking_text: str, expected_count: int = None) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model
        expected_count: Optional number of expected ranked items (to truncate duplicates)

    Returns:
        List of response labels in ranked order
    """
    import re

    # Defensive: ensure ranking_text is a string
    if not isinstance(ranking_text, str):
        ranking_text = str(ranking_text) if ranking_text is not None else ''

    matches = []

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                matches = [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]
            else:
                # Fallback: Extract all "Response X" patterns in order from the section
                matches = re.findall(r'Response [A-Z]', ranking_section)
    
    # If no matches found in section (or section missing), fallback to full text search
    if not matches:
        matches = re.findall(r'Response [A-Z]', ranking_text)

    # Truncate if expected_count is provided
    if expected_count and len(matches) > expected_count:
        matches = matches[:expected_count]
        
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    return_diagnostics: bool = False
) -> Any:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        By default: List of dicts with model name and average rank, sorted best to worst.
        If return_diagnostics=True: Tuple[List[Dict[str, Any]], Dict[str, Any]].
    """
    from collections import defaultdict

    expected_count = len(label_to_model)
    valid_labels = set(label_to_model.keys())

    # Track weighted sums for each model
    model_rank_sum = defaultdict(float)
    model_weight_sum = defaultdict(float)
    model_rankings_count = defaultdict(int)

    diagnostics = {
        "ballots_total": len(stage2_results),
        "ballots_used": 0,
        "ballots_dropped_hard_cap": 0,
        "hard_cap_min_completion": STAGE2_HARD_CAP_MIN_COMPLETION,
    }

    def _dedupe_valid_labels(labels: List[str]) -> List[str]:
        """Preserve order, keep first occurrence, and ignore invalid labels."""
        seen = set()
        deduped = []
        for label in labels:
            if label not in valid_labels:
                continue
            if label in seen:
                continue
            seen.add(label)
            deduped.append(label)
        return deduped

    for ranking in stage2_results:
        ranking_text = ranking.get("ranking")
        if not ranking_text:
            diagnostics["ballots_dropped_hard_cap"] += 1
            continue

        # Parse the ranking from the structured format
        parsed_ranking = parse_ranking_from_text(ranking_text, expected_count=expected_count)
        parsed_ranking = _dedupe_valid_labels(parsed_ranking)
        ranked_count = len(parsed_ranking)
        completion_ratio = (ranked_count / expected_count) if expected_count > 0 else 0.0

        # Hard cap: discard very incomplete ballots
        if completion_ratio < STAGE2_HARD_CAP_MIN_COMPLETION:
            diagnostics["ballots_dropped_hard_cap"] += 1
            continue

        # Soft cap: retained ballots weighted by completion ratio
        ballot_weight = completion_ratio
        diagnostics["ballots_used"] += 1

        for position, label in enumerate(parsed_ranking, start=1):
            model_name = label_to_model[label]
            model_rank_sum[model_name] += ballot_weight * position
            model_weight_sum[model_name] += ballot_weight
            model_rankings_count[model_name] += 1

    # Calculate average position for each model
    aggregate = []
    for model, weighted_sum in model_rank_sum.items():
        total_weight = model_weight_sum[model]
        if total_weight > 0:
            avg_rank = weighted_sum / total_weight
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": model_rankings_count[model]
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    if return_diagnostics:
        return aggregate, diagnostics
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Uses a simple heuristic (first few words) to avoid unnecessary API calls.

    Args:
        user_query: The first user message

    Returns:
        A short title (max 50 chars)
    """
    # Validate input
    if not user_query or not isinstance(user_query, str):
        return "Untitled Conversation"

    # Simple heuristic: take first 50 chars
    title = user_query.strip()

    # If empty after stripping, return default
    if not title:
        return "Untitled Conversation"

    # Remove quotes if present
    title = title.strip('"\'')

    # If stripping quotes emptied the title, fall back to default
    if not title:
        return "Untitled Conversation"

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


def generate_search_query(user_query: str) -> str:
    """Return user query directly for web search (passthrough).
    
    Modern search engines (DuckDuckGo, Brave, Tavily) handle 
    natural language queries well without optimization.
    
    Args:
        user_query: The user's full question
    
    Returns:
        User query truncated to 100 characters for safety
    """
    return user_query[:100]  # Truncate for safety
