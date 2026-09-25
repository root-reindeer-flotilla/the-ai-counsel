from unittest.mock import AsyncMock, patch

import pytest

from backend import council
from backend.providers.temperature import resolve_temperature, should_force_temperature_one


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("openrouter:google/gemini-3-pro-preview", True),
        ("openrouter:google/gemini-3-flash-preview", True),
        ("google:gemini-3-flash-preview", True),
        ("openrouter:google/gemini-2.5-flash", True),
        ("requesty:google/gemini-2.5-flash", True),
        ("openrouter:x-ai/grok-4.1-fast", True),
        ("openrouter:z-ai/glm-5", True),
        ("openrouter:minimax/minimax-m2.5", True),
        ("openrouter:openai/gpt-oss-120b", True),
        ("openrouter:openai/gpt-oss-20b:free", True),
        ("openrouter:openai/gpt-4o-mini", False),
        ("google:gemini-2.5-flash-lite", False),
        ("", False),
    ],
)
def test_should_force_temperature_one_by_model_id(model_id, expected):
    assert should_force_temperature_one(model_id) is expected


def test_resolve_temperature_keeps_requested_value_for_regular_models():
    assert resolve_temperature("openai:gpt-4.1", 0.27) == 0.27
    assert resolve_temperature("openrouter:z-ai/glm-5", 0.27) == 1.0


@pytest.mark.parametrize(
    ("model_id", "expected_temperature"),
    [
        ("openrouter:google/gemini-3-pro-preview", 1.0),
        ("openrouter:z-ai/glm-5", 1.0),
        ("openrouter:openai/gpt-oss-120b", 1.0),
        ("openrouter:openai/gpt-4o-mini", 0.27),
    ],
)
@pytest.mark.anyio
async def test_query_model_applies_forced_temperature(model_id, expected_temperature):
    provider = type("DummyProvider", (), {})()
    provider.query = AsyncMock(return_value={"content": "ok", "error": False})

    with patch("backend.council.get_provider_for_model", return_value=provider), \
         patch("backend.council.attach_cost", new=AsyncMock(side_effect=lambda m, r: r)):
        await council.query_model(model_id, [{"role": "user", "content": "hi"}], timeout=5.0, temperature=0.27)

    assert provider.query.await_count == 1
    _, _, _, passed_temperature = provider.query.await_args.args
    assert passed_temperature == expected_temperature
