import asyncio
import importlib.util
from pathlib import Path

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


def test_prepare_qq_pages_compresses_each_page_once_and_logs_sizes(
    monkeypatch,
):
    calls = []
    logs = []

    def fake_compress(page, max_bytes=1_000_000):
        calls.append((page, max_bytes))
        return b"compressed-" + page

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(
        delivery.prepare_qq_pages([b"one", b"two"])
    )

    assert result == [b"compressed-one", b"compressed-two"]
    assert calls == [
        (b"one", 1_000_000),
        (b"two", 1_000_000),
    ]
    assert "page 1/2" in logs[0]
    assert "3 -> 14 bytes" in logs[0]
