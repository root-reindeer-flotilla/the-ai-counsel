from backend import council
import pytest


def test_parse_ranking_falls_back_without_header():
    text = "I prefer Response C, then Response A, then Response B."
    parsed = council.parse_ranking_from_text(text)
    assert parsed == ["Response C", "Response A", "Response B"]


def test_parse_ranking_truncates_to_expected_count():
    text = (
        "FINAL RANKING:\n"
        "1. Response A\n"
        "2. Response A\n"
        "3. Response B\n"
        "4. Response C\n"
    )
    parsed = council.parse_ranking_from_text(text, expected_count=2)
    assert parsed == ["Response A", "Response A"]


def test_parse_ranking_coerces_non_string_input():
    parsed = council.parse_ranking_from_text(None)
    assert parsed == []


def test_parse_ranking_handles_malformed_numbering():
    text = (
        "FINAL RANKING:\n"
        "1) Response B\n"
        "- Response C\n"
        "3 Response A\n"
    )
    parsed = council.parse_ranking_from_text(text)
    assert parsed == ["Response B", "Response C", "Response A"]


def test_strip_thinking_tags_removes_think_blocks():
    text = "Intro\n<think>hidden reasoning</think>\nAnswer"
    assert council.strip_thinking_tags(text) == "Intro\n\nAnswer"


def test_normalize_thinking_content_combines_reasoning_and_sanitizes_prompt():
    normalized = council.normalize_thinking_content(
        "Visible answer",
        reasoning="structured reasoning",
        reasoning_details=[{"type": "reasoning.text", "text": "detail reasoning"}],
    )
    assert "<think>" in normalized["display_text"]
    assert "structured reasoning" in normalized["display_text"]
    assert "detail reasoning" in normalized["display_text"]
    assert normalized["prompt_safe_text"] == "Visible answer"


@pytest.mark.anyio
async def test_stage2_prompt_uses_prompt_safe_stage1_text(monkeypatch):
    stage1_results = [
        {
            "model": "openai:model-a",
            "response": "<think>hidden A</think>\n\nPublic A",
            "response_prompt_safe": "Public A",
            "error": None,
        },
        {
            "model": "openai:model-b",
            "response": "<think>hidden B</think>\n\nPublic B",
            "response_prompt_safe": "Public B",
            "error": None,
        },
    ]
    captured_prompts = []

    async def _fake_query_model(model, messages, timeout=120.0, temperature=0.7):
        captured_prompts.append(messages[0]["content"])
        return {
            "content": "FINAL RANKING:\n1. Response A\n2. Response B",
            "error": False,
        }

    monkeypatch.setattr(council, "query_model", _fake_query_model)

    items = []
    async for item in council.stage2_collect_rankings("Which is best?", stage1_results):
        items.append(item)

    assert len(items) == 3  # label map + 2 model rankings
    assert captured_prompts
    for prompt in captured_prompts:
        assert "<think>" not in prompt
        assert "hidden A" not in prompt
        assert "hidden B" not in prompt
        assert "Public A" in prompt
        assert "Public B" in prompt


@pytest.mark.anyio
async def test_stage3_prompt_strips_thinking_from_stage1_and_stage2_inputs(monkeypatch):
    stage1_results = [
        {
            "model": "openai:model-a",
            "response": "<think>s1 hidden</think>\n\nS1 visible",
            "response_prompt_safe": "S1 visible",
            "error": None,
        }
    ]
    stage2_results = [
        {
            "model": "openai:model-a",
            "ranking": "<think>s2 hidden</think>\n\nFINAL RANKING:\n1. Response A",
            "ranking_prompt_safe": "FINAL RANKING:\n1. Response A",
            "error": None,
        }
    ]
    captured_messages = []

    async def _fake_query_model(model, messages, timeout=120.0, temperature=0.7):
        captured_messages.extend(messages)
        return {
            "content": "Final answer",
            "reasoning": "Chairman reasoning",
            "error": False,
        }

    monkeypatch.setattr(council, "query_model", _fake_query_model)
    monkeypatch.setattr(council, "get_chairman_model", lambda: "openai:chair")

    result = await council.stage3_synthesize_final(
        "Question?",
        stage1_results,
        stage2_results,
    )

    assert result["error"] is False
    assert "<think>" in result["response"]
    assert captured_messages
    prompt_blob = "\n\n".join(msg.get("content", "") for msg in captured_messages)
    assert "s1 hidden" not in prompt_blob
    assert "s2 hidden" not in prompt_blob
    assert "<think>" not in prompt_blob
    assert "S1 visible" in prompt_blob
    assert "FINAL RANKING:\n1. Response A" in prompt_blob


@pytest.mark.anyio
async def test_generate_conversation_title_empty_returns_default():
    assert await council.generate_conversation_title("") == "Untitled Conversation"


@pytest.mark.anyio
async def test_generate_conversation_title_quotes_only_returns_default():
    assert await council.generate_conversation_title('""') == "Untitled Conversation"


@pytest.mark.anyio
async def test_generate_conversation_title_truncates_long_input():
    query = "a" * 80
    title = await council.generate_conversation_title(query)
    assert len(title) == 50
    assert title.endswith("...")
