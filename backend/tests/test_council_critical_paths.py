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
