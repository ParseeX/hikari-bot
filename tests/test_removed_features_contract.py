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


def test_faq_command_and_service_are_removed():
    plugin_source = _source("hikari_bot/plugins/ygocard_query.py")
    service_source = _source("hikari_bot/services/ygocard.py")
    service_tree = ast.parse(service_source)
    function_names = {
        node.name
        for node in ast.walk(service_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for removed_command in ("裁定查询", "游戏王裁定", '"裁定"'):
        assert removed_command not in plugin_source
    assert "get_qa_by_id" not in function_names
    assert 'FAQ = "https://ygocdb.com/faq/"' not in service_source
