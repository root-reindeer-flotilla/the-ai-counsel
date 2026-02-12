import json

import pytest

from backend import storage


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_list_conversations_rebuilds_when_index_missing(isolated_data_dir):
    conv = {
        "id": "conv-1",
        "created_at": "2026-01-01T00:00:00",
        "title": "First",
        "messages": [{"role": "user", "content": "hello"}],
    }
    (isolated_data_dir / "conv-1.json").write_text(json.dumps(conv))

    listed = storage.list_conversations()
    assert len(listed) == 1
    assert listed[0]["id"] == "conv-1"
    assert listed[0]["message_count"] == 1
    assert (isolated_data_dir / storage.INDEX_FILE_NAME).exists()


def test_rebuild_index_skips_invalid_json_and_sorts_newest_first(isolated_data_dir):
    newer = {
        "id": "newer",
        "created_at": "2026-02-01T00:00:00",
        "title": "New",
        "messages": [],
    }
    older = {
        "id": "older",
        "created_at": "2026-01-01T00:00:00",
        "title": "Old",
        "messages": [{"role": "assistant", "content": "x"}],
    }
    (isolated_data_dir / "older.json").write_text(json.dumps(older))
    (isolated_data_dir / "newer.json").write_text(json.dumps(newer))
    (isolated_data_dir / "broken.json").write_text("{this is not json")

    rebuilt = storage.rebuild_index()
    assert [item["id"] for item in rebuilt] == ["newer", "older"]
    assert all(item["id"] != "broken" for item in rebuilt)


def test_update_index_entry_dedupes_existing_item(isolated_data_dir):
    original = {
        "id": "conv-1",
        "created_at": "2026-01-01T00:00:00",
        "title": "Original",
        "messages": [{"role": "user", "content": "a"}],
    }
    updated = {
        "id": "conv-1",
        "created_at": "2026-01-01T00:00:00",
        "title": "Updated",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "stage1": []},
        ],
    }
    (isolated_data_dir / "conv-1.json").write_text(json.dumps(original))
    storage.rebuild_index()

    storage._update_index_entry(updated)
    index = storage.list_conversations()

    assert len(index) == 1
    assert index[0]["title"] == "Updated"
    assert index[0]["message_count"] == 2


def test_delete_conversation_updates_index_and_missing_returns_false(isolated_data_dir):
    storage.create_conversation("conv-1")
    assert storage.delete_conversation("conv-1") is True
    assert storage.delete_conversation("conv-1") is False
    assert storage.list_conversations() == []


def test_add_assistant_message_preserves_partial_mode_shape(isolated_data_dir):
    storage.create_conversation("conv-1")

    storage.add_assistant_message(
        "conv-1",
        stage1=[{"model": "m1", "response": "r"}],
        metadata={"execution_mode": "chat_only"},
    )

    conv = storage.get_conversation("conv-1")
    msg = conv["messages"][-1]
    assert msg["role"] == "assistant"
    assert "stage1" in msg
    assert "stage2" not in msg
    assert "stage3" not in msg
    assert msg["metadata"]["execution_mode"] == "chat_only"


def test_add_user_and_error_messages_append_expected_shapes(isolated_data_dir):
    storage.create_conversation("conv-1")
    storage.add_user_message("conv-1", "hello")
    storage.add_error_message("conv-1", "boom")

    conv = storage.get_conversation("conv-1")
    user_msg = conv["messages"][0]
    err_msg = conv["messages"][1]

    assert user_msg == {"role": "user", "content": "hello"}
    assert err_msg["role"] == "assistant"
    assert err_msg["error"] == "boom"
    assert err_msg["stage1"] == []
    assert err_msg["stage2"] == []
    assert err_msg["stage3"] is None
