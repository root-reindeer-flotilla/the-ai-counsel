"""FastAPI backend for LLM Council."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
import uuid

from . import storage
from .council import PROVIDERS
from .runs import RunManager, VALID_EXECUTION_MODES
from .search import SearchProvider
from .settings import get_settings, update_settings, Settings, DEFAULT_COUNCIL_MODELS, DEFAULT_CHAIRMAN_MODEL

app = FastAPI(title="LLM Council Plus API")
RUN_MANAGER = RunManager()

# Enable CORS for local development and network access
# Allow requests from any hostname on ports 5173 and 3000 (frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://.*:(5173|5174|3000)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str
    web_search: bool = False
    execution_mode: str = "full"  # 'chat_only', 'chat_ranking', 'full'


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    deleted = storage.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


@app.post("/api/conversations/{conversation_id}/runs")
async def start_conversation_run(conversation_id: str, body: SendMessageRequest):
    """Start a background deliberation run and return a run id."""
    try:
        run = await RUN_MANAGER.start_run(
            conversation_id=conversation_id,
            content=body.content,
            web_search=body.web_search,
            execution_mode=body.execution_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    snapshot = await RUN_MANAGER.snapshot(run)
    return snapshot


@app.get("/api/conversations/{conversation_id}/runs/active")
async def get_active_conversation_run(conversation_id: str):
    """Get active run for a conversation, if any."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    run = await RUN_MANAGER.get_active_run_for_conversation(conversation_id)
    if run is None:
        return {"active_run": None}
    return {"active_run": await RUN_MANAGER.snapshot(run)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Get a run snapshot."""
    run = await RUN_MANAGER.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return await RUN_MANAGER.snapshot(run)


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str, from_event: int = 0):
    """Stream events for a run (supports reconnect/replay)."""
    run = await RUN_MANAGER.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return StreamingResponse(
        RUN_MANAGER.stream_events(run_id, from_event=from_event),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Force-stop a running deliberation."""
    run = await RUN_MANAGER.cancel_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run.run_id, "status": run.status}


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, body: SendMessageRequest):
    """
    Legacy endpoint kept for compatibility.
    Internally starts a resumable run and streams that run's events.
    """
    # Preserve historical validation semantics used by tests/clients.
    if body.execution_mode not in VALID_EXECUTION_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid execution_mode. Must be one of: {VALID_EXECUTION_MODES}",
        )
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        run = await RUN_MANAGER.start_run(
            conversation_id=conversation_id,
            content=body.content,
            web_search=body.web_search,
            execution_mode=body.execution_mode,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StreamingResponse(
        RUN_MANAGER.stream_events(run.run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    search_provider: Optional[str] = None
    search_keyword_extraction: Optional[str] = None
    ollama_base_url: Optional[str] = None
    full_content_results: Optional[int] = None

    # Custom OpenAI-compatible endpoint
    custom_endpoint_name: Optional[str] = None
    custom_endpoint_url: Optional[str] = None
    custom_endpoint_api_key: Optional[str] = None

    # API Keys
    serper_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    brave_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    requesty_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    mistral_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None

    # Enabled Providers
    enabled_providers: Optional[Dict[str, bool]] = None
    direct_provider_toggles: Optional[Dict[str, bool]] = None

    # Council Configuration (unified)
    council_models: Optional[List[str]] = None
    chairman_model: Optional[str] = None
    
    # Remote/Local filters
    council_member_filters: Optional[Dict[int, str]] = None
    chairman_filter: Optional[str] = None
    search_query_filter: Optional[str] = None

    # Temperature Settings
    council_temperature: Optional[float] = None
    chairman_temperature: Optional[float] = None
    stage2_temperature: Optional[float] = None

    # Execution Mode
    execution_mode: Optional[str] = None

    # System Prompts
    stage1_prompt: Optional[str] = None
    stage2_prompt: Optional[str] = None
    stage3_prompt: Optional[str] = None



class TestTavilyRequest(BaseModel):
    """Request to test Tavily API key."""
    api_key: str | None = None


@app.get("/api/settings")
async def get_app_settings():
    """Get current application settings."""
    settings = get_settings()
    return {
        "search_provider": settings.search_provider,
        "search_keyword_extraction": settings.search_keyword_extraction,
        "ollama_base_url": settings.ollama_base_url,
        "full_content_results": settings.full_content_results,

        # Custom Endpoint
        "custom_endpoint_name": settings.custom_endpoint_name,
        "custom_endpoint_url": settings.custom_endpoint_url,
        # Don't send the API key to frontend for security

        # API Key Status
        "serper_api_key_set": bool(settings.serper_api_key),
        "tavily_api_key_set": bool(settings.tavily_api_key),
        "brave_api_key_set": bool(settings.brave_api_key),
        "openrouter_api_key_set": bool(settings.openrouter_api_key),
        "requesty_api_key_set": bool(settings.requesty_api_key),
        "openai_api_key_set": bool(settings.openai_api_key),
        "anthropic_api_key_set": bool(settings.anthropic_api_key),
        "google_api_key_set": bool(settings.google_api_key),
        "mistral_api_key_set": bool(settings.mistral_api_key),
        "deepseek_api_key_set": bool(settings.deepseek_api_key),
        "groq_api_key_set": bool(settings.groq_api_key),
        "custom_endpoint_api_key_set": bool(settings.custom_endpoint_api_key),

        # Enabled Providers
        "enabled_providers": settings.enabled_providers,
        "direct_provider_toggles": settings.direct_provider_toggles,

        # Council Configuration (unified)
        "council_models": settings.council_models,
        "chairman_model": settings.chairman_model,
        
        # Remote/Local filters
        "council_member_filters": settings.council_member_filters,
        "chairman_filter": settings.chairman_filter,
        "search_query_filter": settings.search_query_filter,

        # Temperature Settings
        "council_temperature": settings.council_temperature,
        "chairman_temperature": settings.chairman_temperature,
        "stage2_temperature": settings.stage2_temperature,

        # Prompts
        "stage1_prompt": settings.stage1_prompt,
        "stage2_prompt": settings.stage2_prompt,
        "stage3_prompt": settings.stage3_prompt,
    }



@app.get("/api/settings/defaults")
async def get_default_settings():
    """Get default model settings."""
    from .prompts import (
        STAGE1_PROMPT_DEFAULT,
        STAGE2_PROMPT_DEFAULT,
        STAGE3_PROMPT_DEFAULT,
        TITLE_PROMPT_DEFAULT
    )
    from .settings import DEFAULT_ENABLED_PROVIDERS
    return {
        "council_models": DEFAULT_COUNCIL_MODELS,
        "chairman_model": DEFAULT_CHAIRMAN_MODEL,
        "enabled_providers": DEFAULT_ENABLED_PROVIDERS,
        "stage1_prompt": STAGE1_PROMPT_DEFAULT,
        "stage2_prompt": STAGE2_PROMPT_DEFAULT,
        "stage3_prompt": STAGE3_PROMPT_DEFAULT,
    }


@app.put("/api/settings")
async def update_app_settings(request: UpdateSettingsRequest):
    """Update application settings."""
    updates = {}

    if request.search_provider is not None:
        # Validate provider
        try:
            provider = SearchProvider(request.search_provider)
            updates["search_provider"] = provider
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search provider. Must be one of: {[p.value for p in SearchProvider]}"
            )

    if request.search_keyword_extraction is not None:
        if request.search_keyword_extraction not in ["direct", "yake"]:
             raise HTTPException(
                status_code=400,
                detail="Invalid keyword extraction mode. Must be 'direct' or 'yake'"
            )
        updates["search_keyword_extraction"] = request.search_keyword_extraction

    if request.ollama_base_url is not None:
        updates["ollama_base_url"] = request.ollama_base_url

    # Custom endpoint
    if request.custom_endpoint_name is not None:
        updates["custom_endpoint_name"] = request.custom_endpoint_name
    if request.custom_endpoint_url is not None:
        updates["custom_endpoint_url"] = request.custom_endpoint_url
    if request.custom_endpoint_api_key is not None:
        updates["custom_endpoint_api_key"] = request.custom_endpoint_api_key

    if request.full_content_results is not None:
        # Validate range
        if request.full_content_results < 0 or request.full_content_results > 10:
            raise HTTPException(
                status_code=400,
                detail="full_content_results must be between 0 and 10"
            )
        updates["full_content_results"] = request.full_content_results

    # Prompt updates
    if request.stage1_prompt is not None:
        updates["stage1_prompt"] = request.stage1_prompt
    if request.stage2_prompt is not None:
        updates["stage2_prompt"] = request.stage2_prompt
    if request.stage3_prompt is not None:
        updates["stage3_prompt"] = request.stage3_prompt

    if request.serper_api_key is not None:
        updates["serper_api_key"] = request.serper_api_key
        # Also set in environment for immediate use
        if request.serper_api_key:
            os.environ["SERPER_API_KEY"] = request.serper_api_key

    if request.tavily_api_key is not None:
        updates["tavily_api_key"] = request.tavily_api_key
        # Also set in environment for immediate use
        if request.tavily_api_key:
            os.environ["TAVILY_API_KEY"] = request.tavily_api_key

    if request.brave_api_key is not None:
        updates["brave_api_key"] = request.brave_api_key
        # Also set in environment for immediate use
        if request.brave_api_key:
            os.environ["BRAVE_API_KEY"] = request.brave_api_key

    if request.openrouter_api_key is not None:
        updates["openrouter_api_key"] = request.openrouter_api_key
    if request.requesty_api_key is not None:
        updates["requesty_api_key"] = request.requesty_api_key

    # Direct Provider Keys
    if request.openai_api_key is not None:
        updates["openai_api_key"] = request.openai_api_key
    if request.anthropic_api_key is not None:
        updates["anthropic_api_key"] = request.anthropic_api_key
    if request.google_api_key is not None:
        updates["google_api_key"] = request.google_api_key
    if request.mistral_api_key is not None:
        updates["mistral_api_key"] = request.mistral_api_key
    if request.deepseek_api_key is not None:
        updates["deepseek_api_key"] = request.deepseek_api_key
    if request.groq_api_key is not None:
        updates["groq_api_key"] = request.groq_api_key

    # Enabled Providers
    if request.enabled_providers is not None:
        updates["enabled_providers"] = request.enabled_providers

    if request.direct_provider_toggles is not None:
        updates["direct_provider_toggles"] = request.direct_provider_toggles

    # Council Configuration (unified)
    if request.council_models is not None:
        # Validate that at least two models are selected
        if len(request.council_models) < 2:
            raise HTTPException(
                status_code=400,
                detail="At least two council models must be selected"
            )
        if len(request.council_models) > 12:
            raise HTTPException(
                status_code=400,
                detail="Maximum of 12 council models allowed"
            )
        updates["council_models"] = request.council_models

    if request.chairman_model is not None:
        updates["chairman_model"] = request.chairman_model
        
    # Remote/Local filters
    if request.council_member_filters is not None:
        updates["council_member_filters"] = request.council_member_filters
    if request.chairman_filter is not None:
        updates["chairman_filter"] = request.chairman_filter
    if request.search_query_filter is not None:
        updates["search_query_filter"] = request.search_query_filter

    # Temperature Settings
    if request.council_temperature is not None:
        updates["council_temperature"] = request.council_temperature
    if request.chairman_temperature is not None:
        updates["chairman_temperature"] = request.chairman_temperature
    if request.stage2_temperature is not None:
        updates["stage2_temperature"] = request.stage2_temperature

    # Prompts   # Execution Mode
    if request.execution_mode is not None:
        valid_modes = ["chat_only", "chat_ranking", "full"]
        if request.execution_mode not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid execution_mode. Must be one of: {valid_modes}"
            )
        updates["execution_mode"] = request.execution_mode

    if updates:
        settings = update_settings(**updates)
    else:
        settings = get_settings()

    return {
        "search_provider": settings.search_provider,
        "search_keyword_extraction": settings.search_keyword_extraction,
        "ollama_base_url": settings.ollama_base_url,
        "full_content_results": settings.full_content_results,

        # Custom Endpoint
        "custom_endpoint_name": settings.custom_endpoint_name,
        "custom_endpoint_url": settings.custom_endpoint_url,

        # API Key Status
        "serper_api_key_set": bool(settings.serper_api_key),
        "tavily_api_key_set": bool(settings.tavily_api_key),
        "brave_api_key_set": bool(settings.brave_api_key),
        "openrouter_api_key_set": bool(settings.openrouter_api_key),
        "requesty_api_key_set": bool(settings.requesty_api_key),
        "openai_api_key_set": bool(settings.openai_api_key),
        "anthropic_api_key_set": bool(settings.anthropic_api_key),
        "google_api_key_set": bool(settings.google_api_key),
        "mistral_api_key_set": bool(settings.mistral_api_key),
        "deepseek_api_key_set": bool(settings.deepseek_api_key),
        "groq_api_key_set": bool(settings.groq_api_key),
        "custom_endpoint_api_key_set": bool(settings.custom_endpoint_api_key),

        # Enabled Providers
        "enabled_providers": settings.enabled_providers,
        "direct_provider_toggles": settings.direct_provider_toggles,

        # Council Configuration (unified)
        "council_models": settings.council_models,
        "chairman_model": settings.chairman_model,

        # Remote/Local filters
        "council_member_filters": settings.council_member_filters,
        "chairman_filter": settings.chairman_filter,

        # Prompts
        "stage1_prompt": settings.stage1_prompt,
        "stage2_prompt": settings.stage2_prompt,
        "stage3_prompt": settings.stage3_prompt,
    }


@app.get("/api/models/direct")
async def get_direct_models():
    """Get available models from all configured direct providers."""
    all_models = []
    
    # Iterate over all providers
    for provider_id, provider in PROVIDERS.items():
        # Skip OpenRouter, Requesty, and Ollama as they are handled separately
        if provider_id in ["openrouter", "requesty", "ollama", "hybrid"]:
            continue
            
        try:
            # Fetch models from provider
            models = await provider.get_models()
            all_models.extend(models)
        except Exception as e:
            print(f"Error fetching models for {provider_id}: {e}")
            
    return all_models


@app.post("/api/settings/test-tavily")
async def test_tavily_api(request: TestTavilyRequest):
    """Test Tavily API key with a simple search."""
    import httpx
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": request.api_key or settings.tavily_api_key,
                    "query": "test",
                    "max_results": 1,
                    "search_depth": "basic",
                },
            )

            if response.status_code == 200:
                return {"success": True, "message": "API key is valid"}
            elif response.status_code == 401:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"API error: {response.status_code}"}

    except httpx.TimeoutException:
        return {"success": False, "message": "Request timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


class TestBraveRequest(BaseModel):
    """Request to test Brave API key."""
    api_key: str | None = None


@app.post("/api/settings/test-brave")
async def test_brave_api(request: TestBraveRequest):
    """Test Brave API key with a simple search."""
    import httpx
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test", "count": 1},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": request.api_key or settings.brave_api_key,
                },
            )

            if response.status_code == 200:
                return {"success": True, "message": "API key is valid"}
            elif response.status_code == 401 or response.status_code == 403:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"API error: {response.status_code}"}

    except httpx.TimeoutException:
        return {"success": False, "message": "Request timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


class TestSerperRequest(BaseModel):
    """Request to test Serper API key."""
    api_key: str | None = None


@app.post("/api/settings/test-serper")
async def test_serper_api(request: TestSerperRequest):
    """Test Serper API key with a simple search."""
    import httpx
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                json={"q": "test", "num": 1},
                headers={
                    "X-API-KEY": request.api_key or settings.serper_api_key,
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                return {"success": True, "message": "API key is valid"}
            elif response.status_code == 401 or response.status_code == 403:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"API error: {response.status_code}"}

    except httpx.TimeoutException:
        return {"success": False, "message": "Request timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


class TestOpenRouterRequest(BaseModel):
    """Request to test OpenRouter API key."""
    api_key: Optional[str] = None


class TestRequestyRequest(BaseModel):
    """Request to test Requesty API key."""
    api_key: Optional[str] = None


class TestProviderRequest(BaseModel):
    """Request to test a specific provider's API key."""
    provider_id: str
    api_key: str


@app.post("/api/settings/test-provider")
async def test_provider_api(request: TestProviderRequest):
    """Test an API key for a specific provider."""
    from .council import PROVIDERS
    from .settings import get_settings
    
    if request.provider_id not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Invalid provider ID")
        
    api_key = request.api_key
    if not api_key:
        # Try to get from settings
        settings = get_settings()
        # Map provider_id to setting key (e.g. 'openai' -> 'openai_api_key')
        setting_key = f"{request.provider_id}_api_key"
        if hasattr(settings, setting_key):
             api_key = getattr(settings, setting_key)
    
    if not api_key:
         return {"success": False, "message": "No API key provided or configured"}

    provider = PROVIDERS[request.provider_id]
    return await provider.validate_key(api_key)


class TestOllamaRequest(BaseModel):
    """Request to test Ollama connection."""
    base_url: str


@app.get("/api/ollama/tags")
async def get_ollama_tags(base_url: Optional[str] = None):
    """Fetch available models from Ollama."""
    import httpx
    from .config import get_ollama_base_url
    
    if not base_url:
        base_url = get_ollama_base_url()
        
    if base_url.endswith('/'):
        base_url = base_url[:-1]
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            
            if response.status_code != 200:
                return {"models": [], "error": f"Ollama API error: {response.status_code}"}
                
            data = response.json()
            models = []
            for model in data.get("models", []):
                models.append({
                    "id": model.get("name"),
                    "name": model.get("name"),
                    # Ollama doesn't return context length in tags
                    "context_length": None,
                    "is_free": True,
                    "modified_at": model.get("modified_at")
                })
                
            # Sort by modified_at (newest first), fallback to name
            models.sort(key=lambda x: x.get("modified_at", ""), reverse=True)
            return {"models": models}
            
    except httpx.ConnectError:
        return {"models": [], "error": "Could not connect to Ollama. Is it running?"}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/settings/test-ollama")
async def test_ollama_connection(request: TestOllamaRequest):
    """Test connection to Ollama instance."""
    import httpx
    
    base_url = request.base_url
    if base_url.endswith('/'):
        base_url = base_url[:-1]
        
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            
            if response.status_code == 200:
                return {"success": True, "message": "Successfully connected to Ollama"}
            else:
                return {"success": False, "message": f"Ollama API error: {response.status_code}"}
                
    except httpx.ConnectError:
        return {"success": False, "message": "Could not connect to Ollama. Is it running at this URL?"}
    except Exception as e:
        return {"success": False, "message": str(e)}


class TestCustomEndpointRequest(BaseModel):
    """Request to test custom OpenAI-compatible endpoint."""
    name: str
    url: str
    api_key: Optional[str] = None


@app.post("/api/settings/test-custom-endpoint")
async def test_custom_endpoint(request: TestCustomEndpointRequest):
    """Test connection to a custom OpenAI-compatible endpoint."""
    from .providers.custom_openai import CustomOpenAIProvider

    provider = CustomOpenAIProvider()
    return await provider.validate_connection(request.url, request.api_key or "")


@app.get("/api/custom-endpoint/models")
async def get_custom_endpoint_models():
    """Fetch available models from the custom endpoint."""
    from .providers.custom_openai import CustomOpenAIProvider
    from .settings import get_settings

    settings = get_settings()
    if not settings.custom_endpoint_url:
        return {"models": [], "error": "No custom endpoint configured"}

    provider = CustomOpenAIProvider()
    models = await provider.get_models()
    return {"models": models}


@app.get("/api/models")
async def get_openrouter_models():
    """Fetch available models from OpenRouter API."""
    import httpx
    from .config import get_openrouter_api_key

    api_key = get_openrouter_api_key()
    if not api_key:
        return {"models": [], "error": "No OpenRouter API key configured"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code != 200:
                return {"models": [], "error": f"API error: {response.status_code}"}

            data = response.json()
            models = []
            
            # Comprehensive exclusion list for non-text/chat models
            excluded_terms = [
                "embed", "audio", "whisper", "tts", "dall-e", "realtime", 
                "vision-only", "voxtral", "speech", "transcribe", "sora"
            ]

            for model in data.get("data", []):
                mid = model.get("id", "").lower()
                name_lower = model.get("name", "").lower()
                
                if any(term in mid for term in excluded_terms) or any(term in name_lower for term in excluded_terms):
                    continue

                # Extract pricing - free models have 0 cost
                pricing = model.get("pricing", {})
                prompt_price = float(pricing.get("prompt", "0") or "0")
                completion_price = float(pricing.get("completion", "0") or "0")
                is_free = prompt_price == 0 and completion_price == 0

                models.append({
                    "id": f"openrouter:{model.get('id')}",
                    "name": f"{model.get('name', model.get('id'))} [OpenRouter]",
                    "provider": "OpenRouter",
                    "context_length": model.get("context_length"),
                    "is_free": is_free,
                })

            # Sort by name
            models.sort(key=lambda x: x["name"].lower())
            return {"models": models}

    except httpx.TimeoutException:
        return {"models": [], "error": "Request timed out"}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.get("/api/models/requesty")
async def get_requesty_models():
    """Fetch available models from Requesty API."""
    from . import requesty as requesty_module

    models = await requesty_module.fetch_models()
    # Prefix ids for frontend routing
    for m in models:
        raw_id = m["id"]
        m["id"] = f"requesty:{raw_id}"
        m["name"] = (m.get("name") or raw_id) + " [Requesty]"
    models.sort(key=lambda x: (x.get("name") or "").lower())
    return {"models": models}


@app.post("/api/settings/test-openrouter")
async def test_openrouter_api(request: TestOpenRouterRequest):
    """Test OpenRouter API key with a simple request."""
    import httpx
    from .config import get_openrouter_api_key

    # Use provided key or fall back to saved key
    api_key = request.api_key if request.api_key else get_openrouter_api_key()
    
    if not api_key:
        return {"success": False, "message": "No API key provided or configured"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
            )

            if response.status_code == 200:
                return {"success": True, "message": "API key is valid"}
            elif response.status_code == 401:
                return {"success": False, "message": "Invalid API key"}
            else:
                return {"success": False, "message": f"API error: {response.status_code}"}

    except httpx.TimeoutException:
        return {"success": False, "message": "Request timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/openrouter/generation")
async def get_openrouter_generation(id: str):
    """Fetch OpenRouter generation usage/cost metadata by generation ID."""
    from . import openrouter as openrouter_client

    result = await openrouter_client.fetch_generation(id)
    if result.get("error"):
        return {"success": False, "error": result.get("error_message", "Unknown error")}
    return {"success": True, "data": result.get("data")}


@app.post("/api/settings/test-requesty")
async def test_requesty_api(request: TestRequestyRequest):
    """Test Requesty API key with a simple request."""
    from .config import get_requesty_api_key

    api_key = request.api_key if request.api_key else get_requesty_api_key()
    if not api_key:
        return {"success": False, "message": "No API key provided or configured"}

    provider = PROVIDERS["requesty"]
    result = await provider.validate_key(api_key)
    return result


# SPA fallback: serve frontend build so refresh/direct URLs don't 404
_SPA_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve built frontend (index.html or static file). Only used when no API route matched."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if not _SPA_DIST.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # Try to serve a static file (e.g. /assets/foo.js, /vite.svg)
    file_path = _SPA_DIST / full_path
    if full_path and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(_SPA_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)