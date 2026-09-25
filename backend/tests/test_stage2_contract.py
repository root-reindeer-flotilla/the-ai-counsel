from types import SimpleNamespace

import pytest

from backend import council


def _settings():
    return SimpleNamespace(
        stage2_prompt="{responses_text}\n\nFINAL RANKING:",
        stage2_temperature=0.3,
        response_language=None,
    )


def _stage1(n):
    return [
        {"model": f"requesty:model-{chr(97 + i)}", "response": f"answer {i}", "error": None}
        for i in range(n)
    ]


async def _collect(stage1, **kwargs):
    items = []
    async for item in council.stage2_collect_rankings("Q?", stage1, **kwargs):
        items.append(item)
    return items[0], items[1:]


@pytest.fixture
def fake_llm(monkeypatch):
    replies = {}

    async def _fake_query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms=None):
        return {"content": replies.get(model, replies.get("*")), "error": False}

    monkeypatch.setattr(council, "get_settings", _settings)
    monkeypatch.setattr(council, "query_model", _fake_query_model)
    return replies


@pytest.mark.anyio
async def test_first_yield_is_flat_label_map(fake_llm):
    fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
    first, results = await _collect(_stage1(3))
    assert isinstance(first, dict) and "model" not in first
    assert first == {
        "Response A": "requesty:model-a",
        "Response B": "requesty:model-b",
        "Response C": "requesty:model-c",
    }
    assert all(isinstance(v, str) for v in first.values())
    assert len(results) == 3


@pytest.mark.anyio
async def test_parsed_ranking_is_global_and_matches_models(fake_llm):
    fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
    first, results = await _collect(_stage1(3))
    for r in results:
        assert [first[label] for label in r["parsed_ranking"]] == r["parsed_ranking_models"]
        assert [r["stage2_label_map"][label] for label in r["parsed_ranking_local"]] == r["parsed_ranking_models"]


@pytest.mark.anyio
async def test_unknown_and_duplicate_labels_are_dropped(fake_llm):
    fake_llm["*"] = "FINAL RANKING:\n1. Response Z\n2. Response B\n3. Response B\n4. Response A"
    first, results = await _collect(_stage1(3))
    for r in results:
        assert r["parsed_ranking_local"] == ["Response B", "Response A"]
        assert len(r["parsed_ranking"]) == 2
        assert set(r["parsed_ranking"]) <= set(first)


@pytest.mark.anyio
async def test_single_successful_response(fake_llm):
    fake_llm["*"] = "FINAL RANKING:\n1. Response A"
    stage1 = _stage1(2)
    stage1[1]["error"] = True
    first, results = await _collect(stage1)
    assert first == {"Response A": "requesty:model-a"}
    assert [r["model"] for r in results] == ["requesty:model-a"]
    assert results[0]["parsed_ranking"] == ["Response A"]


@pytest.mark.anyio
async def test_balanced_order_false_uses_one_label_space(fake_llm):
    fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
    first, results = await _collect(_stage1(3), balanced_order=False)
    for r in results:
        assert r["stage2_label_map"] == first
        assert r["parsed_ranking"] == r["parsed_ranking_local"]


@pytest.mark.anyio
async def test_prompt_override_implies_canonical_order(monkeypatch):
    prompts = {}

    async def _fake_query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms=None):
        prompts[model] = [m["content"] for m in messages]
        return {"content": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "error": False}

    monkeypatch.setattr(council, "get_settings", _settings)
    monkeypatch.setattr(council, "query_model", _fake_query_model)

    first, results = await _collect(_stage1(3), prompt_override="PREBUILT")
    assert set(prompts) == set(first.values())
    for sent in prompts.values():
        assert sent == ["PREBUILT"]
    for r in results:
        assert r["stage2_label_map"] == first
        assert r["parsed_ranking"] == r["parsed_ranking_local"]


@pytest.mark.anyio
async def test_stage3_chairman_prompt_uses_global_labels(monkeypatch):
    stage1 = _stage1(3)
    # Evaluator saw a rotated order: its local A/B/C are global B/C/A. A chained
    # replace (A->B, then B->C, ...) would collapse labels; one pass must not.
    stage2 = [
        {
            "model": "requesty:model-a",
            "ranking": "Response A is strongest; Response C is weakest.\n\n"
                       "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C",
            "parsed_ranking": ["Response B", "Response C", "Response A"],
            "stage2_label_map": {
                "Response A": "requesty:model-b",
                "Response B": "requesty:model-c",
                "Response C": "requesty:model-a",
            },
            "error": None,
        },
    ]
    captured = []

    async def _fake_query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms=None):
        captured.extend(m["content"] for m in messages)
        return {"content": "Final", "error": False}

    monkeypatch.setattr(
        council,
        "get_settings",
        lambda: SimpleNamespace(
            stage3_prompt="{stage1_text}\n---\n{stage2_text}",
            chairman_temperature=0.4,
            response_language=None,
        ),
    )
    monkeypatch.setattr(council, "query_model", _fake_query_model)
    monkeypatch.setattr(council, "get_chairman_model", lambda: "requesty:chair")

    await council.stage3_synthesize_final("Q?", stage1, stage2)

    prompt = captured[-1]
    stage2_part = prompt.split("---", 1)[1]
    assert "Response B is strongest; Response A is weakest." in stage2_part
    assert "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A" in stage2_part


@pytest.mark.anyio
async def test_debate_requests_one_label_space_every_round():
    from unittest.mock import MagicMock, patch

    from backend.debate import run_iterative_debate

    settings = MagicMock()
    settings.debate_rounds = 2
    settings.auto_converge = False
    settings.convergence_threshold = 2
    settings.critique_mode = "freeform"
    settings.council_temperature = 0.5
    settings.stage2_temperature = 0.3
    settings.chairman_temperature = 0.4
    settings.stage1_prompt = None
    settings.stage2_prompt = None
    settings.stage3_prompt = None
    settings.council_models = ["model_a", "model_b"]

    def fake_stage1(*args, **kwargs):
        async def gen():
            yield 2
            yield {"model": "model_a", "response": "Answer A", "error": None}
            yield {"model": "model_b", "response": "Answer B", "error": None}
        return gen()

    stage2_calls = []

    def fake_stage2(*args, **kwargs):
        stage2_calls.append(kwargs)

        async def gen():
            yield {"Response A": "model_a", "Response B": "model_b"}
            yield {"model": "model_a", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B",
                   "parsed_ranking": ["Response A", "Response B"], "error": None}
        return gen()

    async def fake_stage3(*args, **kwargs):
        return {"model": "chair", "response": "Synthesis", "error": False}

    with patch("backend.debate.get_settings", return_value=settings), \
         patch("backend.debate.stage1_collect_responses", side_effect=fake_stage1), \
         patch("backend.debate.stage2_collect_rankings", side_effect=fake_stage2), \
         patch("backend.debate.stage3_synthesize_final", side_effect=fake_stage3):
        async for _ in run_iterative_debate("test?", "", None, "full", debate_rounds=2):
            pass

    assert len(stage2_calls) == 2
    assert all(call.get("balanced_order") is False for call in stage2_calls)
