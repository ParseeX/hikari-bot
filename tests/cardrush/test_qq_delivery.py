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


def jpeg_bytes(size=(1080, 1920)) -> bytes:
    image = Image.new("RGB", size, "#081020")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_prepare_qq_pages_uses_target_and_logs_dimensions(
    monkeypatch,
):
    calls = []
    logs = []
    compressed = jpeg_bytes()

    def fake_compress(page, target_bytes=350_000):
        calls.append((page, target_bytes))
        return compressed

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert result == [compressed]
    assert calls == [(b"one", 350_000)]
    assert "page 1/1" in logs[0]
    assert "1080x1920" in logs[0]
    assert "WARNING" not in logs[0]


def test_prepare_qq_pages_warns_above_observation_limit(
    monkeypatch,
):
    logs = []

    def fake_compress(page, target_bytes=350_000):
        return jpeg_bytes() + bytes(451_000)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)
    monkeypatch.setattr(
        delivery,
        "_image_size",
        lambda data: (1080, 1920),
    )

    asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert "WARNING: above 450000 bytes" in logs[0]
