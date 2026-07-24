import pytest

from hikari_bot.persistence.database import StateDatabase
from hikari_bot.persistence.repositories import StateRepository


@pytest.fixture
def repository(tmp_path):
    return StateRepository(StateDatabase(tmp_path / "hikari.db"))


def test_flags_default_to_enabled_and_persist_false(repository):
    assert repository.get_flag("mycard_notify", default=True) is True

    repository.set_flag("mycard_notify", False)

    assert repository.get_flag("mycard_notify", default=True) is False


def test_whitelist_preserves_groups_and_users(repository):
    repository.replace_whitelist(groups=["100"], users=["200"])

    assert repository.get_whitelist() == {"groups": ["100"], "users": ["200"]}
    assert repository.add_group("100") is False
    assert repository.add_group("101") is True


def test_mycard_subscription_is_unique_and_can_remove_target(repository):
    repository.set_binding("1", "alice")

    assert repository.get_bindings() == {"1": "alice"}
    assert repository.subscribe("group", "100", "alice") is True
    assert repository.subscribe("group", "100", "alice") is False
    assert repository.unsubscribe_all("group", "100") is True
    assert repository.get_subscriptions() == {}
