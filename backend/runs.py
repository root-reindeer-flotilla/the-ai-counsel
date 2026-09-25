"""In-memory run manager for resumable council deliberations.

Runs execute in a background task, independent of any HTTP request, and keep
their events so a client can re-attach with ``from_event``. Runs do not survive
a backend restart (spec D7).

This module must not import ``main``: ``main`` passes its ``_active_runs``
progress map in as ``RunManager(progress=...)``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from . import storage
from .config import get_chairman_model, get_council_models
from .costs import build_council_cost_report
from .council import (
    _prompt_safe_field,
    calculate_aggregate_rankings,
    generate_conversation_title,
    generate_search_query,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .credentials import get_api_key
from .documents import build_effective_query, to_attachment_metadata, validate_documents_for_request
from .model_preflight import build_preflight_error_message, preflight_models
from .search import SearchProvider, perform_web_search
from .settings import get_settings


VALID_EXECUTION_MODES = ["chat_only", "chat_ranking", "full"]
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
RANKING_MODES = ("chat_ranking", "full")
STAGE1_ALL_FAILED_MESSAGE = (
    "All models failed to respond in Stage 1, likely due to rate limits or API errors. "
    "Please try again or adjust your model selection."
)

# Search providers that need an API key in the environment, as in main._apply_search_env.
_SEARCH_API_KEY_ENV = {
    SearchProvider.SERPER: ("serper", "SERPER_API_KEY"),
    SearchProvider.TAVILY: ("tavily", "TAVILY_API_KEY"),
    SearchProvider.BRAVE: ("brave", "BRAVE_API_KEY"),
    SearchProvider.TINYFISH: ("tinyfish", "TINYFISH_API_KEY"),
}


def _apply_search_env(settings: Any, provider_override: Optional[str] = None) -> SearchProvider:
    """Set the env var for the active search provider and return it.

    Mirrors main._apply_search_env; kept here because runs.py must not import main.
    """
    provider = SearchProvider(provider_override or settings.search_provider)
    key_spec = _SEARCH_API_KEY_ENV.get(provider)
    if key_spec:
        key = get_api_key(key_spec[0])
        if key:
            os.environ[key_spec[1]] = key
    return provider


def build_chat_history(conversation: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract prior turns as [{role, content}, ...] for multi-turn context.

    Assistant turns use the chairman synthesis, else the first successful
    Stage 1 response, always without thinking blocks. main._build_chat_history
    delegates here.
    """
    history: List[Dict[str, str]] = []
    for msg in conversation.get("messages", []):
        role = msg.get("role")
        if role == "user":
            history.append({"role": "user", "content": msg.get("content") or ""})
        elif role == "assistant":
            content = ""
            if msg.get("stage3") and msg["stage3"].get("response"):
                content = _prompt_safe_field(msg["stage3"], "response")
            elif msg.get("stage1"):
                first_success = next((r for r in msg["stage1"] if not r.get("error")), msg["stage1"][0])
                content = _prompt_safe_field(first_success, "response")
            if content:
                history.append({"role": "assistant", "content": content})
    return history


@dataclass
class RunState:
    run_id: str
    conversation_id: str
    content: str
    web_search: bool
    execution_mode: str
    is_first_message: bool
    # Upstream SendMessageRequest options.
    search_provider: Optional[str] = None
    council_models: Optional[List[str]] = None
    chairman_model: Optional[str] = None
    history: List[Dict[str, str]] = field(default_factory=list)
    # What the stages see: content plus any attached documents.
    query: str = ""
    status: str = "queued"
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    stage1_results: List[Dict[str, Any]] = field(default_factory=list)
    stage2_results: List[Dict[str, Any]] = field(default_factory=list)
    stage3_result: Optional[Dict[str, Any]] = None
    label_to_model: Dict[str, str] = field(default_factory=dict)
    stage2_label_maps_by_evaluator: Dict[str, Dict[str, str]] = field(default_factory=dict)
    stage2_candidate_maps_by_evaluator: Dict[str, Dict[str, str]] = field(default_factory=dict)
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

    def __init__(self, progress: Optional[Dict[str, Dict[str, Any]]] = None):
        self._runs: Dict[str, RunState] = {}
        self._active_by_conversation: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        # main's _active_runs map (conversation_id -> progress entry), so that
        # GET /api/conversations/{id}/progress also reports background runs.
        self._progress: Dict[str, Dict[str, Any]] = progress if progress is not None else {}

    async def start_run(
        self,
        conversation_id: str,
        content: str,
        web_search: bool = False,
        execution_mode: str = "full",
        search_provider: Optional[str] = None,
        council_models: Optional[List[str]] = None,
        chairman_model: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
    ) -> RunState:
        """Store the user message and start a background run.

        Raises ValueError (bad mode or documents), LookupError (no such
        conversation) or RuntimeError (a run or stream is already active).
        ``history`` defaults to the conversation's turns before this message.
        """
        if execution_mode not in VALID_EXECUTION_MODES:
            raise ValueError(f"Invalid execution_mode. Must be one of: {VALID_EXECUTION_MODES}")
        validated_documents = validate_documents_for_request(documents)  # DocumentError is a ValueError
        query = build_effective_query(content, validated_documents)
        attachments = to_attachment_metadata(validated_documents)

        async with self._lock:
            conversation = storage.get_conversation(conversation_id)
            if conversation is None:
                raise LookupError("Conversation not found")

            existing_run_id = self._active_by_conversation.get(conversation_id)
            if existing_run_id:
                existing = self._runs.get(existing_run_id)
                if existing and existing.status not in TERMINAL_STATUSES:
                    raise RuntimeError("Conversation already has an active run")
            if conversation_id in self._progress:
                # An upstream /message/stream or advisor debate is running here.
                raise RuntimeError("Conversation already has an active run")

            is_first_message = len(conversation["messages"]) == 0
            if history is None:
                history = build_chat_history(conversation)
            storage.add_user_message(conversation_id, content, conversation=conversation, attachments=attachments)

            run = RunState(
                run_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                content=content,
                web_search=bool(web_search or search_provider),
                execution_mode=execution_mode,
                is_first_message=is_first_message,
                search_provider=search_provider or None,
                council_models=council_models or None,
                chairman_model=chairman_model or None,
                history=history,
                query=query,
            )
            self._runs[run.run_id] = run
            self._active_by_conversation[conversation_id] = run.run_id
            self._start_progress(run)
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
        # A task cancelled before its first step never enters _execute_run's
        # try/finally, so a queued run is left to see cancel_event instead.
        if run.status != "queued" and run.task and not run.task.done():
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
                "stage2_label_maps_by_evaluator": run.stage2_label_maps_by_evaluator,
                "stage2_candidate_maps_by_evaluator": run.stage2_candidate_maps_by_evaluator,
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

    # --- Progress map (main._active_runs) -------------------------------------

    def _start_progress(self, run: RunState) -> None:
        """Register the run in the same shape as main._register_run, plus re-attach info."""
        self._progress[run.conversation_id] = {
            "mode": "council",
            "stage": "initializing",
            "execution_mode": run.execution_mode,
            "progress": {
                "stage1": {"total": 0},
                "stage2": {"total": 0},
            },
            # Live lists: the /progress route counts them as results arrive.
            "stage1_responses": run.stage1_results,
            "stage2_responses": run.stage2_results,
            "run_id": run.run_id,
            "event_count": len(run.events),
        }

    def _progress_entry(self, run: RunState) -> Optional[Dict[str, Any]]:
        entry = self._progress.get(run.conversation_id)
        if entry is None or entry.get("run_id") != run.run_id:
            return None
        return entry

    def _set_stage(self, run: RunState, stage: str, **updates: Any) -> None:
        """Set the progress stage; ``stage1_total``/``stage2_total`` set totals, other keys are stored."""
        entry = self._progress_entry(run)
        if entry is None:
            return
        entry["stage"] = stage
        for key, value in updates.items():
            if key in ("stage1_total", "stage2_total"):
                entry["progress"][key.split("_")[0]]["total"] = value
            else:
                entry[key] = value

    def _finish_progress(self, run: RunState) -> None:
        if self._progress_entry(run) is not None:
            self._progress.pop(run.conversation_id, None)

    # --------------------------------------------------------------------------

    async def _emit(self, run: RunState, event: Dict[str, Any]):
        run.events.append(event)
        entry = self._progress_entry(run)
        if entry is not None:
            entry["event_count"] = len(run.events)
        async with run.event_condition:
            run.event_condition.notify_all()

    async def _fail_with_error_message(self, run: RunState, message: str):
        """End the run the way upstream records a failed turn: an error message, no stages."""
        run.error_message = message
        run.status = "failed"
        storage.add_error_message(run.conversation_id, message)
        run.assistant_message_saved = True
        await self._emit(run, {"type": "error", "message": message})

    def _raise_if_cancelled(self, run: RunState):
        if run.cancel_event.is_set():
            raise asyncio.CancelledError("Run cancelled")

    async def _save_assistant_message(self, run: RunState, partial: bool = False):
        """Save through upstream's storage.add_assistant_message, with its metadata keys."""
        if run.assistant_message_saved:
            return
        metadata: Dict[str, Any] = {
            "execution_mode": run.execution_mode,
            "cost_report": build_council_cost_report(
                run.stage1_results,
                run.stage2_results,
                run.stage3_result,
            ),
        }
        if partial:
            metadata["incomplete"] = True
        if run.execution_mode in RANKING_MODES:
            metadata["label_to_model"] = run.label_to_model
            metadata["stage2_label_maps_by_evaluator"] = run.stage2_label_maps_by_evaluator
            metadata["stage2_candidate_maps_by_evaluator"] = run.stage2_candidate_maps_by_evaluator
            metadata["aggregate_rankings"] = run.aggregate_rankings
            metadata["ranking_diagnostics"] = run.ranking_diagnostics
            metadata["generation_time_ms"] = run.generation_time_ms
            metadata["generation_time_seconds"] = run.generation_time_seconds
        if run.search_context:
            metadata["search_context"] = run.search_context
            metadata["web_search"] = True
        if run.search_query:
            metadata["search_query"] = run.search_query
        if run.aborted:
            metadata["aborted"] = True
        if run.error_message:
            metadata["error_message"] = run.error_message

        conversation = storage.get_conversation(run.conversation_id)
        if not conversation:
            return

        stage2 = None
        if run.execution_mode in RANKING_MODES and (run.stage2_results or not partial):
            stage2 = run.stage2_results
        storage.add_assistant_message(
            run.conversation_id,
            run.stage1_results,
            stage2,
            run.stage3_result if run.execution_mode == "full" else None,
            metadata,
            conversation=conversation,
        )

        # Mark as aborted on persisted message if needed.
        if run.aborted:
            last = conversation["messages"][-1]
            last["aborted"] = True
            storage.save_conversation(conversation)

        run.assistant_message_saved = True

    async def _execute_run(self, run: RunState):
        title_task = None
        stage1_started_at = None
        stage1_completed_at = None
        stage2_started_at = None
        stage2_completed_at = None

        def _tokens_for_log(value: Any) -> str:
            return str(value) if isinstance(value, int) else "null"

        def _usage_tokens_for_log(usage: Any, keys: List[str]) -> Optional[int]:
            if not isinstance(usage, dict):
                return None
            for key in keys:
                token_value = usage.get(key)
                if isinstance(token_value, int):
                    return token_value
            return None

        def _cost_for_log(usage: Any) -> str:
            if not isinstance(usage, dict):
                return "null"
            cost = usage.get("cost")
            if cost is None:
                return "null"
            try:
                return str(float(cost))
            except (TypeError, ValueError):
                return "null"

        def _sum_cost_for_log(results: List[Dict[str, Any]], usage_key: str) -> Optional[float]:
            total = 0.0
            has_cost = False
            for result in results:
                usage = result.get(usage_key)
                if not isinstance(usage, dict):
                    continue
                cost = usage.get("cost")
                if cost is None:
                    continue
                try:
                    total += float(cost)
                    has_cost = True
                except (TypeError, ValueError):
                    continue
            return total if has_cost else None

        def _sum_tokens_for_log(results: List[Dict[str, Any]], tokens_key: str) -> Optional[int]:
            total = 0
            has_tokens = False
            for result in results:
                tokens = result.get(tokens_key)
                if isinstance(tokens, int) and tokens > 0:
                    total += tokens
                    has_tokens = True
            return total if has_tokens else None

        def _sum_usage_tokens_for_log(
            results: List[Dict[str, Any]],
            usage_key: str,
            token_keys: List[str],
        ) -> Optional[int]:
            total = 0
            has_tokens = False
            for result in results:
                usage = result.get(usage_key)
                token_count = _usage_tokens_for_log(usage, token_keys)
                if isinstance(token_count, int) and token_count >= 0:
                    total += token_count
                    has_tokens = True
            return total if has_tokens else None

        try:
            self._raise_if_cancelled(run)
            run.status = "running"
            run.started_at = time.perf_counter()

            # Same checks as upstream's /message/stream: the council, plus the chairman in full mode.
            preflight_targets = list(run.council_models or get_council_models())
            if run.execution_mode == "full":
                preflight_targets.append(run.chairman_model or get_chairman_model())
            preflight_result = await preflight_models(preflight_targets, conversation_id=run.conversation_id)
            if not preflight_result.ok:
                await self._fail_with_error_message(run, build_preflight_error_message(preflight_result))
                return
            self._raise_if_cancelled(run)

            if run.is_first_message:
                title_task = asyncio.create_task(
                    generate_conversation_title(run.content, conversation_id=run.conversation_id)
                )

            if run.web_search:
                settings = get_settings()
                provider = _apply_search_env(settings, run.search_provider)
                self._set_stage(run, "search")
                await self._emit(run, {"type": "search_start", "data": {"provider": provider.value}})
                # LLM query generation only when selected and not DuckDuckGo (upstream's rule).
                if settings.search_keyword_extraction == "llm" and provider != SearchProvider.DUCKDUCKGO:
                    run.search_query = await generate_search_query(
                        run.content,
                        conversation_id=run.conversation_id,
                    )
                else:
                    run.search_query = run.content
                self._raise_if_cancelled(run)
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
            self._set_stage(run, "stage1")
            await self._emit(run, {"type": "stage1_start"})
            async for item in stage1_collect_responses(
                run.query,
                run.search_context,
                None,
                models_override=run.council_models,
                history=run.history,
                conversation_id=run.conversation_id,
            ):
                self._raise_if_cancelled(run)
                if isinstance(item, int):
                    run.stage1_total_models = item
                    self._set_stage(run, "stage1", stage1_total=item)
                    await self._emit(run, {"type": "stage1_init", "total": item})
                    continue
                run.stage1_results.append(item)
                stage1_usage = item.get("stage1_usage")
                stage1_input_tokens_value = _usage_tokens_for_log(stage1_usage, ["input_tokens", "prompt_tokens"])
                stage1_output_tokens_value = _usage_tokens_for_log(stage1_usage, ["output_tokens", "completion_tokens"])
                stage1_tokens_value = item.get("stage1_total_tokens")
                print(
                    f"Stage 1 Progress: {len(run.stage1_results)}/{run.stage1_total_models} - "
                    f"{item.get('model', 'unknown')} | "
                    f"{_tokens_for_log(stage1_input_tokens_value)} | "
                    f"{_tokens_for_log(stage1_output_tokens_value)} | "
                    f"{_tokens_for_log(stage1_tokens_value)} | "
                    f"{_cost_for_log(stage1_usage)}"
                )
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
            stage1_total_cost = _sum_cost_for_log(run.stage1_results, "stage1_usage")
            stage1_total_input_tokens = _sum_usage_tokens_for_log(
                run.stage1_results,
                "stage1_usage",
                ["input_tokens", "prompt_tokens"],
            )
            stage1_total_output_tokens = _sum_usage_tokens_for_log(
                run.stage1_results,
                "stage1_usage",
                ["output_tokens", "completion_tokens"],
            )
            stage1_total_tokens = _sum_tokens_for_log(run.stage1_results, "stage1_total_tokens")
            print(
                "Stage 1 Totals: "
                f"{str(stage1_total_input_tokens) if stage1_total_input_tokens is not None else 'null'} | "
                f"{str(stage1_total_output_tokens) if stage1_total_output_tokens is not None else 'null'} | "
                f"{str(stage1_total_tokens) if stage1_total_tokens is not None else 'null'} | "
                f"{str(stage1_total_cost) if stage1_total_cost is not None else 'null'}"
            )
            await self._emit(run, {"type": "stage1_complete", "data": run.stage1_results})

            if not any(r for r in run.stage1_results if not r.get("error")):
                await self._fail_with_error_message(run, STAGE1_ALL_FAILED_MESSAGE)
                return

            if run.execution_mode in RANKING_MODES:
                stage2_started_at = time.perf_counter()
                self._set_stage(run, "stage2")
                await self._emit(run, {"type": "stage2_start"})
                # Stage 2 gets the same stage1_results list that Stage 3 gets below.
                async for item in stage2_collect_rankings(
                    run.query,
                    run.stage1_results,
                    run.search_context,
                    None,
                    conversation_id=run.conversation_id,
                ):
                    self._raise_if_cancelled(run)
                    if isinstance(item, dict) and not item.get("model"):
                        # Upstream contract (spec D3): the first item is the flat global label map.
                        run.label_to_model = item
                        run.stage2_total_models = len(item)
                        self._set_stage(run, "stage2", stage2_total=run.stage2_total_models)
                        await self._emit(run, {"type": "stage2_init", "total": run.stage2_total_models})
                        continue
                    run.stage2_results.append(item)
                    # Snapshot convenience only, keyed by model: a model sitting in the
                    # council twice collapses here. Each result's own stage2_label_map
                    # is authoritative, and aggregation below reads that.
                    evaluator = item.get("model")
                    if isinstance(item.get("stage2_label_map"), dict):
                        run.stage2_label_maps_by_evaluator[evaluator] = item["stage2_label_map"]
                    if isinstance(item.get("stage2_candidate_label_map"), dict):
                        run.stage2_candidate_maps_by_evaluator[evaluator] = item["stage2_candidate_label_map"]
                    stage2_usage = item.get("stage2_usage")
                    stage2_input_tokens_value = _usage_tokens_for_log(stage2_usage, ["input_tokens", "prompt_tokens"])
                    stage2_output_tokens_value = _usage_tokens_for_log(stage2_usage, ["output_tokens", "completion_tokens"])
                    stage2_tokens_value = item.get("stage2_total_tokens")
                    print(
                        f"Stage 2 Progress: {len(run.stage2_results)}/{run.stage2_total_models} - "
                        f"{item.get('model', 'unknown')} | "
                        f"{_tokens_for_log(stage2_input_tokens_value)} | "
                        f"{_tokens_for_log(stage2_output_tokens_value)} | "
                        f"{_tokens_for_log(stage2_tokens_value)} | "
                        f"{_cost_for_log(stage2_usage)}"
                    )
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
                stage2_total_cost = _sum_cost_for_log(run.stage2_results, "stage2_usage")
                stage2_total_input_tokens = _sum_usage_tokens_for_log(
                    run.stage2_results,
                    "stage2_usage",
                    ["input_tokens", "prompt_tokens"],
                )
                stage2_total_output_tokens = _sum_usage_tokens_for_log(
                    run.stage2_results,
                    "stage2_usage",
                    ["output_tokens", "completion_tokens"],
                )
                stage2_total_tokens = _sum_tokens_for_log(run.stage2_results, "stage2_total_tokens")
                print(
                    "Stage 2 Totals: "
                    f"{str(stage2_total_input_tokens) if stage2_total_input_tokens is not None else 'null'} | "
                    f"{str(stage2_total_output_tokens) if stage2_total_output_tokens is not None else 'null'} | "
                    f"{str(stage2_total_tokens) if stage2_total_tokens is not None else 'null'} | "
                    f"{str(stage2_total_cost) if stage2_total_cost is not None else 'null'}"
                )
                combined_stage12_cost = None
                if stage1_total_cost is not None or stage2_total_cost is not None:
                    combined_stage12_cost = float(stage1_total_cost or 0) + float(stage2_total_cost or 0)
                combined_stage12_input_tokens = None
                if stage1_total_input_tokens is not None or stage2_total_input_tokens is not None:
                    combined_stage12_input_tokens = int(stage1_total_input_tokens or 0) + int(stage2_total_input_tokens or 0)
                combined_stage12_output_tokens = None
                if stage1_total_output_tokens is not None or stage2_total_output_tokens is not None:
                    combined_stage12_output_tokens = int(stage1_total_output_tokens or 0) + int(stage2_total_output_tokens or 0)
                combined_stage12_tokens = None
                if stage1_total_tokens is not None or stage2_total_tokens is not None:
                    combined_stage12_tokens = int(stage1_total_tokens or 0) + int(stage2_total_tokens or 0)
                print(
                    "Stage 1+2 Totals: "
                    f"{str(combined_stage12_input_tokens) if combined_stage12_input_tokens is not None else 'null'} | "
                    f"{str(combined_stage12_output_tokens) if combined_stage12_output_tokens is not None else 'null'} | "
                    f"{str(combined_stage12_tokens) if combined_stage12_tokens is not None else 'null'} | "
                    f"{str(combined_stage12_cost) if combined_stage12_cost is not None else 'null'}"
                )
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
                            "stage2_label_maps_by_evaluator": run.stage2_label_maps_by_evaluator,
                            "stage2_candidate_maps_by_evaluator": run.stage2_candidate_maps_by_evaluator,
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
                self._set_stage(run, "stage3")
                await self._emit(run, {"type": "stage3_start"})
                self._raise_if_cancelled(run)
                run.stage3_result = await stage3_synthesize_final(
                    run.query,
                    run.stage1_results,
                    run.stage2_results,
                    run.search_context,
                    chairman_override=run.chairman_model,
                    conversation_id=run.conversation_id,
                )
                self._set_stage(run, "stage3", stage3_response=run.stage3_result)
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
            cost_report = build_council_cost_report(run.stage1_results, run.stage2_results, run.stage3_result)
            await self._emit(run, {"type": "complete", "metadata": {"cost_report": cost_report}})
        except asyncio.CancelledError:
            run.aborted = True
            run.status = "cancelled"
            if title_task:
                try:
                    run.title = await asyncio.wait_for(title_task, timeout=2.0)
                    storage.update_conversation_title(run.conversation_id, run.title)
                except Exception:
                    pass
            try:
                await self._save_assistant_message(run, partial=True)
            except Exception as save_exc:
                print(f"Could not save partial results for run {run.run_id}: {save_exc}")
            await self._emit(run, {"type": "cancelled"})
        except Exception as exc:
            run.error_message = str(exc) or exc.__class__.__name__
            run.status = "failed"
            try:
                if run.stage1_results:
                    await self._save_assistant_message(run, partial=True)
                elif not run.assistant_message_saved:
                    storage.add_error_message(run.conversation_id, f"Error: {run.error_message}")
                    run.assistant_message_saved = True
            except Exception as save_exc:
                print(f"Could not save failed run {run.run_id}: {save_exc}")
            await self._emit(run, {"type": "error", "message": run.error_message})
        finally:
            run.ended_at = time.perf_counter()
            async with self._lock:
                active_run_id = self._active_by_conversation.get(run.conversation_id)
                if active_run_id == run.run_id:
                    self._active_by_conversation.pop(run.conversation_id, None)
                self._finish_progress(run)
            async with run.event_condition:
                run.event_condition.notify_all()
