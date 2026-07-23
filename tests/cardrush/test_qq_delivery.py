import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path

from PIL import Image

module_path = Path(
    "hikari_bot/plugins/monitors/cardrush_delivery.py"
)
spec = importlib.util.spec_from_file_location(
    "cardrush_test_delivery",
    module_path,
)
assert spec and spec.loader
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def webp_bytes(size=(1080, 1920)) -> bytes:
    image = Image.new("RGB", size, "#081020")
    buffer = BytesIO()
    image.save(buffer, format="WEBP")
    return buffer.getvalue()


def test_prepare_qq_pages_uses_webp_target_and_logs_format(
    monkeypatch,
):
    calls = []
    logs = []
    compressed = webp_bytes()

    def fake_compress(page, target_bytes=200_000):
        calls.append((page, target_bytes))
        return compressed

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert result == [compressed]
    assert calls == [(b"one", 200_000)]
    assert "1080x1920" in logs[0]
    assert "WEBP" in logs[0]
    assert "WARNING" not in logs[0]


def test_prepare_qq_pages_warns_above_observation_limit(
    monkeypatch,
):
    logs = []

    def fake_compress(page, target_bytes=200_000):
        return bytes(230_001)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)
    monkeypatch.setattr(
        delivery,
        "_image_info",
        lambda data: (1080, 1920, "WEBP"),
    )

    asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert "WARNING: above 230000 bytes" in logs[0]
