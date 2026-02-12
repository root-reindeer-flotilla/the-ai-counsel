from unittest.mock import AsyncMock, patch

import pytest

from backend import council


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("openrouter:google/gemini-3-pro-preview", True),
        ("openrouter:google/gemini-3-flash-preview", True),
        ("google:gemini-3-flash-preview", True),
        ("openrouter:google/gemini-2.5-flash", True),
        ("openrouter:deepseek/deepseek-v3.2", True),
        ("openrouter:deepseek/deepseek-v3.2-exp", True),
        ("requesty:arcee-ai/trinity-large-preview:free", True),
        ("openrouter:x-ai/grok-4.1-fast", True),
        ("openrouter:z-ai/glm-5", True),
        ("openrouter:minimax/minimax-m2.5", True),
        ("openrouter:openai/gpt-4o-mini", False),
        ("google:gemini-2.5-flash-lite", False),
    ],
)
def test_should_force_temperature_one_by_model_id(model_id, expected):
    assert council._should_force_temperature_one(model_id) is expected


@pytest.mark.parametrize(
    ("model_id", "expected_temperature"),
    [
        ("openrouter:google/gemini-3-pro-preview", 1.0),
        ("openrouter:deepseek/deepseek-v3.2-speciale", 1.0),
        ("openrouter:z-ai/glm-5", 1.0),
        ("openrouter:openai/gpt-4o-mini", 0.27),
    ],
)
@pytest.mark.anyio
async def test_query_model_applies_forced_temperature(model_id, expected_temperature):
    provider = type("DummyProvider", (), {})()
    provider.query = AsyncMock(return_value={"content": "ok", "error": False})

    with patch("backend.council.get_provider_for_model", return_value=provider):
        await council.query_model(
            model_id,
            [{"role": "user", "content": "hi"}],
            temperature=0.27,
        )

    assert provider.query.await_count == 1
    _, _, _, passed_temperature = provider.query.await_args.args
    assert passed_temperature == expected_temperature
