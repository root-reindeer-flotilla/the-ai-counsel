import asyncio
import time
import inspect

import pytest
from backend import main, runs, storage


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


@pytest.mark.anyio
async def test_run_continues_without_stream_client(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    manager = runs.RunManager()
    monkeypatch.setattr(main, "RUN_MANAGER", manager)
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q: asyncio.sleep(0, result="Title"))

    async def _stage1_collect(_query, _search_context="", _request=None):
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
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q: asyncio.sleep(0, result="Title"))

    async def _stage1_collect(_query, _search_context="", _request=None):
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
    monkeypatch.setattr(runs, "generate_conversation_title", lambda _q: asyncio.sleep(0, result="Title"))
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

    async def _stage1_collect(_query, _search_context="", _request=None):
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

    async def _stage2_collect(_query, _stage1_results, _search_context="", _request=None):
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
