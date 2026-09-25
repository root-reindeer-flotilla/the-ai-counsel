import asyncio
import inspect
import json
import time
from types import SimpleNamespace

import pytest
from backend import main, runs, storage
from backend.settings import Settings


async def _wait_for(predicate, timeout=2.0, interval=0.05):
    started = time.time()
    while time.time() - started < timeout:
        value = predicate()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return True
        await asyncio.sleep(interval)
    return False


async def _status(manager, run_id):
    run = await manager.get_run(run_id)
    return run.status if run else None


class _PreflightOk:
    ok = True


@pytest.fixture(autouse=True)
def _no_real_model_calls(monkeypatch):
    """Upstream preflight pings real providers; treat every model as available.

    Title generation is stubbed too, so a first message never calls a real model.
    """

    async def _always_ok(_models, **_kwargs):
        return _PreflightOk()

    monkeypatch.setattr(runs, "preflight_models", _always_ok)
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q, **_kwargs: asyncio.sleep(0, result="Title"))


@pytest.mark.anyio
async def test_run_continues_without_stream_client(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q, **_kwargs: asyncio.sleep(0, result="Title"))

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        await asyncio.sleep(0.05)
        yield {"model": "m1", "response": "A", "error": None}
        await asyncio.sleep(0.05)
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-1")
    run = await manager.start_run(
        conversation_id=conv["id"],
        content="hello",
        execution_mode="chat_only",
    )

    async def _is_completed():
        return (await _status(manager, run.run_id)) == "completed"

    assert await _wait_for(_is_completed, timeout=3.0)

    saved = storage.get_conversation(conv["id"])
    assert len(saved["messages"]) == 2
    assert saved["messages"][0]["role"] == "user"
    assert saved["messages"][1]["role"] == "assistant"
    assert len(saved["messages"][1]["stage1"]) == 2


@pytest.mark.anyio
async def test_cancel_run_persists_partial_results(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q, **_kwargs: asyncio.sleep(0, result="Title"))

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 3
        yield {"model": "m1", "response": "A", "error": None}
        await asyncio.sleep(2.0)
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-2")
    run = await manager.start_run(
        conversation_id=conv["id"],
        content="hello",
        execution_mode="chat_only",
    )

    async def _has_partial_stage1():
        current_run = await manager.get_run(run.run_id)
        return bool(current_run and len(current_run.stage1_results) == 1)

    assert await _wait_for(_has_partial_stage1, timeout=1.0)
    current = await manager.get_run(run.run_id)
    assert len(current.stage1_results) == 1

    cancelled = await manager.cancel_run(run.run_id)
    assert cancelled is not None

    async def _is_cancelled():
        return (await _status(manager, run.run_id)) == "cancelled"

    assert await _wait_for(_is_cancelled, timeout=3.0)

    saved = storage.get_conversation(conv["id"])
    assert len(saved["messages"]) == 2
    assistant = saved["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant.get("aborted") is True
    assert len(assistant["stage1"]) >= 1


@pytest.mark.anyio
async def test_stage2_aggregate_rows_include_generation_time_and_tokens(monkeypatch, tmp_path):
    """Leaderboard rows carry per-model time, tokens and cost, read from upstream's result shape.

    Upstream results carry normalized ``usage`` and a ``cost`` record; durations are
    measured by the run from each result's arrival relative to its stage's start.
    """
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    clock = {"now": 100.0}
    monkeypatch.setattr(runs, "time", SimpleNamespace(perf_counter=lambda: clock["now"]))

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        clock["now"] += 3.0  # model-a answers 3 s after Stage 1 starts
        yield {
            "model": "openai:model-a",
            "response": "A",
            "error": None,
            "usage": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
            "cost": {"model": "openai:model-a", "total_cost": 0.12, "cost_status": "estimated"},
        }

    async def _stage2_collect(_query, _stage1_results, _search_context="", _request=None, **_kwargs):
        yield {"Response A": "openai:model-a"}
        clock["now"] += 6.0  # and ranks 6 s after Stage 2 starts
        yield {
            "model": "openai:model-a",
            "ranking": "FINAL RANKING:\n1. Response A",
            "parsed_ranking": ["Response A"],
            "stage2_label_map": {"Response A": "openai:model-a"},
            "error": None,
            # Raw provider naming, no total: prompt + completion.
            "usage": {"prompt_tokens": 29000, "completion_tokens": 209},
            "cost": {"model": "openai:model-a", "total_cost": 0.28, "cost_status": "estimated"},
        }

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    monkeypatch.setattr(runs, "stage2_collect_rankings", _stage2_collect)

    conv = storage.create_conversation("c-run-3")
    run = await manager.start_run(
        conversation_id=conv["id"],
        content="hello",
        execution_mode="chat_ranking",
    )

    async def _is_completed():
        return (await _status(manager, run.run_id)) == "completed"

    assert await _wait_for(_is_completed, timeout=3.0)

    final_run = await manager.get_run(run.run_id)
    assert final_run is not None
    assert len(final_run.aggregate_rankings) == 1
    row = final_run.aggregate_rankings[0]
    assert row["model"] == "openai:model-a"
    assert row["generation_time_ms"] == 9000
    assert row["generation_time_seconds"] == 9
    assert row["generation_total_tokens"] == 30409
    assert row["generation_total_cost"] == pytest.approx(0.40)

    saved = storage.get_conversation(conv["id"])
    assistant = saved["messages"][1]
    persisted_row = assistant["metadata"]["aggregate_rankings"][0]
    assert persisted_row["generation_time_seconds"] == 9
    assert persisted_row["generation_total_tokens"] == 30409
    assert persisted_row["generation_total_cost"] == pytest.approx(0.40)


@pytest.mark.anyio
async def test_preflight_failure_fails_run_and_records_error(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    preflight_calls = []
    stage1_called = []

    class _Failed:
        ok = False

    async def _preflight(models, **kwargs):
        preflight_calls.append((list(models), kwargs))
        return _Failed()

    async def _stage1_collect(*_args, **_kwargs):
        stage1_called.append(True)
        yield 0

    monkeypatch.setattr(runs, "preflight_models", _preflight)
    monkeypatch.setattr(runs, "build_preflight_error_message", lambda _result: "model m1 unavailable")
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-preflight")
    run = await manager.start_run(
        conversation_id=conv["id"],
        content="hello",
        execution_mode="full",
        council_models=["openai:m1", "requesty:openai/m2"],
        chairman_model="anthropic:chair",
    )

    async def _is_failed():
        return (await _status(manager, run.run_id)) == "failed"

    assert await _wait_for(_is_failed, timeout=3.0)
    # Council plus the chairman in full mode, as upstream's /message/stream checks.
    assert preflight_calls == [
        (["openai:m1", "requesty:openai/m2", "anthropic:chair"], {"conversation_id": conv["id"]})
    ]
    assert stage1_called == []
    assert run.error_message == "model m1 unavailable"
    assert run.events[-1] == {"type": "error", "message": "model m1 unavailable"}
    assert await manager.get_active_run_for_conversation(conv["id"]) is None
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["error"] == "model m1 unavailable"


@pytest.mark.anyio
async def test_run_passes_overrides_and_history_to_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    captured = {}

    async def _stage1_collect(query, _search_context="", _request=None, **kwargs):
        captured["stage1_query"] = query
        captured["stage1"] = kwargs
        yield 1
        yield {"model": "requesty:openai/m1", "response": "A", "error": None}

    async def _stage2_collect(query, stage1_results, _search_context="", _request=None, **kwargs):
        captured["stage2_query"] = query
        captured["stage2_stage1"] = stage1_results
        captured["stage2"] = kwargs
        yield {"Response A": "requesty:openai/m1"}
        yield {
            "model": "requesty:openai/m1",
            "ranking": "FINAL RANKING:\n1. Response A",
            "parsed_ranking": ["Response A"],
            "stage2_label_map": {"Response A": "requesty:openai/m1"},
            "stage2_candidate_label_map": {"Response A": "candidate_01"},
            "error": None,
        }

    async def _stage3(query, stage1_results, stage2_results, _search_context="", **kwargs):
        captured["stage3_query"] = query
        captured["stage3_stage1"] = stage1_results
        captured["stage3_stage2"] = stage2_results
        captured["stage3"] = kwargs
        return {"model": "anthropic:chair", "response": "final"}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    monkeypatch.setattr(runs, "stage2_collect_rankings", _stage2_collect)
    monkeypatch.setattr(runs, "stage3_synthesize_final", _stage3)

    conv = storage.create_conversation("c-run-overrides")
    storage.add_user_message(conv["id"], "earlier question")
    storage.add_assistant_message(
        conv["id"],
        [{"model": "m0", "response": "<think>hidden</think>\n\nearlier answer", "error": None}],
        None,
        None,
        {"execution_mode": "chat_only"},
    )
    storage.add_user_message(conv["id"], "second question")
    storage.add_assistant_message(
        conv["id"],
        [{"model": "m0", "response": "stage1 text", "error": None}],
        None,
        {
            "model": "chair",
            "response": "<thinking>scratch</thinking>chairman answer",
            "response_prompt_safe": "chairman answer",
        },
        {"execution_mode": "full"},
    )
    run = await manager.start_run(
        conversation_id=conv["id"],
        content="follow-up",
        execution_mode="full",
        council_models=["requesty:openai/m1"],
        chairman_model="anthropic:chair",
    )

    async def _is_completed():
        return (await _status(manager, run.run_id)) == "completed"

    assert await _wait_for(_is_completed, timeout=3.0)
    assert captured["stage1_query"] == "follow-up"
    assert captured["stage1"]["models_override"] == ["requesty:openai/m1"]
    assert captured["stage1"]["conversation_id"] == conv["id"]
    # Prior turns only (not the new user message), with thinking stripped.
    assert captured["stage1"]["history"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "chairman answer"},
    ]
    assert captured["stage2"]["conversation_id"] == conv["id"]
    assert captured["stage3"]["chairman_override"] == "anthropic:chair"
    assert captured["stage3"]["conversation_id"] == conv["id"]
    # Stage 3 gets the very list Stage 2 ranked.
    assert captured["stage3_stage1"] is captured["stage2_stage1"]
    assert captured["stage3_stage2"] == run.stage2_results
    assert run.events[-1]["type"] == "complete"
    assert "cost_report" in run.events[-1]["metadata"]

    saved = storage.get_conversation(conv["id"])
    assistant = saved["messages"][-1]
    assert assistant["stage3"]["response"] == "final"
    assert assistant["metadata"]["execution_mode"] == "full"
    assert "cost_report" in assistant["metadata"]


def test_main_chat_history_strips_thinking_blocks():
    conversation = {
        "messages": [
            {"role": "user", "content": "q1"},
            {
                "role": "assistant",
                "stage1": [
                    {"model": "bad", "error": True, "response": ""},
                    {"model": "m1", "response": "<think>x</think>visible", "error": None},
                ],
            },
            {"role": "user", "content": "q2"},
            {
                "role": "assistant",
                "stage1": [{"model": "m1", "response": "s1", "error": None}],
                "stage3": {"response": "<think>y</think>synth"},
            },
            {"role": "assistant", "content": None, "error": "boom", "stage1": [], "stage3": None},
        ]
    }
    expected = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "visible"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "synth"},
    ]
    assert main._build_chat_history(conversation) == expected
    assert runs.build_chat_history(conversation) == expected


@pytest.mark.anyio
async def test_stage2_flat_contract_and_per_result_label_maps(monkeypatch, tmp_path):
    """First Stage 2 yield is the flat global map; aggregation reads each ballot's own map."""
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "one", "error": None}
        yield {"model": "m2", "response": "two", "error": None}

    async def _stage2_collect(_query, _stage1_results, _search_context="", _request=None, **_kwargs):
        yield {"Response A": "m1", "Response B": "m2"}
        # m1 saw candidates in canonical order.
        yield {
            "model": "m1",
            "ranking": "FINAL RANKING:\n1. Response B\n2. Response A",
            "parsed_ranking": ["Response B", "Response A"],
            "parsed_ranking_local": ["Response B", "Response A"],
            "stage2_label_map": {"Response A": "m1", "Response B": "m2"},
            "stage2_candidate_label_map": {"Response A": "candidate_01", "Response B": "candidate_02"},
            "error": None,
        }
        # m2 saw them rotated: its local "Response A" is m2.
        yield {
            "model": "m2",
            "ranking": "FINAL RANKING:\n1. Response A\n2. Response B",
            "parsed_ranking": ["Response B", "Response A"],
            "parsed_ranking_local": ["Response A", "Response B"],
            "stage2_label_map": {"Response A": "m2", "Response B": "m1"},
            "stage2_candidate_label_map": {"Response A": "candidate_02", "Response B": "candidate_01"},
            "error": None,
        }

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    monkeypatch.setattr(runs, "stage2_collect_rankings", _stage2_collect)

    conv = storage.create_conversation("c-run-stage2")
    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="chat_ranking")

    async def _is_completed():
        return (await _status(manager, run.run_id)) == "completed"

    assert await _wait_for(_is_completed, timeout=3.0)
    assert run.label_to_model == {"Response A": "m1", "Response B": "m2"}
    assert run.stage2_total_models == 2
    assert {"type": "stage2_init", "total": 2} in run.events
    assert len(run.stage2_results) == 2
    assert run.stage2_label_maps_by_evaluator == {
        "m1": {"Response A": "m1", "Response B": "m2"},
        "m2": {"Response A": "m2", "Response B": "m1"},
    }
    assert run.stage2_candidate_maps_by_evaluator["m2"] == {
        "Response A": "candidate_02",
        "Response B": "candidate_01",
    }
    # Both evaluators ranked m2 first; the text of m2's ballot says "Response A".
    assert [row["model"] for row in run.aggregate_rankings] == ["m2", "m1"]
    assert run.aggregate_rankings[0]["average_rank"] == 1.0

    snapshot = await manager.snapshot(run)
    assert snapshot["metadata"]["label_to_model"] == run.label_to_model
    assert snapshot["stage2_results"][1]["stage2_label_map"] == {"Response A": "m2", "Response B": "m1"}
    stage2_complete = next(e for e in run.events if e["type"] == "stage2_complete")
    assert stage2_complete["metadata"]["label_to_model"] == run.label_to_model
    assert stage2_complete["metadata"]["aggregate_rankings"] == run.aggregate_rankings

    saved = storage.get_conversation(conv["id"])
    assistant = saved["messages"][1]
    assert assistant["stage2"][1]["stage2_label_map"] == {"Response A": "m2", "Response B": "m1"}
    assert assistant["metadata"]["label_to_model"] == run.label_to_model


@pytest.mark.anyio
async def test_all_stage1_failures_record_error_message(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        yield {"model": "m1", "response": "", "error": True, "error_message": "429"}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-all-failed")
    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="full")

    async def _is_failed():
        return (await _status(manager, run.run_id)) == "failed"

    assert await _wait_for(_is_failed, timeout=3.0)
    assert run.events[-1]["type"] == "error"
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["error"].startswith("All models failed")


@pytest.mark.anyio
async def test_progress_endpoint_reports_background_run(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    progress_map = {}
    monkeypatch.setattr(main, "_active_runs", progress_map)
    manager = runs.RunManager(progress=progress_map)
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    release = asyncio.Event()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "A", "error": None}
        await release.wait()
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-progress")
    storage.add_user_message(conv["id"], "earlier")
    run = await manager.start_run(conversation_id=conv["id"], content="hello", execution_mode="chat_only")
    # Registered as soon as start_run returns, before the task has run.
    assert progress_map[conv["id"]]["run_id"] == run.run_id
    assert await _wait_for(lambda: len(run.stage1_results) == 1, timeout=3.0)

    progress = await main.get_conversation_progress(conv["id"])
    assert progress["active"] is True
    assert progress["mode"] == "council"
    assert progress["run_id"] == run.run_id
    assert progress["event_count"] == len(run.events)
    assert progress["stage"] == "stage1"
    assert progress["execution_mode"] == "chat_only"
    assert progress["progress"]["stage1"] == {"count": 1, "total": 2}
    assert progress["stage1"] == [{"model": "m1", "response": "A", "error": None}]

    # A second run on the same conversation is refused while this one is live.
    with pytest.raises(RuntimeError):
        await manager.start_run(conversation_id=conv["id"], content="again", execution_mode="chat_only")

    release.set()
    assert await _wait_for(lambda: run.status == "completed", timeout=3.0)
    assert await main.get_conversation_progress(conv["id"]) == {"active": False}
    assert conv["id"] not in progress_map


@pytest.mark.anyio
async def test_start_run_refuses_while_legacy_stream_is_active(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    progress_map = {}
    manager = runs.RunManager(progress=progress_map)
    conv = storage.create_conversation("c-run-legacy")
    legacy_entry = {"mode": "council", "stage": "stage1", "execution_mode": "full"}
    progress_map[conv["id"]] = legacy_entry

    with pytest.raises(RuntimeError):
        await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="full")

    assert progress_map[conv["id"]] is legacy_entry
    assert storage.get_conversation(conv["id"])["messages"] == []


@pytest.mark.anyio
async def test_cancel_before_run_starts_releases_conversation(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    progress_map = {}
    manager = runs.RunManager(progress=progress_map)
    conv = storage.create_conversation("c-run-early-cancel")

    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="full")
    await manager.cancel_run(run.run_id)

    async def _is_cancelled():
        return (await _status(manager, run.run_id)) == "cancelled"

    assert await _wait_for(_is_cancelled, timeout=3.0)
    assert await manager.get_active_run_for_conversation(conv["id"]) is None
    assert conv["id"] not in progress_map
    assert run.events[-1] == {"type": "cancelled"}
    # As upstream's /message/stream does, a cancel with no Stage 1 result keeps the
    # user message and saves no (empty) assistant turn.
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user"]
    # The conversation is free for the next run.
    next_run = await manager.start_run(conversation_id=conv["id"], content="again", execution_mode="chat_only")
    await manager.cancel_run(next_run.run_id)
    assert await _wait_for(lambda: next_run.status == "cancelled", timeout=3.0)


@pytest.mark.anyio
async def test_run_web_search_follows_upstream_query_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    # The run searches through main's own _fetch_search_context (injected), so it
    # follows upstream's query rules and provider env setup without a copy of them.
    manager = runs.RunManager(fetch_search_context=main._fetch_search_context)
    settings = Settings()
    settings.search_provider = "duckduckgo"
    settings.search_keyword_extraction = "llm"
    monkeypatch.setattr(runs, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_api_key", lambda provider: f"key-{provider}")
    monkeypatch.setenv("TAVILY_API_KEY", "before")
    search_calls = []
    query_calls = []

    async def _generate_search_query(content, **kwargs):
        query_calls.append((content, kwargs))
        return "optimized query"

    async def _perform_web_search(query, _count, provider, *_args, **_kwargs):
        search_calls.append((query, provider))
        return {"results": "ctx", "extracted_query": query, "intent": "general"}

    async def _stage1_collect(_query, search_context="", _request=None, **_kwargs):
        assert search_context == "ctx"
        yield 1
        yield {"model": "m1", "response": "A", "error": None}

    monkeypatch.setattr(main, "generate_search_query", _generate_search_query)
    monkeypatch.setattr(main, "perform_web_search", _perform_web_search)
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-search")
    storage.add_user_message(conv["id"], "earlier")

    # search_provider alone turns search on and overrides the settings provider.
    run = await manager.start_run(
        conversation_id=conv["id"], content="question", execution_mode="chat_only", search_provider="tavily"
    )
    assert run.web_search is True
    assert await _wait_for(lambda: run.status == "completed", timeout=3.0)
    assert query_calls == [("question", {"conversation_id": conv["id"]})]
    assert search_calls == [("optimized query", runs.SearchProvider.TAVILY)]
    assert main.os.environ["TAVILY_API_KEY"] == "key-tavily"
    assert run.search_query == "optimized query"
    assert {"type": "search_start", "data": {"provider": "tavily"}} in run.events

    # DuckDuckGo optimizes queries itself, so no LLM query generation.
    run2 = await manager.start_run(
        conversation_id=conv["id"], content="plain", execution_mode="chat_only", web_search=True
    )
    assert await _wait_for(lambda: run2.status == "completed", timeout=3.0)
    assert len(query_calls) == 1
    assert search_calls[-1] == ("plain", runs.SearchProvider.DUCKDUCKGO)
    saved = storage.get_conversation(conv["id"])
    assert saved["messages"][-1]["metadata"]["web_search"] is True
    assert saved["messages"][-1]["metadata"]["search_query"] == "plain"


@pytest.mark.anyio
async def test_run_documents_reach_stages_and_user_message(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    captured = {}

    async def _stage1_collect(query, _search_context="", _request=None, **_kwargs):
        captured["query"] = query
        yield 1
        yield {"model": "m1", "response": "A", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-docs")
    storage.add_user_message(conv["id"], "earlier")

    run = await manager.start_run(
        conversation_id=conv["id"],
        content="summarize",
        execution_mode="chat_only",
        documents=[{"name": "notes.txt", "mime_type": "text/plain", "text": "document body"}],
    )
    assert await _wait_for(lambda: run.status == "completed", timeout=3.0)
    assert captured["query"].startswith("summarize")
    assert "document body" in captured["query"]
    user_message = storage.get_conversation(conv["id"])["messages"][-2]
    assert user_message["content"] == "summarize"
    assert user_message["attachments"][0]["name"] == "notes.txt"

    with pytest.raises(ValueError):
        await manager.start_run(
            conversation_id=conv["id"], content="bad", execution_mode="chat_only", documents=["not-a-dict"]
        )


@pytest.mark.anyio
async def test_run_without_search_helper_fails_with_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()  # no fetch_search_context injected
    conv = storage.create_conversation("c-run-no-search")
    storage.add_user_message(conv["id"], "earlier")

    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="chat_only", web_search=True)
    assert await _wait_for(lambda: run.status == "failed", timeout=3.0)
    assert "fetch_search_context" in run.error_message
    assert run.events[-1]["type"] == "error"


def _sse_events(chunks):
    return [json.loads(chunk[len("data: "):]) for chunk in chunks if chunk.startswith("data: ")]


async def _collect_stream(manager, run_id, from_event=0):
    return [chunk async for chunk in manager.stream_events(run_id, from_event=from_event)]


@pytest.mark.anyio
async def test_cancel_during_title_wait_saves_turn_and_emits_cancelled(monkeypatch, tmp_path):
    """Stop while the finished run waits for the first-message title keeps the turn."""
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    release_title = asyncio.Event()

    async def _title(_query, **_kwargs):
        await release_title.wait()
        return "Late title"

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        yield {"model": "m1", "response": "A", "error": None}

    monkeypatch.setattr(runs, "generate_conversation_title", _title)
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-title-cancel")
    run = await manager.start_run(conversation_id=conv["id"], content="first", execution_mode="chat_only")
    # Every stage is done; the run is now waiting on the title.
    assert await _wait_for(lambda: any(e["type"] == "stage1_complete" for e in run.events), timeout=3.0)
    await asyncio.sleep(0.05)

    await manager.cancel_run(run.run_id)
    await asyncio.sleep(0.05)
    release_title.set()

    assert await _wait_for(lambda: run.task.done(), timeout=3.0)
    assert run.status == "cancelled"
    assert run.events[-1] == {"type": "cancelled"}
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["aborted"] is True
    assert saved["messages"][1]["stage1"][0]["response"] == "A"
    assert saved["title"] == "Late title"
    assert await manager.get_active_run_for_conversation(conv["id"]) is None


@pytest.mark.anyio
async def test_stream_attached_during_cancel_handling_gets_cancelled_event(monkeypatch, tmp_path):
    """The run stays non-terminal until its final event, so a late stream still gets it."""
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    release_title = asyncio.Event()

    async def _title(_query, **_kwargs):
        await release_title.wait()
        return "Title"

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "A", "error": None}
        await asyncio.Event().wait()  # m2 never answers
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "generate_conversation_title", _title)
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    conv = storage.create_conversation("c-run-late-stream")
    run = await manager.start_run(conversation_id=conv["id"], content="first", execution_mode="chat_only")
    assert await _wait_for(lambda: len(run.stage1_results) == 1, timeout=3.0)

    await manager.cancel_run(run.run_id)
    await asyncio.sleep(0.05)  # the cancel handler is now waiting (briefly) for the title
    consumer = asyncio.create_task(_collect_stream(manager, run.run_id))
    await asyncio.sleep(0.05)
    # A second Stop during the handler must not interrupt the save.
    await manager.cancel_run(run.run_id)
    release_title.set()

    chunks = await asyncio.wait_for(consumer, timeout=3.0)
    events = _sse_events(chunks)
    assert events[-1] == {"type": "cancelled"}
    assert events == run.events
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["aborted"] is True
    # The second Stop did not cut the handler's title wait short.
    assert saved["title"] == "Title"


@pytest.mark.anyio
async def test_failure_after_partial_progress_sets_error_on_saved_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "A", "error": None}
        raise RuntimeError("boom")

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-partial-fail")
    storage.add_user_message(conv["id"], "earlier")

    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="full")
    assert await _wait_for(lambda: run.status == "failed", timeout=3.0)
    assert run.events[-1] == {"type": "error", "message": "boom"}
    assistant = storage.get_conversation(conv["id"])["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["stage1"][0]["response"] == "A"
    assert assistant["metadata"]["incomplete"] is True
    # The UI shows a failed turn from the message's top-level error.
    assert assistant["error"] == "Error: boom"


@pytest.mark.anyio
async def test_title_task_is_cancelled_when_run_fails_early(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    title_cancelled = asyncio.Event()

    async def _title(_query, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            title_cancelled.set()
            raise

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        await asyncio.sleep(0.05)  # the title call is under way
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(runs, "generate_conversation_title", _title)
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-title-leak")

    run = await manager.start_run(conversation_id=conv["id"], content="first", execution_mode="full")
    assert await _wait_for(lambda: run.status == "failed", timeout=3.0)
    assert await _wait_for(title_cancelled.is_set, timeout=1.0)


@pytest.mark.anyio
async def test_stream_events_replays_from_event_and_ends_after_final_event(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    release = asyncio.Event()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "A", "error": None}
        await release.wait()
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-replay")
    storage.add_user_message(conv["id"], "earlier")

    run = await manager.start_run(conversation_id=conv["id"], content="q", execution_mode="chat_only")
    assert await _wait_for(lambda: len(run.stage1_results) == 1, timeout=3.0)
    # Re-attach mid-run from event 2: earlier events are skipped, later ones arrive live.
    live = asyncio.create_task(_collect_stream(manager, run.run_id, from_event=2))
    await asyncio.sleep(0.05)
    assert not live.done()
    release.set()
    live_events = _sse_events(await asyncio.wait_for(live, timeout=3.0))
    assert run.status == "completed"
    assert live_events == run.events[2:]
    assert live_events[-1]["type"] == "complete"

    # A finished run replays from any offset and the stream ends after the final event.
    replay = _sse_events(await asyncio.wait_for(_collect_stream(manager, run.run_id, from_event=3), timeout=1.0))
    assert replay == run.events[3:]
    assert replay[-1]["type"] == "complete"


def test_finish_progress_only_pops_the_runs_own_entry():
    progress_map = {}
    manager = runs.RunManager(progress=progress_map)
    run = runs.RunState(
        run_id="r1", conversation_id="c1", content="q", web_search=False, execution_mode="full", is_first_message=False
    )

    other = {"mode": "council", "stage": "stage1", "execution_mode": "full"}  # an upstream stream's entry
    progress_map["c1"] = other
    manager._finish_progress(run)
    assert progress_map["c1"] is other

    progress_map["c1"] = {"mode": "council", "run_id": "someone-else"}
    manager._finish_progress(run)
    assert progress_map["c1"]["run_id"] == "someone-else"

    manager._start_progress(run)
    assert progress_map["c1"]["run_id"] == "r1"
    manager._finish_progress(run)
    assert "c1" not in progress_map


@pytest.mark.anyio
async def test_finished_runs_are_pruned_and_release_request_data(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        yield {"model": "m1", "response": "A", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)

    def _state(run_id, status):
        state = runs.RunState(
            run_id=run_id,
            conversation_id=f"conv-{run_id}",
            content="q",
            web_search=False,
            execution_mode="chat_only",
            is_first_message=False,
            status=status,
        )
        manager._runs[run_id] = state
        return state

    old_active = _state("active-old", "running")  # an older run that is still live
    for i in range(25):
        _state(f"done-{i:02d}", "completed" if i % 2 else "failed")

    conv = storage.create_conversation("c-run-prune")
    storage.add_user_message(conv["id"], "earlier")
    run = await manager.start_run(conversation_id=conv["id"], content="big question", execution_mode="chat_only")
    assert "active-old" in manager._runs and manager._runs["active-old"] is old_active
    finished = [rid for rid, r in manager._runs.items() if r.status in runs.TERMINAL_STATUSES]
    assert finished == [f"done-{i:02d}" for i in range(5, 25)]
    assert run.run_id in manager._runs

    assert await _wait_for(lambda: run.task.done(), timeout=3.0)
    assert run.status == "completed"
    # The request's history and document-expanded query are released; the snapshot never had them.
    assert run.history == []
    assert run.query == ""
    snapshot = await manager.snapshot(run)
    assert "history" not in snapshot and "query" not in snapshot
    assert snapshot["content"] == "big question"


@pytest.mark.anyio
async def test_restart_mid_run_leaves_conversation_idle_with_user_message(monkeypatch, tmp_path):
    """Review Focus 4: after a restart, a conversation whose run was live is idle and keeps the user message."""
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    progress_map = {}
    monkeypatch.setattr(main, "_active_runs", progress_map)
    manager = runs.RunManager(progress=progress_map)
    monkeypatch.setattr(main, "RUN_MANAGER", manager)

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        await asyncio.Event().wait()  # the model never answers before the "restart"
        yield {"model": "m1", "response": "A", "error": None}

    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-restart")
    storage.add_user_message(conv["id"], "earlier")
    storage.add_assistant_message(conv["id"], [{"model": "m1", "response": "old", "error": None}], None, None, {})

    run = await manager.start_run(conversation_id=conv["id"], content="in flight", execution_mode="full")
    assert await _wait_for(lambda: any(e["type"] == "stage1_init" for e in run.events), timeout=3.0)
    assert (await main.get_active_conversation_run(conv["id"]))["active_run"]["run_id"] == run.run_id

    # "Restart": the process comes back with an empty manager and progress map.
    monkeypatch.setattr(main, "_active_runs", {})
    monkeypatch.setattr(main, "RUN_MANAGER", runs.RunManager(progress=main._active_runs))
    try:
        assert await main.get_active_conversation_run(conv["id"]) == {"active_run": None}
        assert await main.get_conversation_progress(conv["id"]) == {"active": False}
        messages = storage.get_conversation(conv["id"])["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[-1]["content"] == "in flight"
    finally:
        # Stop the orphaned task the "old process" left behind.
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)


@pytest.mark.anyio
async def test_run_interrupted_inside_cancel_handler_still_ends_cancelled(monkeypatch, tmp_path):
    """A second hard cancel of the task (e.g. at shutdown) never leaves the conversation busy."""
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()

    async def _title(_query, **_kwargs):
        await asyncio.Event().wait()

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 2
        yield {"model": "m1", "response": "A", "error": None}
        await asyncio.Event().wait()
        yield {"model": "m2", "response": "B", "error": None}

    monkeypatch.setattr(runs, "generate_conversation_title", _title)
    monkeypatch.setattr(runs, "stage1_collect_responses", _stage1_collect)
    conv = storage.create_conversation("c-run-double-cancel")

    run = await manager.start_run(conversation_id=conv["id"], content="first", execution_mode="chat_only")
    assert await _wait_for(lambda: len(run.stage1_results) == 1, timeout=3.0)
    await manager.cancel_run(run.run_id)
    await asyncio.sleep(0.05)  # in the handler's title wait
    run.task.cancel()
    await asyncio.gather(run.task, return_exceptions=True)

    assert run.status == "cancelled"
    assert run.events[-1] == {"type": "cancelled"}
    assert await manager.get_active_run_for_conversation(conv["id"]) is None
    # The partial turn was saved before the title wait.
    assert storage.get_conversation(conv["id"])["messages"][1]["aborted"] is True
