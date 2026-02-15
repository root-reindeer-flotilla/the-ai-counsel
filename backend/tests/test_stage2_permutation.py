from types import SimpleNamespace

import pytest

from backend import council


def _fake_candidates(count: int):
    return [
        {"candidate_id": f"candidate_{idx + 1:02d}", "model": f"model-{idx + 1}", "text": f"text-{idx + 1}"}
        for idx in range(count)
    ]


def test_cyclic_scheduler_balanced_positions_for_common_sizes():
    for count in (2, 3, 5, 12):
        candidates = _fake_candidates(count)
        evaluators = [candidate["model"] for candidate in candidates]
        schedule = council._deterministic_cyclic_orders(
            candidates,
            evaluators,
            seed_key=f"seed-{count}",
        )

        assert set(schedule.keys()) == set(evaluators)
        candidate_ids = {candidate["candidate_id"] for candidate in candidates}

        for ordered in schedule.values():
            assert len(ordered) == count
            assert {item["candidate_id"] for item in ordered} == candidate_ids

        # When evaluators == candidates, each position should contain each candidate exactly once.
        for position in range(count):
            exposure = {schedule[evaluator][position]["candidate_id"] for evaluator in evaluators}
            assert exposure == candidate_ids


@pytest.mark.anyio
async def test_stage2_uses_per_evaluator_label_maps_and_normalizes_to_canonical(monkeypatch):
    settings = SimpleNamespace(
        stage2_prompt="{responses_text}\n\nFINAL RANKING:",
        stage2_temperature=0.3,
    )
    stage1_results = [
        {"model": "requesty:model-a", "response_prompt_safe": "Alpha", "error": None},
        {"model": "requesty:model-b", "response_prompt_safe": "Bravo", "error": None},
        {"model": "requesty:model-c", "response_prompt_safe": "Charlie", "error": None},
    ]

    async def _fake_query_model(model, messages, timeout=120.0, temperature=0.7):
        return {
            "content": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C",
            "error": False,
        }

    monkeypatch.setattr(council, "get_settings", lambda: settings)
    monkeypatch.setattr(council, "query_model", _fake_query_model)

    items = []
    async for item in council.stage2_collect_rankings("Which is best?", stage1_results):
        items.append(item)

    init_payload = items[0]
    results = items[1:]
    stage2_candidate_maps = init_payload["stage2_candidate_maps_by_evaluator"]

    # Response A should not always map to the same candidate across evaluators.
    response_a_candidates = {
        per_eval_map["Response A"] for per_eval_map in stage2_candidate_maps.values()
    }
    assert len(response_a_candidates) > 1

    for result in results:
        assert result["parsed_ranking_local"] == ["Response A", "Response B", "Response C"]
        assert len(result["parsed_ranking_candidate_ids"]) == 3
        assert len(result["parsed_ranking_models"]) == 3

    aggregate = council.calculate_aggregate_rankings(results, init_payload["label_to_model"])
    # Under cyclic permutations with all evaluators ranking local A,B,C, all candidates tie.
    by_model = {item["model"]: item for item in aggregate}
    assert by_model["requesty:model-a"]["average_rank"] == 2.0
    assert by_model["requesty:model-b"]["average_rank"] == 2.0
    assert by_model["requesty:model-c"]["average_rank"] == 2.0
