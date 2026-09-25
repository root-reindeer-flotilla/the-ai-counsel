import asyncio
import time
import inspect

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
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q, **_kwargs: asyncio.sleep(0, result="Title"))
    monkeypatch.setattr(
        runs,
        "calculate_aggregate_rankings",
        lambda _stage2, _labels, return_diagnostics=False: (
            (
                [{"model": "openai:model-a", "average_rank": 1.5, "rankings_count": 1}],
                {"ballots_used": 1},
            )
            if return_diagnostics
            else [{"model": "openai:model-a", "average_rank": 1.5, "rankings_count": 1}]
        ),
    )

    async def _stage1_collect(_query, _search_context="", _request=None, **_kwargs):
        yield 1
        yield {
            "model": "openai:model-a",
            "response": "A",
            "error": None,
            "stage1_duration_ms": 3000,
            "stage1_total_tokens": 1200,
            "stage1_usage": {"cost": 0.12},
            "stage1_response_id": "s1",
        }

    async def _stage2_collect(_query, _stage1_results, _search_context="", _request=None, **_kwargs):
        yield {"Response A": "openai:model-a"}
        yield {
            "model": "openai:model-a",
            "ranking": "FINAL RANKING:\n1. Response A",
            "parsed_ranking": ["Response A"],
            "error": None,
            "stage2_duration_ms": 6000,
            "stage2_total_tokens": 29209,
            "stage2_usage": {"cost": 0.28},
            "stage2_response_id": "s2",
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
    assert row["generation_time_seconds"] == 9
    assert row["generation_total_tokens"] == 30409
    assert row["generation_total_cost"] == pytest.approx(0.40)

    saved = storage.get_conversation(conv["id"])
    assistant = saved["messages"][1]
    persisted_row = assistant["metadata"]["aggregate_rankings"][0]
    assert persisted_row["generation_time_seconds"] == 9
    assert persisted_row["generation_total_tokens"] == 30409


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
    # As for any cancelled run, the user message stays and an aborted assistant turn is saved.
    saved = storage.get_conversation(conv["id"])
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["aborted"] is True
    assert saved["messages"][1]["stage1"] == []


@pytest.mark.anyio
async def test_run_web_search_follows_upstream_query_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    settings = Settings()
    settings.search_provider = "duckduckgo"
    settings.search_keyword_extraction = "llm"
    monkeypatch.setattr(runs, "get_settings", lambda: settings)
    monkeypatch.setattr(runs, "get_api_key", lambda provider: f"key-{provider}")
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

    monkeypatch.setattr(runs, "generate_search_query", _generate_search_query)
    monkeypatch.setattr(runs, "perform_web_search", _perform_web_search)
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
    assert runs.os.environ["TAVILY_API_KEY"] == "key-tavily"
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
