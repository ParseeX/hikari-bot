import json

import pytest

from hikari_bot.persistence import PersistenceError
from hikari_bot.persistence.migrations import initialize_state_store


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_imports_json_once_and_keeps_backups(tmp_path):
    write_json(tmp_path / "whitelist.json", {"groups": ["10"], "users": ["20"]})
    write_json(tmp_path / "feature_flags.json", {"mensa_monitor": False})
    write_json(tmp_path / "mycard_user.json", {"1": "alice"})
    write_json(tmp_path / "subscribe.json", {"alice": [["group", "10"]]})

    store = initialize_state_store(tmp_path / "hikari.db", tmp_path)

    assert store.get_whitelist()["groups"] == ["10"]
    assert store.get_whitelist()["users"] == ["20"]
    assert store.get_flag("mensa_monitor", True) is False
    assert store.get_bindings() == {"1": "alice"}
    assert store.get_subscriptions() == {"alice": [["group", "10"]]}
    assert (tmp_path / "whitelist.json.bak").is_file()

    second = initialize_state_store(tmp_path / "hikari.db", tmp_path)

    assert second.get_bindings() == {"1": "alice"}


def test_invalid_json_does_not_create_partial_state_or_backup(tmp_path):
    (tmp_path / "subscribe.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(PersistenceError):
        initialize_state_store(tmp_path / "hikari.db", tmp_path)

    assert (tmp_path / "subscribe.json").is_file()
    assert not (tmp_path / "subscribe.json.bak").exists()


def test_recovers_interrupted_migration_from_backup_file(tmp_path):
    write_json(tmp_path / "mycard_user.json.bak", {"1": "alice"})

    store = initialize_state_store(tmp_path / "hikari.db", tmp_path)

    assert store.get_bindings() == {"1": "alice"}
    assert (tmp_path / "mycard_user.json.bak").is_file()


def test_existing_database_is_authoritative_over_later_legacy_json(tmp_path):
    initial = initialize_state_store(tmp_path / "hikari.db", tmp_path)
    initial.set_binding("1", "database-user")
    write_json(tmp_path / "mycard_user.json", {"1": "legacy-user"})

    second = initialize_state_store(tmp_path / "hikari.db", tmp_path)

    assert second.get_bindings() == {"1": "database-user"}
    assert (tmp_path / "mycard_user.json.bak").is_file()


def test_refuses_to_overwrite_existing_legacy_backup(tmp_path):
    database_path = tmp_path / "hikari.db"
    initialize_state_store(database_path, tmp_path)
    write_json(tmp_path / "mycard_user.json", {"1": "new-user"})
    write_json(tmp_path / "mycard_user.json.bak", {"1": "old-user"})

    with pytest.raises(PersistenceError, match="拒绝覆盖"):
        initialize_state_store(database_path, tmp_path)
