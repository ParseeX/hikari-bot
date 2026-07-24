import asyncio
import sys
from types import ModuleType, SimpleNamespace


if "nonebot" not in sys.modules:
    nonebot_stub = ModuleType("nonebot")
    nonebot_stub.get_bot = lambda: None
    nonebot_stub.logger = SimpleNamespace(error=lambda *args, **kwargs: None)
    sys.modules["nonebot"] = nonebot_stub


from hikari_bot.core import feature_flags, whitelist
from hikari_bot.persistence import StateDatabase, StateRepository
from hikari_bot.services import mycard


def test_existing_state_apis_use_shared_repository(tmp_path, monkeypatch):
    repository = StateRepository(StateDatabase(tmp_path / "hikari.db"))
    monkeypatch.setattr(feature_flags, "get_state_store", lambda: repository, raising=False)
    monkeypatch.setattr(whitelist, "get_state_store", lambda: repository, raising=False)
    monkeypatch.setattr(mycard, "get_state_store", lambda: repository, raising=False)

    asyncio.run(feature_flags.set_notify_enabled(False))
    asyncio.run(feature_flags.set_mensa_enabled(False))
    assert asyncio.run(feature_flags.get_notify_enabled()) is False
    assert asyncio.run(feature_flags.get_mensa_enabled()) is False

    assert asyncio.run(whitelist.add_group_to_whitelist("100")) is True
    assert asyncio.run(whitelist.is_allowed_group("100")) is True
    asyncio.run(whitelist.save_whitelist({"groups": ["100"], "users": ["200"]}))
    assert repository.get_whitelist() == {"groups": ["100"], "users": ["200"]}

    mycard.add_mycard_user("1", "alice")
    mycard.subscribe("group", "100", "alice")
    assert mycard.get_mycard_user() == {"1": "alice"}
    assert mycard.get_subscribe_list() == {"alice": [["group", "100"]]}
    assert mycard.unsubscribe_all("group", "100") is True
    assert repository.get_subscriptions() == {}


def test_default_store_initializes_database_in_data_directory(tmp_path, monkeypatch):
    import hikari_bot.persistence as persistence

    captured = {}
    monkeypatch.setattr(persistence, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        persistence,
        "initialize_state_store",
        lambda database_path, legacy_dir: captured.update(
            {"database_path": database_path, "legacy_dir": legacy_dir}
        )
        or object(),
    )
    persistence.get_state_store.cache_clear()

    persistence.get_state_store()

    assert captured == {
        "database_path": tmp_path / "hikari.db",
        "legacy_dir": tmp_path,
    }
    persistence.get_state_store.cache_clear()
