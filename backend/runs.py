"""In-memory run manager for resumable council deliberations.

Runs execute in a background task, independent of any HTTP request, and keep
their events so a client can re-attach with ``from_event``. Runs do not survive
a backend restart (spec D7).

This module must not import ``main``: ``main`` passes in its ``_active_runs``
progress map and its ``_fetch_search_context`` helper as
``RunManager(progress=..., fetch_search_context=...)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from . import storage
from .config import get_chairman_model, get_council_models
from .costs import build_council_cost_report
from .council import (
    _prompt_safe_field,
    calculate_aggregate_rankings,
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .documents import build_effective_query, to_attachment_metadata, validate_documents_for_request
from .model_preflight import build_preflight_error_message, preflight_models
from .search import SearchProvider
from .settings import get_settings

logger = logging.getLogger(__name__)

VALID_EXECUTION_MODES = ["chat_only", "chat_ranking", "full"]
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
RANKING_MODES = ("chat_ranking", "full")
STAGE1_ALL_FAILED_MESSAGE = (
    "All models failed to respond in Stage 1, likely due to rate limits or API errors. "
    "Please try again or adjust your model selection."
)
# Finished runs kept for re-attach and replay; older ones are dropped when a run starts.
MAX_FINISHED_RUNS = 20
# How long a cancelled first-message run waits for its title, as upstream's stream does.
CANCEL_TITLE_WAIT_SECONDS = 2.0

# main._fetch_search_context(content, settings, provider_override, *, conversation_id)
# -> (search_context, search_query, search_result)
FetchSearchContext = Callable[..., Awaitable[Tuple[str, str, Dict[str, Any]]]]


def _call_tokens(usage: Any) -> Optional[int]:
    """Total tokens of one call from upstream's ``usage``: total, else input + output."""
    if not isinstance(usage, dict):
        return None

    def _first_int(*keys: str) -> Optional[int]:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    total = _first_int("total_tokens")
    if total is not None:
        return total
    parts = [
        part
        for part in (_first_int("input_tokens", "prompt_tokens"), _first_int("output_tokens", "completion_tokens"))
        if part is not None
    ]
    return sum(parts) if parts else None


def _call_cost(cost: Any) -> Optional[float]:
    """Cost of one call from upstream's ``cost`` record (its ``total_cost``), or a bare number."""
    if isinstance(cost, dict):
        cost = cost.get("total_cost")
    if cost is None or isinstance(cost, bool):
        return None
    try:
        return float(cost)
    except (TypeError, ValueError):
        return None


def _sum_known(values: Iterable[Optional[float]]) -> Optional[float]:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


def _with_generation_metrics(
    rows: List[Dict[str, Any]],
    stages: List[Tuple[List[Dict[str, Any]], Dict[str, int]]],
) -> List[Dict[str, Any]]:
    """Add the leaderboard's per-model time, tokens and cost to aggregate ranking rows.

    ``stages`` is ``[(results, elapsed_ms_by_model), ...]`` for Stage 1 and Stage 2.
    A model's time is its arrival time within each stage, summed over the stages.
    """
    enriched = []
    for row in rows:
        model = row.get("model")
        time_ms = 0
        tokens: List[Optional[float]] = []
        costs: List[Optional[float]] = []
        for results, elapsed_ms_by_model in stages:
            time_ms += elapsed_ms_by_model.get(model, 0)
            result = next((r for r in reversed(results) if r.get("model") == model), None)
            if result is not None:
                tokens.append(_call_tokens(result.get("usage")))
                costs.append(_call_cost(result.get("cost")))
        total_tokens = _sum_known(tokens)
        enriched.append(
            {
                **row,
                "generation_time_ms": time_ms,
                "generation_time_seconds": int(round(time_ms / 1000)),
                "generation_total_tokens": int(total_tokens) if total_tokens is not None else None,
                "generation_total_cost": _sum_known(costs),
            }
        )
    return enriched


def _log_stage_totals(run: "RunState", stage: str, results: List[Dict[str, Any]]) -> None:
    logger.info(
        "Run %s %s: %d results, tokens=%s, cost=%s",
        run.run_id,
        stage,
        len(results),
        _sum_known(_call_tokens(r.get("usage")) for r in results),
        _sum_known(_call_cost(r.get("cost")) for r in results),
    )


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

    def __init__(
        self,
        progress: Optional[Dict[str, Dict[str, Any]]] = None,
        fetch_search_context: Optional[FetchSearchContext] = None,
    ):
        self._runs: Dict[str, RunState] = {}
        self._active_by_conversation: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        # main's _active_runs map (conversation_id -> progress entry), so that
        # GET /api/conversations/{id}/progress also reports background runs.
        self._progress: Dict[str, Dict[str, Any]] = progress if progress is not None else {}
        # main's _fetch_search_context, so runs search exactly as upstream's routes do.
        # Without it, a run that asks for web search fails with a clear error.
        self._fetch_search_context = fetch_search_context

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
            self._prune_finished_runs()
            self._runs[run.run_id] = run
            self._active_by_conversation[conversation_id] = run.run_id
            self._start_progress(run)
            run.task = asyncio.create_task(self._execute_run(run))
            return run

    def _prune_finished_runs(self) -> None:
        """Keep only the most recent MAX_FINISHED_RUNS finished runs; never drop a live one."""
        finished = [run_id for run_id, run in self._runs.items() if run.status in TERMINAL_STATUSES]
        for run_id in finished[: max(0, len(finished) - MAX_FINISHED_RUNS)]:
            del self._runs[run_id]

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
        if run.status in TERMINAL_STATUSES or run.cancel_event.is_set():
            # Already stopping: a second task.cancel() would interrupt the cancel
            # handler's save and final event.
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

    async def _emit_final(self, run: RunState, status: str, event: Dict[str, Any]):
        """Set the terminal status and append the final event with no await in between.

        A stream ends once the status is terminal and it has sent every event, so
        the status must never be terminal before the final event is in the list.
        """
        run.status = status
        await self._emit(run, event)

    async def _fail_with_error_message(self, run: RunState, message: str):
        """End the run the way upstream records a failed turn: an error message, no stages."""
        run.error_message = message
        storage.add_error_message(run.conversation_id, message)
        run.assistant_message_saved = True
        await self._emit_final(run, "failed", {"type": "error", "message": message})

    async def _save_title_after_cancel(self, run: RunState, title_task: Optional[asyncio.Task]):
        """Save the first-message title if it finishes shortly, as upstream's stream does."""
        if title_task is None:
            return
        # asyncio.wait neither cancels title_task nor raises what it raised.
        await asyncio.wait({title_task}, timeout=CANCEL_TITLE_WAIT_SECONDS)
        if not title_task.done() or title_task.cancelled() or title_task.exception() is not None:
            return
        run.title = title_task.result()
        try:
            storage.update_conversation_title(run.conversation_id, run.title)
        except Exception as exc:
            logger.warning("Could not save title for cancelled run %s: %s", run.run_id, exc)

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

        # Top-level flags the UI reads on a reloaded turn.
        if run.aborted or run.error_message:
            last = conversation["messages"][-1]
            if run.aborted:
                last["aborted"] = True
            if run.error_message:
                last["error"] = f"Error: {run.error_message}"
            storage.save_conversation(conversation)

        run.assistant_message_saved = True

    async def _execute_run(self, run: RunState):
        title_task: Optional[asyncio.Task] = None
        # Per-model arrival time within each stage; every model in a stage starts together.
        stage1_ms: Dict[str, int] = {}
        stage2_ms: Dict[str, int] = {}

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
                if self._fetch_search_context is None:
                    raise RuntimeError("Web search is unavailable: RunManager was created without fetch_search_context")
                settings = get_settings()
                provider = SearchProvider(run.search_provider or settings.search_provider)
                self._set_stage(run, "search")
                await self._emit(run, {"type": "search_start", "data": {"provider": provider.value}})
                # main._fetch_search_context applies upstream's provider env and query rules.
                run.search_context, run.search_query, search_result = await self._fetch_search_context(
                    run.content,
                    settings,
                    run.search_provider,
                    conversation_id=run.conversation_id,
                )
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
                stage1_ms[item.get("model")] = _elapsed_ms(stage1_started_at)
                await self._emit(
                    run,
                    {
                        "type": "stage1_progress",
                        "data": item,
                        "count": len(run.stage1_results),
                        "total": run.stage1_total_models,
                    },
                )
            stage1_wall_ms = _elapsed_ms(stage1_started_at)
            _log_stage_totals(run, "stage1", run.stage1_results)
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
                    stage2_ms[item.get("model")] = _elapsed_ms(stage2_started_at)
                    # Snapshot convenience only, keyed by model: a model sitting in the
                    # council twice collapses here. Each result's own stage2_label_map
                    # is authoritative, and aggregation below reads that.
                    evaluator = item.get("model")
                    if isinstance(item.get("stage2_label_map"), dict):
                        run.stage2_label_maps_by_evaluator[evaluator] = item["stage2_label_map"]
                    if isinstance(item.get("stage2_candidate_label_map"), dict):
                        run.stage2_candidate_maps_by_evaluator[evaluator] = item["stage2_candidate_label_map"]
                    await self._emit(
                        run,
                        {
                            "type": "stage2_progress",
                            "data": item,
                            "count": len(run.stage2_results),
                            "total": run.stage2_total_models,
                        },
                    )
                stage2_wall_ms = _elapsed_ms(stage2_started_at)
                _log_stage_totals(run, "stage2", run.stage2_results)

                aggregate_rankings, run.ranking_diagnostics = calculate_aggregate_rankings(
                    run.stage2_results,
                    run.label_to_model,
                    return_diagnostics=True,
                )
                # Per-model time/tokens/cost for the fork's leaderboard columns.
                run.aggregate_rankings = _with_generation_metrics(
                    aggregate_rankings,
                    [(run.stage1_results, stage1_ms), (run.stage2_results, stage2_ms)],
                )
                run.generation_time_ms = stage1_wall_ms + stage2_wall_ms
                run.generation_time_seconds = int(round(run.generation_time_ms / 1000))

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
                    # Shielded: a Stop during this wait cancels the run, not the title,
                    # so the cancel handler can still save both.
                    run.title = await asyncio.shield(title_task)
                    storage.update_conversation_title(run.conversation_id, run.title)
                    await self._emit(run, {"type": "title_complete", "data": {"title": run.title}})
                except Exception as exc:
                    logger.warning("Title generation failed for run %s: %s", run.run_id, exc)

            await self._save_assistant_message(run)
            cost_report = build_council_cost_report(run.stage1_results, run.stage2_results, run.stage3_result)
            await self._emit_final(run, "completed", {"type": "complete", "metadata": {"cost_report": cost_report}})
        except asyncio.CancelledError:
            run.aborted = True
            # As upstream's stream: save partial results only when Stage 1 produced any.
            if run.stage1_results:
                try:
                    await self._save_assistant_message(run, partial=True)
                except Exception as save_exc:
                    logger.warning("Could not save partial results for run %s: %s", run.run_id, save_exc)
            await self._save_title_after_cancel(run, title_task)
            await self._emit_final(run, "cancelled", {"type": "cancelled"})
        except Exception as exc:
            run.error_message = str(exc) or exc.__class__.__name__
            try:
                if run.stage1_results:
                    await self._save_assistant_message(run, partial=True)
                elif not run.assistant_message_saved:
                    storage.add_error_message(run.conversation_id, f"Error: {run.error_message}")
                    run.assistant_message_saved = True
            except Exception as save_exc:
                logger.warning("Could not save failed run %s: %s", run.run_id, save_exc)
            await self._emit_final(run, "failed", {"type": "error", "message": run.error_message})
        finally:
            if title_task is not None and not title_task.done():
                title_task.cancel()
            if run.status not in TERMINAL_STATUSES:
                # Interrupted inside a handler (e.g. at shutdown): never leave the
                # conversation looking busy.
                run.aborted = True
                run.status = "cancelled"
                run.events.append({"type": "cancelled"})
            run.ended_at = time.perf_counter()
            # Request-sized data the snapshot never exposes; finished runs are kept for replay.
            run.history = []
            run.query = ""
            async with self._lock:
                active_run_id = self._active_by_conversation.get(run.conversation_id)
                if active_run_id == run.run_id:
                    self._active_by_conversation.pop(run.conversation_id, None)
                self._finish_progress(run)
            async with run.event_condition:
                run.event_condition.notify_all()
