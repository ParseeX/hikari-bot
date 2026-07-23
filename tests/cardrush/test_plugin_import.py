import ast
import asyncio
import importlib.util
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
        '"发布B站动态"',
    ):
        assert command in source
    assert "minutes=15" in source
    assert 'hour=22, minute=20, timezone="Asia/Tokyo"' in source
    assert len(source.splitlines()) < 450


def test_upload_route_preserves_response_shape(monkeypatch):
    path = Path("hikari_bot/plugins/web/routes/cr_upload.py")
    spec = importlib.util.spec_from_file_location(
        "cardrush_test_cr_upload",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeService:
        async def save_prices(self, records):
            assert len(records) == 1
            return 1

    monkeypatch.setattr(module, "service", FakeService())
    payload = module.UploadPayload(
        prices=[
            module.PriceRecord(
                product_id=1,
                name="青眼の白龍",
                price=3200,
                rarity="ウルトラ",
                model_number="QCAC-JP001",
                updated_at="2026-07-23T00:00:00.000Z",
            )
        ]
    )

    response = asyncio.run(module.cr_upload(payload))

    assert response == {"ok": True, "received": 1, "saved": 1}


def test_qq_delivery_uses_compressed_pages_but_bilibili_keeps_originals():
    source = Path(
        "hikari_bot/plugins/monitors/cardrush.py"
    ).read_text(encoding="utf-8")

    assert source.count("await prepare_qq_pages(") == 2
    assert source.count("await send_qq_forward(") == 2
    assert "await send_qq_pages(" not in source
    assert "post_article_with_images(screenshots, date_str)" in source
