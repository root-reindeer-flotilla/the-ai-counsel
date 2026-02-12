from fastapi.testclient import TestClient

from backend import main
from backend.settings import Settings


client = TestClient(main.app)


def _settings_with(**overrides):
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_post_conversations_happy_path(monkeypatch):
    conversation = {
        "id": "fixed-id",
        "created_at": "2026-01-01T00:00:00",
        "title": "New Conversation",
        "messages": [],
    }
    monkeypatch.setattr(main.uuid, "uuid4", lambda: "fixed-id")
    monkeypatch.setattr(main.storage, "create_conversation", lambda _cid: conversation)

    resp = client.post("/api/conversations", json={})
    assert resp.status_code == 200
    assert resp.json()["id"] == "fixed-id"


def test_get_conversations_happy_path(monkeypatch):
    data = [{"id": "c1", "created_at": "2026-01-01", "title": "t", "message_count": 1}]
    monkeypatch.setattr(main.storage, "list_conversations", lambda: data)

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert resp.json() == data


def test_get_conversation_missing_returns_404(monkeypatch):
    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: None)
    resp = client.get("/api/conversations/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found"


def test_delete_conversation_success_and_missing(monkeypatch):
    monkeypatch.setattr(main.storage, "delete_conversation", lambda _cid: True)
    resp_ok = client.delete("/api/conversations/c1")
    assert resp_ok.status_code == 200
    assert resp_ok.json() == {"status": "deleted"}

    monkeypatch.setattr(main.storage, "delete_conversation", lambda _cid: False)
    resp_missing = client.delete("/api/conversations/c1")
    assert resp_missing.status_code == 404


def test_get_settings_returns_shape_with_key_flags(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: _settings_with(
            openrouter_api_key="x",
            tavily_api_key=None,
            council_models=["m1", "m2"],
            chairman_model="m1",
        ),
    )

    resp = client.get("/api/settings")
    body = resp.json()
    assert resp.status_code == 200
    assert "search_provider" in body
    assert body["openrouter_api_key_set"] is True
    assert body["tavily_api_key_set"] is False
    assert body["council_models"] == ["m1", "m2"]


def test_put_settings_valid_update_path(monkeypatch):
    monkeypatch.setattr(main, "update_settings", lambda **kwargs: _settings_with(**kwargs))

    payload = {
        "search_provider": "duckduckgo",
        "search_keyword_extraction": "direct",
        "full_content_results": 3,
        "council_models": ["a", "b"],
        "execution_mode": "full",
    }
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["search_keyword_extraction"] == "direct"
    assert body["council_models"] == ["a", "b"]


def test_put_settings_invalid_provider_returns_400():
    resp = client.put("/api/settings", json={"search_provider": "not-a-provider"})
    assert resp.status_code == 400
    assert "Invalid search provider" in resp.json()["detail"]


def test_put_settings_invalid_execution_mode_returns_400():
    resp = client.put(
        "/api/settings",
        json={"council_models": ["a", "b"], "execution_mode": "bad-mode"},
    )
    assert resp.status_code == 400
    assert "Invalid execution_mode" in resp.json()["detail"]


def test_put_settings_invalid_council_model_count_returns_400():
    resp = client.put("/api/settings", json={"council_models": ["only-one"]})
    assert resp.status_code == 400
    assert "At least two council models" in resp.json()["detail"]


def test_stream_endpoint_invalid_execution_mode_returns_400(monkeypatch):
    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: {"messages": []})
    resp = client.post(
        "/api/conversations/c1/message/stream",
        json={"content": "hello", "execution_mode": "invalid"},
    )
    assert resp.status_code == 400
    assert "Invalid execution_mode" in resp.json()["detail"]


def test_stream_endpoint_missing_conversation_returns_404(monkeypatch):
    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: None)
    resp = client.post(
        "/api/conversations/missing/message/stream",
        json={"content": "hello", "execution_mode": "full"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found"


def test_openrouter_generation_success(monkeypatch):
    async def _fetch_generation(_id):
        return {"error": False, "data": {"id": "g1"}}

    monkeypatch.setattr("backend.openrouter.fetch_generation", _fetch_generation)
    resp = client.get("/api/openrouter/generation?id=g1")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": {"id": "g1"}}


def test_openrouter_generation_error(monkeypatch):
    async def _fetch_generation(_id):
        return {"error": True, "error_message": "boom"}

    monkeypatch.setattr("backend.openrouter.fetch_generation", _fetch_generation)
    resp = client.get("/api/openrouter/generation?id=g1")
    assert resp.status_code == 200
    assert resp.json() == {"success": False, "error": "boom"}
