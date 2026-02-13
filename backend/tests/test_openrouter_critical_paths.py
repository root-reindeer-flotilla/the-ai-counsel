import pytest
from unittest.mock import patch

from backend import openrouter


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise openrouter.httpx.HTTPStatusError(
                "error",
                request=openrouter.httpx.Request("POST", openrouter.OPENROUTER_API_URL),
                response=openrouter.httpx.Response(self.status_code, json=self._payload),
            )


class _FakeAsyncClient:
    def __init__(self, responses, payloads_store):
        self._responses = list(responses)
        self._payloads_store = payloads_store

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self._payloads_store.append(json)
        return self._responses.pop(0)


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("openrouter:google/gemini-3-pro-preview", "google/gemini-3-pro-preview"),
        ("google/gemini-3-pro-preview", "google/gemini-3-pro-preview"),
        ("", ""),
    ],
)
def test_strip_openrouter_prefix(model_id, expected):
    assert openrouter._strip_openrouter_prefix(model_id) == expected


@pytest.mark.anyio
async def test_query_model_adds_reasoning_effort_for_gemini3_targets():
    payloads = []
    ok_response = _FakeResponse(
        200,
        {
            "id": "resp_1",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 10},
        },
    )

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient([ok_response], payloads)

    with patch("backend.openrouter.get_openrouter_api_key", return_value="test-key"):
        with patch("backend.openrouter.httpx.AsyncClient", side_effect=_client_factory):
            result = await openrouter.query_model(
                "google/gemini-3-pro-preview",
                [{"role": "user", "content": "hi"}],
            )

    assert result["error"] is None
    assert payloads[0]["reasoning"] == {"effort": "high"}


@pytest.mark.anyio
async def test_query_model_does_not_add_reasoning_effort_for_non_targets():
    payloads = []
    ok_response = _FakeResponse(
        200,
        {
            "id": "resp_1",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 10},
        },
    )

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient([ok_response], payloads)

    with patch("backend.openrouter.get_openrouter_api_key", return_value="test-key"):
        with patch("backend.openrouter.httpx.AsyncClient", side_effect=_client_factory):
            result = await openrouter.query_model(
                "openai/gpt-4o-mini",
                [{"role": "user", "content": "hi"}],
            )

    assert result["error"] is None
    assert "reasoning" not in payloads[0]


@pytest.mark.anyio
async def test_query_model_adds_reasoning_enabled_for_deepseek_v32():
    """DeepSeek V3.2 on OpenRouter gets reasoning.enabled=True to turn on thinking mode."""
    payloads = []
    ok_response = _FakeResponse(
        200,
        {
            "id": "resp_1",
            "choices": [{"message": {"content": "ok", "reasoning": "internal thought"}}],
            "usage": {"total_tokens": 10},
        },
    )

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient([ok_response], payloads)

    with patch("backend.openrouter.get_openrouter_api_key", return_value="test-key"):
        with patch("backend.openrouter.httpx.AsyncClient", side_effect=_client_factory):
            result = await openrouter.query_model(
                "openrouter:deepseek/deepseek-v3.2",
                [{"role": "user", "content": "hi"}],
            )

    assert result["error"] is None
    assert payloads[0]["reasoning"] == {"enabled": True}


@pytest.mark.anyio
async def test_bad_request_overflow_sets_context_overflow_flag():
    payloads = []
    bad_request = _FakeResponse(
        400,
        {
            "error": {
                "message": "Prompt exceeds model context length.",
                "code": "context_length_exceeded",
            }
        },
    )

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient([bad_request], payloads)

    with patch("backend.openrouter.get_openrouter_api_key", return_value="test-key"):
        with patch("backend.openrouter.httpx.AsyncClient", side_effect=_client_factory):
            result = await openrouter.query_model(
                "google/gemini-3-pro-preview",
                [{"role": "user", "content": "hi"}],
            )

    assert result["error"] == "bad_request"
    assert result["is_context_overflow"] is True
    assert result["error_code"] == "context_length_exceeded"
