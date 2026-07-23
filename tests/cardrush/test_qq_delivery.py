import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from nonebot.adapters.onebot.v11 import ActionFailed

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


def test_send_qq_pages_continues_after_retcode_1200(
    monkeypatch,
):
    attempts = []
    logs = []

    async def fake_send(page):
        attempts.append(page)
        if page == "base64://b25l":
            raise ActionFailed(
                status="failed",
                retcode=1200,
                message="Timeout",
            )

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "log_message", fake_log)

    timeouts = asyncio.run(
        delivery.send_qq_pages(
            [b"one", b"two", b"three"],
            fake_send,
            log_prefix="[test]",
        )
    )

    assert attempts == [
        "base64://b25l",
        "base64://dHdv",
        "base64://dGhyZWU=",
    ]
    assert timeouts == [1]
    assert "page 1/3" in logs[0]
    assert "retcode=1200" in logs[0]


def test_send_qq_pages_reraises_other_errors():
    attempts = []
    expected = ActionFailed(
        status="failed",
        retcode=100,
        message="Other failure",
    )

    async def fake_send(page):
        attempts.append(page)
        if page == "base64://dHdv":
            raise expected

    with pytest.raises(ActionFailed) as caught:
        asyncio.run(
            delivery.send_qq_pages(
                [b"one", b"two", b"three"],
                fake_send,
                log_prefix="[test]",
            )
        )

    assert caught.value is expected
    assert attempts == ["base64://b25l", "base64://dHdv"]
