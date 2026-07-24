import ast
from pathlib import Path


def test_cardrush_core_has_no_framework_imports():
    root = Path("hikari_bot/features/cardrush")
    forbidden = {"nonebot", "fastapi", "nonebot_plugin_apscheduler"}
    imported = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_plugin_keeps_command_and_schedule_declarations():
    source = Path(
        "hikari_bot/plugins/monitors/cardrush.py"
    ).read_text(encoding="utf-8")
    for command in (
        '"卡价查询"',
        '"卡价曲线"',
        '"卡价图报"',
        '"卡价日报"',
        '"重置卡价数据库"',
    ):
        assert command in source
    assert "minutes=15" in source
    assert 'hour=22, minute=20, timezone="Asia/Tokyo"' in source
    assert len(source.splitlines()) < 460


def test_qq_delivery_uses_compressed_pages_and_forward_targets():
    source = Path(
        "hikari_bot/plugins/monitors/cardrush.py"
    ).read_text(encoding="utf-8")

    assert source.count("await prepare_qq_pages(") == 2
    assert source.count("await send_qq_forward(") == 3
    assert "await send_qq_pages(" not in source
    assert "from hikari_bot.core.config import settings" in source
    assert "group_id=int(settings.public_group_id)" in source
    assert "public group failed" in source
