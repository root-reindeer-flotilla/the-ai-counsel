"""In-memory run manager for resumable council deliberations."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from . import storage
from .council import (
    calculate_aggregate_rankings,
    generate_conversation_title,
    generate_search_query,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .search import SearchProvider, perform_web_search
from .settings import get_settings


VALID_EXECUTION_MODES = ["chat_only", "chat_ranking", "full"]
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


@dataclass
class RunState:
    run_id: str
    conversation_id: str
    content: str
    web_search: bool
    execution_mode: str
    is_first_message: bool
    status: str = "queued"
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    stage1_results: List[Dict[str, Any]] = field(default_factory=list)
    stage2_results: List[Dict[str, Any]] = field(default_factory=list)
    stage3_result: Optional[Dict[str, Any]] = None
    label_to_model: Dict[str, str] = field(default_factory=dict)
    aggregate_rankings: List[Dict[str, Any]] = field(default_factory=list)
    ranking_diagnostics: Dict[str, Any] = field(default_factory=dict)
    search_context: str = ""
    search_query: str = ""
    generation_time_ms: Optional[int] = None
    generation_time_seconds: Optional[int] = None
    title: Optional[str] = None
    aborted: bool = False
    error_message: Optional[str] = None
    assistant_message_saved: bool = False
    stage1_total_models: int = 0
    stage2_total_models: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    event_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


class RunManager:
    """Tracks and executes deliberation runs independent from HTTP request lifetime."""

    def __init__(self):
        self._runs: Dict[str, RunState] = {}
        self._active_by_conversation: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_run(
        self,
        conversation_id: str,
        content: str,
        web_search: bool = False,
        execution_mode: str = "full",
    ) -> RunState:
        if execution_mode not in VALID_EXECUTION_MODES:
            raise ValueError(f"Invalid execution_mode. Must be one of: {VALID_EXECUTION_MODES}")

        async with self._lock:
            conversation = storage.get_conversation(conversation_id)
            if conversation is None:
                raise LookupError("Conversation not found")

            existing_run_id = self._active_by_conversation.get(conversation_id)
            if existing_run_id:
                existing = self._runs.get(existing_run_id)
                if existing and existing.status not in TERMINAL_STATUSES:
                    raise RuntimeError("Conversation already has an active run")

            is_first_message = len(conversation["messages"]) == 0
            storage.add_user_message(conversation_id, content)

            run = RunState(
                run_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                content=content,
                web_search=web_search,
                execution_mode=execution_mode,
                is_first_message=is_first_message,
            )
            self._runs[run.run_id] = run
            self._active_by_conversation[conversation_id] = run.run_id
            run.task = asyncio.create_task(self._execute_run(run))
            return run

    async def get_active_run_for_conversation(self, conversation_id: str) -> Optional[RunState]:
        run_id = self._active_by_conversation.get(conversation_id)
        if not run_id:
            return None
        run = self._runs.get(run_id)
        if not run:
            return None
        if run.status in TERMINAL_STATUSES:
            return None
        return run

    async def get_run(self, run_id: str) -> Optional[RunState]:
        return self._runs.get(run_id)

    async def cancel_run(self, run_id: str) -> Optional[RunState]:
        run = self._runs.get(run_id)
        if not run:
            return None
        if run.status in TERMINAL_STATUSES:
            return run
        run.cancel_event.set()
        if run.task and not run.task.done():
            run.task.cancel()
        return run

    async def stream_events(self, run_id: str, from_event: int = 0) -> AsyncGenerator[str, None]:
        run = self._runs.get(run_id)
        if not run:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Run not found'})}\n\n"
            return

        idx = max(0, int(from_event))
        while True:
            while idx < len(run.events):
                event = run.events[idx]
                idx += 1
                yield f"data: {json.dumps(event)}\n\n"

            if run.status in TERMINAL_STATUSES and idx >= len(run.events):
                break

            try:
                async with run.event_condition:
                    await asyncio.wait_for(run.event_condition.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                # Keep SSE connection warm for long model calls.
                yield ": ping\n\n"

    async def snapshot(self, run: RunState) -> Dict[str, Any]:
        return {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "status": run.status,
            "content": run.content,
            "web_search": run.web_search,
            "execution_mode": run.execution_mode,
            "stage1_results": run.stage1_results,
            "stage2_results": run.stage2_results,
            "stage3_result": run.stage3_result,
            "metadata": {
                "label_to_model": run.label_to_model,
                "aggregate_rankings": run.aggregate_rankings,
                "ranking_diagnostics": run.ranking_diagnostics,
                "generation_time_ms": run.generation_time_ms,
                "generation_time_seconds": run.generation_time_seconds,
                "search_query": run.search_query,
                "search_context": run.search_context,
                "aborted": run.aborted,
                "error_message": run.error_message,
            },
            "progress": {
                "stage1": {"count": len(run.stage1_results), "total": run.stage1_total_models},
                "stage2": {"count": len(run.stage2_results), "total": run.stage2_total_models},
            },
            "assistant_message_saved": run.assistant_message_saved,
            "event_count": len(run.events),
        }

    async def _emit(self, run: RunState, event: Dict[str, Any]):
        run.events.append(event)
        async with run.event_condition:
            run.event_condition.notify_all()

    def _raise_if_cancelled(self, run: RunState):
        if run.cancel_event.is_set():
            raise asyncio.CancelledError("Run cancelled")

    async def _save_assistant_message(self, run: RunState):
        if run.assistant_message_saved:
            return
        metadata: Dict[str, Any] = {"execution_mode": run.execution_mode}
        if run.execution_mode in ["chat_ranking", "full"]:
            metadata["label_to_model"] = run.label_to_model
            metadata["aggregate_rankings"] = run.aggregate_rankings
            metadata["ranking_diagnostics"] = run.ranking_diagnostics
            metadata["generation_time_ms"] = run.generation_time_ms
            metadata["generation_time_seconds"] = run.generation_time_seconds
        if run.search_context:
            metadata["search_context"] = run.search_context
        if run.search_query:
            metadata["search_query"] = run.search_query
        if run.aborted:
            metadata["aborted"] = True
        if run.error_message:
            metadata["error_message"] = run.error_message

        conversation = storage.get_conversation(run.conversation_id)
        if not conversation:
            return

        storage.add_assistant_message(
            run.conversation_id,
            run.stage1_results,
            run.stage2_results if run.execution_mode in ["chat_ranking", "full"] else None,
            run.stage3_result if run.execution_mode == "full" else None,
            metadata,
        )

        # Mark as aborted on persisted message if needed.
        if run.aborted:
            conversation = storage.get_conversation(run.conversation_id)
            if conversation and conversation["messages"]:
                last = conversation["messages"][-1]
                if last.get("role") == "assistant":
                    last["aborted"] = True
                    storage.save_conversation(conversation)

        run.assistant_message_saved = True

    async def _execute_run(self, run: RunState):
        title_task = None
        stage1_started_at = None
        stage1_completed_at = None
        stage2_started_at = None
        stage2_completed_at = None

        try:
            run.status = "running"
            run.started_at = time.perf_counter()

            if run.is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(run.content))

            if run.web_search:
                settings = get_settings()
                provider = SearchProvider(settings.search_provider)
                if settings.serper_api_key and provider == SearchProvider.SERPER:
                    os.environ["SERPER_API_KEY"] = settings.serper_api_key
                if settings.tavily_api_key and provider == SearchProvider.TAVILY:
                    os.environ["TAVILY_API_KEY"] = settings.tavily_api_key
                if settings.brave_api_key and provider == SearchProvider.BRAVE:
                    os.environ["BRAVE_API_KEY"] = settings.brave_api_key
                await self._emit(run, {"type": "search_start", "data": {"provider": provider.value}})
                run.search_query = generate_search_query(run.content)
                search_result = await perform_web_search(
                    run.search_query,
                    settings.search_result_count,
                    provider,
                    settings.full_content_results,
                    settings.search_keyword_extraction,
                    hybrid_mode=settings.search_hybrid_mode,
                )
                run.search_context = search_result["results"]
                await self._emit(
                    run,
                    {
                        "type": "search_complete",
                        "data": {
                            "search_query": run.search_query,
                            "extracted_query": search_result["extracted_query"],
                            "search_context": run.search_context,
                            "provider": provider.value,
                            "intent": search_result.get("intent", "unknown"),
                        },
                    },
                )
                self._raise_if_cancelled(run)

            stage1_started_at = time.perf_counter()
            await self._emit(run, {"type": "stage1_start"})
            async for item in stage1_collect_responses(run.content, run.search_context, None):
                self._raise_if_cancelled(run)
                if isinstance(item, int):
                    run.stage1_total_models = item
                    await self._emit(run, {"type": "stage1_init", "total": item})
                    continue
                run.stage1_results.append(item)
                await self._emit(
                    run,
                    {
                        "type": "stage1_progress",
                        "data": item,
                        "count": len(run.stage1_results),
                        "total": run.stage1_total_models,
                    },
                )

            stage1_completed_at = time.perf_counter()
            await self._emit(run, {"type": "stage1_complete", "data": run.stage1_results})

            if not any(r for r in run.stage1_results if not r.get("error")):
                run.error_message = (
                    "All models failed to respond in Stage 1, likely due to rate limits or API errors. "
                    "Please try again or adjust your model selection."
                )
                run.status = "failed"
                await self._save_assistant_message(run)
                await self._emit(run, {"type": "error", "message": run.error_message})
                return

            if run.execution_mode in ["chat_ranking", "full"]:
                stage2_started_at = time.perf_counter()
                await self._emit(run, {"type": "stage2_start"})
                async for item in stage2_collect_rankings(run.content, run.stage1_results, run.search_context, None):
                    self._raise_if_cancelled(run)
                    if isinstance(item, dict) and not item.get("model"):
                        run.label_to_model = item
                        run.stage2_total_models = len(item)
                        await self._emit(run, {"type": "stage2_init", "total": run.stage2_total_models})
                        continue
                    run.stage2_results.append(item)
                    await self._emit(
                        run,
                        {
                            "type": "stage2_progress",
                            "data": item,
                            "count": len(run.stage2_results),
                            "total": run.stage2_total_models,
                        },
                    )

                run.aggregate_rankings, run.ranking_diagnostics = calculate_aggregate_rankings(
                    run.stage2_results,
                    run.label_to_model,
                    return_diagnostics=True,
                )
                # Enrich aggregate rows with per-model generation metrics used by the leaderboard UI.
                stage1_times = {
                    item["model"]: item.get("stage1_duration_ms")
                    for item in run.stage1_results
                    if item.get("model")
                }
                stage2_times = {
                    item["model"]: item.get("stage2_duration_ms")
                    for item in run.stage2_results
                    if item.get("model")
                }
                stage1_tokens = {
                    item["model"]: item.get("stage1_total_tokens")
                    for item in run.stage1_results
                    if item.get("model")
                }
                stage2_tokens = {
                    item["model"]: item.get("stage2_total_tokens")
                    for item in run.stage2_results
                    if item.get("model")
                }
                stage1_usage = {
                    item["model"]: item.get("stage1_usage")
                    for item in run.stage1_results
                    if item.get("model")
                }
                stage2_usage = {
                    item["model"]: item.get("stage2_usage")
                    for item in run.stage2_results
                    if item.get("model")
                }
                stage1_response_ids = {
                    item["model"]: item.get("stage1_response_id")
                    for item in run.stage1_results
                    if item.get("model")
                }
                stage2_response_ids = {
                    item["model"]: item.get("stage2_response_id")
                    for item in run.stage2_results
                    if item.get("model")
                }

                enriched_aggregate_rankings = []
                for item in run.aggregate_rankings:
                    model_name = item.get("model")
                    model_stage1_ms = stage1_times.get(model_name) or 0
                    model_stage2_ms = stage2_times.get(model_name) or 0
                    model_total_ms = max(0, int(model_stage1_ms + model_stage2_ms))
                    model_stage1_tokens = stage1_tokens.get(model_name)
                    model_stage2_tokens = stage2_tokens.get(model_name)
                    model_total_tokens = None
                    if isinstance(model_stage1_tokens, int) or isinstance(model_stage2_tokens, int):
                        model_total_tokens = int(model_stage1_tokens or 0) + int(model_stage2_tokens or 0)

                    model_stage1_usage = stage1_usage.get(model_name) if isinstance(stage1_usage.get(model_name), dict) else {}
                    model_stage2_usage = stage2_usage.get(model_name) if isinstance(stage2_usage.get(model_name), dict) else {}
                    stage1_cost = model_stage1_usage.get("cost")
                    stage2_cost = model_stage2_usage.get("cost")
                    generation_total_cost = None
                    try:
                        if stage1_cost is not None or stage2_cost is not None:
                            generation_total_cost = float(stage1_cost or 0) + float(stage2_cost or 0)
                    except (TypeError, ValueError):
                        generation_total_cost = None

                    enriched_aggregate_rankings.append(
                        {
                            **item,
                            "generation_time_ms": model_total_ms,
                            "generation_time_seconds": max(0, int(round(model_total_ms / 1000))),
                            "generation_total_tokens": model_total_tokens,
                            "generation_total_cost": generation_total_cost,
                            "stage1_usage": model_stage1_usage,
                            "stage2_usage": model_stage2_usage,
                            "stage1_response_id": stage1_response_ids.get(model_name),
                            "stage2_response_id": stage2_response_ids.get(model_name),
                        }
                    )
                run.aggregate_rankings = enriched_aggregate_rankings
                stage2_completed_at = time.perf_counter()
                if (
                    stage1_started_at is not None
                    and stage1_completed_at is not None
                    and stage2_started_at is not None
                    and stage2_completed_at is not None
                ):
                    stage1_ms = max(0, int((stage1_completed_at - stage1_started_at) * 1000))
                    stage2_ms = max(0, int((stage2_completed_at - stage2_started_at) * 1000))
                    run.generation_time_ms = stage1_ms + stage2_ms
                    run.generation_time_seconds = max(0, int(round(run.generation_time_ms / 1000)))

                await self._emit(
                    run,
                    {
                        "type": "stage2_complete",
                        "data": run.stage2_results,
                        "metadata": {
                            "label_to_model": run.label_to_model,
                            "aggregate_rankings": run.aggregate_rankings,
                            "ranking_diagnostics": run.ranking_diagnostics,
                            "generation_time_ms": run.generation_time_ms,
                            "generation_time_seconds": run.generation_time_seconds,
                            "search_query": run.search_query,
                            "search_context": run.search_context,
                        },
                    },
                )

            if run.execution_mode == "full":
                await self._emit(run, {"type": "stage3_start"})
                self._raise_if_cancelled(run)
                run.stage3_result = await stage3_synthesize_final(
                    run.content,
                    run.stage1_results,
                    run.stage2_results,
                    run.search_context,
                )
                await self._emit(run, {"type": "stage3_complete", "data": run.stage3_result})

            if title_task:
                try:
                    run.title = await title_task
                    storage.update_conversation_title(run.conversation_id, run.title)
                    await self._emit(run, {"type": "title_complete", "data": {"title": run.title}})
                except Exception:
                    pass

            await self._save_assistant_message(run)
            run.status = "completed"
            await self._emit(run, {"type": "complete"})
        except asyncio.CancelledError:
            run.aborted = True
            run.status = "cancelled"
            if title_task:
                try:
                    run.title = await asyncio.wait_for(title_task, timeout=2.0)
                    storage.update_conversation_title(run.conversation_id, run.title)
                except Exception:
                    pass
            await self._save_assistant_message(run)
            await self._emit(run, {"type": "cancelled"})
        except Exception as exc:
            run.error_message = str(exc)
            run.status = "failed"
            try:
                await self._save_assistant_message(run)
            except Exception:
                pass
            await self._emit(run, {"type": "error", "message": run.error_message})
        finally:
            run.ended_at = time.perf_counter()
            async with self._lock:
                active_run_id = self._active_by_conversation.get(run.conversation_id)
                if active_run_id == run.run_id:
                    self._active_by_conversation.pop(run.conversation_id, None)
            async with run.event_condition:
                run.event_condition.notify_all()
