"""Fork feature F1: Requesty.ai provider on upstream's credential/provider wiring."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend import council, costs, main, requesty
from backend import settings as settings_mod
from backend.credentials import file_backend, ids, relay_import, store, upgrade
from backend.providers.requesty import RequestyProvider
from backend.providers.temperature import split_upstream_model


client = TestClient(main.app)


@pytest.fixture()
def cred_file(tmp_path, monkeypatch, fake_keyring):
    # fake_keyring: delete/wipe hit both backends regardless of mode.
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(file_backend, "CREDENTIALS_FILE", path)
    monkeypatch.setattr(store, "get_effective_mode", lambda: "file")
    monkeypatch.setattr(store, "_preferred_mode", lambda: "file")
    monkeypatch.setattr(store, "ENV_OVERRIDES", {})
    yield path


def test_requesty_secret_is_registered():
    assert "api:requesty" in ids.KNOWN_SECRET_IDS
    assert ids.SETTINGS_FIELD_TO_SECRET_ID["requesty_api_key"] == "api:requesty"
    assert ids.ENV_OVERRIDES["api:requesty"] == "REQUESTY_API_KEY"


def test_requesty_routes_to_provider():
    assert isinstance(council.get_provider_for_model("requesty:openai/gpt-4o-mini"), RequestyProvider)
    assert council.get_provider_name_for_model("requesty:openai/gpt-4o-mini") == "requesty"


def test_requesty_prefix_is_internal():
    assert split_upstream_model("requesty:openai/gpt-5") == ("openai", "gpt-5")


def test_requesty_cost_is_estimate_only():
    assert "requesty" in costs._SUPPORTED_PROVIDER_PREFIXES
    assert costs.provider_for_model("requesty:openai/gpt-4o-mini") == "requesty"
    assert costs.provider_model_id("requesty:openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert costs._catalog_platform("requesty", "openai/gpt-4o-mini") is None


def test_store_key_is_read_by_config(cred_file):
    from backend.config import get_requesty_api_key

    store.set_secret("api:requesty", "rq-test")
    assert get_requesty_api_key() == "rq-test"


def test_env_key_is_read_by_config(monkeypatch, cred_file):
    from backend.config import get_requesty_api_key

    monkeypatch.setattr(store, "ENV_OVERRIDES", {"api:requesty": "REQUESTY_API_KEY"})
    monkeypatch.setenv("REQUESTY_API_KEY", "rq-env")
    assert get_requesty_api_key() == "rq-env"


def test_plaintext_key_in_settings_is_not_read_by_config(cred_file):
    """settings.json is never a key source; the store (after migration) is."""
    from backend.config import get_requesty_api_key

    # Written directly: update_settings would redact the field before the
    # getter could see it, which would make this test pass vacuously.
    settings_mod.SETTINGS_FILE.write_text(
        json.dumps({"requesty_api_key": "rq-plain", "credentials_migrated": True})
    )
    settings_mod._settings_cache = None
    settings_mod._settings_mtime = 0.0
    assert settings_mod.get_settings().requesty_api_key == "rq-plain"
    assert get_requesty_api_key() == ""


def test_plaintext_requesty_key_migrates_to_store(cred_file, monkeypatch):
    from backend.settings import Settings

    current = Settings(requesty_api_key="rq-plain", credentials_migrated=False)
    applied = {}
    monkeypatch.setattr(settings_mod, "get_settings", lambda: current)
    monkeypatch.setattr(settings_mod, "update_settings", lambda **kw: applied.update(kw))

    assert upgrade.ensure_credentials_upgraded() is True
    assert store.get_secret("api:requesty") == "rq-plain"
    assert applied["requesty_api_key"] is None
    assert applied["credentials_migrated"] is True


def test_fork_settings_file_migrates_on_first_settings_load(cred_file):
    """Review Focus 3: a fork settings.json with a plaintext Requesty key.

    The first GET /api/settings (what the UI does on start) moves the key into
    the credential store and rewrites settings.json without it.
    """
    settings_file = settings_mod.SETTINGS_FILE  # tmp path (conftest autouse)
    settings_file.write_text(json.dumps({
        "requesty_api_key": "rq-plain",
        "enabled_providers": {"openrouter": True, "requesty": True},
    }))

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["requesty_api_key_set"] is True
    assert "requesty_api_key" not in body

    assert file_backend.get_secret("api:requesty") == "rq-plain"
    on_disk = json.loads(settings_file.read_text())
    assert on_disk["requesty_api_key"] is None
    assert on_disk["credentials_migrated"] is True
    assert "rq-plain" not in settings_file.read_text()


def test_disconnect_all_wipes_requesty_and_blocks_env(cred_file, monkeypatch):
    """Review Focus 3: Disconnect All Providers clears api:requesty for good."""
    from backend.config import get_requesty_api_key

    monkeypatch.setattr(store, "ENV_OVERRIDES", dict(ids.ENV_OVERRIDES))
    monkeypatch.setenv("REQUESTY_API_KEY", "rq-env")
    settings_mod.update_settings(enabled_providers={"openrouter": True, "requesty": True})
    store.set_secret("api:requesty", "rq-stored")
    assert get_requesty_api_key() == "rq-stored"

    store.disconnect_all_credentials()

    assert file_backend.get_secret("api:requesty") is None
    assert "api:requesty" in settings_mod.get_settings().disabled_secret_ids
    assert settings_mod.get_settings().enabled_providers["requesty"] is False
    # The env var must not revive the key after Disconnect.
    assert get_requesty_api_key() == ""


def test_relay_import_enables_requesty_aggregator_toggle(monkeypatch):
    monkeypatch.setattr(relay_import, "is_container_environment", lambda: False)
    monkeypatch.setattr(relay_import, "has_secret", lambda _sid: False)
    stored = {}
    monkeypatch.setattr(relay_import, "set_secret", lambda sid, raw: stored.__setitem__(sid, raw))
    monkeypatch.setattr(
        relay_import,
        "_read_relay_keyring",
        lambda account: {"provider:requesty": "rq-imported"}.get(account),
    )
    updates = {}

    class FakeSettings:
        enabled_providers = {"direct": False, "openrouter": False, "requesty": False}
        direct_provider_toggles = {}

    monkeypatch.setattr("backend.settings.get_settings", lambda: FakeSettings())
    monkeypatch.setattr("backend.settings.update_settings", lambda **kw: updates.update(kw))

    result = relay_import.import_relay_ai_credentials(["requesty"], replace_existing=True)
    assert result["imported"] == ["requesty"]
    assert stored["api:requesty"] == "rq-imported"
    assert updates["enabled_providers"]["requesty"] is True
    # Requesty is an aggregator toggle, not a direct provider.
    assert updates["enabled_providers"]["direct"] is False
    assert "requesty" not in updates["direct_provider_toggles"]


class _FakeProvider:
    def __init__(self, models=None):
        self.models = models or []
        self.get_models_calls = 0
        self.validated = []

    async def get_models(self):
        self.get_models_calls += 1
        return list(self.models)

    async def validate_key(self, api_key):
        self.validated.append(api_key)
        return {"success": True, "message": "API key is valid"}


def test_direct_models_skip_requesty(monkeypatch):
    requesty = _FakeProvider([{"id": "requesty:x/y"}])
    direct = _FakeProvider([{"id": "openai:gpt-4.1"}])
    monkeypatch.setattr(main, "PROVIDERS", {"requesty": requesty, "openai": direct})

    resp = client.get("/api/models/direct")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()] == ["openai:gpt-4.1"]
    assert requesty.get_models_calls == 0


def test_requesty_models_route_prefixes_ids(monkeypatch):
    from backend import requesty as requesty_client

    async def _fetch_models():
        return [
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "source": "requesty"},
            {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "source": "requesty"},
        ]

    monkeypatch.setattr(requesty_client, "fetch_models", _fetch_models)
    resp = client.get("/api/models/requesty")
    assert resp.status_code == 200
    models = resp.json()["models"]
    assert [m["id"] for m in models] == [
        "requesty:anthropic/claude-sonnet-4",
        "requesty:openai/gpt-4o-mini",
    ]
    assert all(m["name"].endswith(" [Requesty]") for m in models)


def test_test_requesty_falls_back_to_stored_key(cred_file, monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setitem(council.PROVIDERS, "requesty", fake)

    resp = client.post("/api/settings/test-requesty", json={"api_key": ""})
    assert resp.json() == {"success": False, "message": "No API key provided or configured"}
    assert fake.validated == []

    store.set_secret("api:requesty", "rq-saved")
    resp = client.post("/api/settings/test-requesty", json={"api_key": ""})
    assert resp.json()["success"] is True
    resp = client.post("/api/settings/test-requesty", json={"api_key": "rq-typed"})
    assert resp.json()["success"] is True
    assert fake.validated == ["rq-saved", "rq-typed"]


# --- Review follow-up: request payload, errors, key test, costs -------------

def _ok_reply():
    return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("openai/gpt-5", None),
        ("azure/gpt-5", None),
        ("openai/o3:flex", None),
        ("openai/o1:medium", None),
        ("bedrock/claude-sonnet-4-5@us-east-1", None),
        ("vertex/gemini-2.5-flash@us-south1", 1.0),
        ("google/gemini-3-pro-preview:flex", 1.0),
        ("meta-llama/llama-3.1-8b:free", 0.3),
        ("openai/gpt-4o-mini", 0.3),
    ],
)
async def test_requesty_payload_follows_upstream_temperature_rules(monkeypatch, model_id, expected):
    monkeypatch.setattr(requesty, "get_requesty_api_key", lambda: "rq-test")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(requesty.REQUESTY_API_URL).mock(return_value=_ok_reply())
        result = await requesty.query_model(f"requesty:{model_id}", [{"role": "user", "content": "q"}], temperature=0.3)
    payload = json.loads(route.calls.last.request.content)
    assert payload["model"] == model_id
    assert payload.get("temperature") == expected
    assert result["content"] == "hi"


@pytest.mark.anyio
async def test_requesty_exception_without_message_is_an_error(monkeypatch):
    monkeypatch.setattr(requesty, "get_requesty_api_key", lambda: "rq-test")
    with respx.mock() as mock:
        mock.post(requesty.REQUESTY_API_URL).mock(side_effect=httpx.ReadError(""))
        result = await requesty.query_model("openai/gpt-4o-mini", [{"role": "user", "content": "q"}])
    assert result["content"] is None
    assert result["error"]
    assert result["error_message"]


@pytest.mark.anyio
async def test_requesty_string_error_body_is_reported(monkeypatch):
    monkeypatch.setattr(requesty, "get_requesty_api_key", lambda: "rq-test")
    with respx.mock() as mock:
        mock.post(requesty.REQUESTY_API_URL).mock(
            return_value=httpx.Response(400, json={"error": "temperature is not supported"})
        )
        result = await requesty.query_model("openai/gpt-4o-mini", [{"role": "user", "content": "q"}])
    assert result["error"] == "temperature is not supported"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403])
async def test_validate_key_reports_rejected_key_as_invalid(status):
    with respx.mock() as mock:
        mock.get(requesty.REQUESTY_MODELS_URL).mock(return_value=httpx.Response(status))
        result = await RequestyProvider().validate_key("rq-bad")
    assert result == {"success": False, "message": "Invalid API key"}


@pytest.mark.anyio
async def test_requesty_cost_is_a_medium_confidence_estimate(monkeypatch):
    catalog = {"data": {"models": [{
        "model_id": "gpt-4o-mini",
        "aliases": {"openai": "openai/gpt-4o-mini"},
        "pricing": [{"platform": "openai", "modality": "text", "tier": "standard",
                     "input_per_1m_tokens": 0.15, "output_per_1m_tokens": 0.6}],
    }]}}

    async def _catalog():
        return catalog

    monkeypatch.setattr(costs, "_get_pricing_catalog", _catalog)
    call = await costs.estimate_call_cost("requesty:openai/gpt-4o-mini", {"input_tokens": 1000, "output_tokens": 500})
    assert call["pricing_confidence"] == "medium"
    assert call["cost_status"] == "estimated" and call["is_estimate"] is True
    assert any("Requesty" in note for note in call["notes"])


@pytest.mark.anyio
async def test_requesty_zero_price_match_is_not_reported_as_free(monkeypatch):
    async def _zero(provider, native_id, input_tokens):
        return {"input_cost_per_1m": 0.0, "output_cost_per_1m": 0.0, "cached_input_cost_per_1m": None,
                "source": "catalog:test", "source_url": "https://pricing.example.test", "confidence": "medium"}

    monkeypatch.setattr(costs, "_resolve_catalog_pricing", _zero)
    call = await costs.estimate_call_cost("requesty:bedrock/some-model@eu", {"input_tokens": 10, "output_tokens": 5})
    assert call["cost_status"] == "estimated"
    assert call["is_estimate"] is True
