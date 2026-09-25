from fastapi.testclient import TestClient

from backend import main, settings_payload
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
    # Upstream passes mode= to create_conversation.
    monkeypatch.setattr(main.storage, "create_conversation", lambda _cid, **_kwargs: conversation)

    resp = client.post("/api/conversations", json={})
    assert resp.status_code == 200
    assert resp.json()["id"] == "fixed-id"


def test_get_conversations_happy_path(monkeypatch):
    data = [{"id": "c1", "created_at": "2026-01-01", "title": "t", "message_count": 1}]
    monkeypatch.setattr(main.storage, "list_conversations", lambda: data)

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(data)
    # Upstream adds mode, run_summary and cost fields to each item.
    for item, expected in zip(body, data):
        assert expected.items() <= item.items()


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
    # Upstream builds the payload in settings_payload and reads key flags
    # from the credential store (has_secret), not plaintext settings fields.
    monkeypatch.setattr(
        settings_payload,
        "get_settings",
        lambda: _settings_with(
            council_models=["m1", "m2"],
            chairman_model="m1",
        ),
    )
    monkeypatch.setattr(
        settings_payload,
        "_key_set",
        lambda secret_id: secret_id == "api:openrouter",
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


def test_put_settings_accepts_requesty_key(monkeypatch):
    # PUT routes key fields through credentials.apply_settings_secret_updates,
    # which calls store.set_secret; the key never reaches update_settings.
    saved = {}
    monkeypatch.setattr("backend.credentials.store.set_secret", lambda sid, v: saved.__setitem__(sid, v))
    resp = client.put("/api/settings", json={"requesty_api_key": "rq-x"})
    assert resp.status_code == 200
    assert saved.get("api:requesty") == "rq-x"


def test_get_settings_reports_requesty_key_set():
    body = client.get("/api/settings").json()
    assert "requesty_api_key_set" in body
    assert "requesty_api_key" not in body


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


def test_stream_endpoint_invalid_execution_mode_is_rejected(monkeypatch):
    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: {"messages": []})
    resp = client.post(
        "/api/conversations/c1/message/stream",
        json={"content": "hello", "execution_mode": "invalid"},
    )
    # Upstream validates execution_mode with a pydantic Literal, so FastAPI returns 422.
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("execution_mode" in err.get("loc", []) for err in detail)


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


def test_active_run_after_restart_reports_no_active_run(monkeypatch):
    # Runs live in memory, so a fresh manager is what the backend has after a restart.
    # The fork's contract (kept): 200 {"active_run": null} for a known conversation
    # with no live run, 404 only when the conversation itself is missing. Never 500.
    monkeypatch.setattr(
        main.storage,
        "get_conversation",
        lambda cid: {"id": cid, "messages": [{"role": "user", "content": "hi"}]},
    )
    monkeypatch.setattr(main, "RUN_MANAGER", main.RunManager())
    resp = client.get("/api/conversations/c1/runs/active")
    assert resp.status_code == 200
    assert resp.json() == {"active_run": None}

    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: None)
    resp = client.get("/api/conversations/missing/runs/active")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found"


def test_unknown_run_routes_are_404(monkeypatch):
    monkeypatch.setattr(main, "RUN_MANAGER", main.RunManager())
    for method, path in [
        ("get", "/api/runs/nope"),
        ("get", "/api/runs/nope/stream"),
        ("post", "/api/runs/nope/cancel"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 404, path
        assert resp.json()["detail"] == "Run not found"


def test_start_run_rejects_bad_mode(monkeypatch):
    monkeypatch.setattr(main.storage, "get_conversation", lambda cid: {"id": cid, "messages": []})
    resp = client.post("/api/conversations/c1/runs", json={"content": "q", "execution_mode": "bogus"})
    # The body is upstream's SendMessageRequest, whose Literal execution_mode yields 422.
    assert resp.status_code == 422
    assert any("execution_mode" in err.get("loc", []) for err in resp.json()["detail"])


def test_start_run_passes_request_options_and_maps_errors(monkeypatch):
    calls = []

    class _Manager:
        def __init__(self, error=None):
            self.error = error

        async def start_run(self, **kwargs):
            calls.append(kwargs)
            if self.error:
                raise self.error
            return "run-object"

        async def snapshot(self, run):
            return {"run_id": "r1", "status": "queued", "wrapped": run}

    monkeypatch.setattr(main, "RUN_MANAGER", _Manager())
    body = {
        "content": "q",
        "execution_mode": "chat_ranking",
        "search_provider": "tavily",
        "council_models": ["openai:a", "requesty:openai/b"],
        "chairman_model": "anthropic:c",
        "documents": [{"name": "n.txt", "mime_type": "text/plain", "text": "t"}],
    }
    resp = client.post("/api/conversations/c1/runs", json=body)
    assert resp.status_code == 200
    assert resp.json() == {"run_id": "r1", "status": "queued", "wrapped": "run-object"}
    assert calls[-1] == {
        "conversation_id": "c1",
        "content": "q",
        "web_search": False,
        "execution_mode": "chat_ranking",
        "search_provider": "tavily",
        "council_models": ["openai:a", "requesty:openai/b"],
        "chairman_model": "anthropic:c",
        "documents": [{"name": "n.txt", "mime_type": "text/plain", "text": "t"}],
    }

    for error, status in [
        (ValueError("bad document"), 400),
        (LookupError("Conversation not found"), 404),
        (RuntimeError("Conversation already has an active run"), 409),
    ]:
        monkeypatch.setattr(main, "RUN_MANAGER", _Manager(error))
        resp = client.post("/api/conversations/c1/runs", json={"content": "q"})
        assert resp.status_code == status
        assert resp.json()["detail"] == str(error)


def _manager_with_live_run(conversation_id):
    from backend import runs

    manager = main.RunManager()
    run = runs.RunState(
        run_id="live-run",
        conversation_id=conversation_id,
        content="q",
        web_search=False,
        execution_mode="full",
        is_first_message=False,
        status="running",
    )
    manager._runs[run.run_id] = run
    manager._active_by_conversation[conversation_id] = run.run_id
    return manager


def test_upstream_turn_routes_refuse_while_a_run_is_live(monkeypatch):
    """Upstream's per-conversation POSTs would overwrite a /runs run's progress and saved turn."""
    monkeypatch.setattr(main, "RUN_MANAGER", _manager_with_live_run("c-live"))
    # Without the guard these routes would reach storage; a missing conversation makes that a 404.
    monkeypatch.setattr(main.storage, "get_conversation", lambda _cid: None)
    message_body = {"content": "q"}
    debate_body = {"question": "q", "persona_ids": ["a", "b"]}
    for path, body in [
        ("/api/conversations/c-live/message/stream", message_body),
        ("/api/conversations/c-live/message/debate", message_body),
        ("/api/conversations/c-live/message", message_body),
        ("/api/conversations/c-live/debate/stream", debate_body),
    ]:
        resp = client.post(path, json=body, headers={"Origin": "http://localhost:5173"})
        assert resp.status_code == 409, path
        assert resp.json() == {"detail": "Conversation already has an active run"}
        # Inside the CORS middleware, so the browser can read the 409.
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173", path

    # Other conversations, and reads of this one, are not affected.
    assert client.post("/api/conversations/c-other/message/stream", json=message_body).status_code == 404
    assert client.get("/api/conversations/c-live/runs/active").status_code == 404


def test_start_run_refuses_while_upstream_stream_is_registered(monkeypatch):
    """The other direction: a /runs start is refused while an upstream stream owns the conversation."""
    monkeypatch.setattr(main, "RUN_MANAGER", main.RunManager(progress=main._active_runs))
    monkeypatch.setattr(main.storage, "get_conversation", lambda cid: {"id": cid, "messages": []})
    add_calls = []
    monkeypatch.setattr(main.storage, "add_user_message", lambda *a, **k: add_calls.append(a))
    entry = {"mode": "council", "stage": "stage1", "execution_mode": "full", "progress": {}}
    monkeypatch.setitem(main._active_runs, "c-stream", entry)

    resp = client.post("/api/conversations/c-stream/runs", json={"content": "q"})
    assert resp.status_code == 409
    assert resp.json() == {"detail": "Conversation already has an active run"}
    assert main._active_runs["c-stream"] is entry
    assert add_calls == []
