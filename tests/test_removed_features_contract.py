import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _settings_fields() -> set[str]:
    tree = ast.parse(_source("hikari_bot/core/config.py"))
    settings_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance((target := node.target), ast.Name)
    }


def test_removed_deployment_settings_are_not_declared():
    assert {
        "command_start",
        "faq_relay_group_id",
        "public_deck_url",
    }.isdisjoint(_settings_fields())


def test_command_prefix_and_public_deck_url_are_fixed_in_code():
    bot_source = "".join(_source("bot.py").split())
    constants_source = _source("hikari_bot/core/constants.py")
    match_source = _source("hikari_bot/plugins/ygomatch_query.py")

    assert 'command_start={""}' in bot_source
    assert 'PUBLIC_DECK_URL = "https://ygo.xyk.one/deck"' in constants_source
    assert "settings.public_deck_url" not in match_source
    assert "PUBLIC_DECK_URL" in match_source
