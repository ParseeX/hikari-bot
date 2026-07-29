import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nas_power_plugin_is_private_and_uses_existing_superusers():
    source = (ROOT / "hikari_bot/plugins/nas_power.py").read_text(encoding="utf-8")

    assert "PrivateMessageEvent" in source
    assert "settings.superusers" in source
    assert 'on_cmd("开机"' in source
    assert 'on_cmd("关机"' in source
    assert 'f"{base_url}/v1/{action}"' in source


def test_nas_power_settings_are_declared_without_exposing_the_token_repr():
    source = (ROOT / "hikari_bot/core/config.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    settings_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    names = {
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert {
        "nas_power_url",
        "nas_power_token",
        "nas_power_timeout",
    }.issubset(names)
