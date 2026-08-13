import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from hikari_bot.services import ruling_assistant


ROOT = Path(__file__).resolve().parents[1]


def test_ruling_assistant_returns_only_short_answer(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://example.test/api/answer")
        assert json.loads(request.content) == {"question": "这张卡能发动吗？"}
        return httpx.Response(
            200,
            json={"shortAnswer": "可以发动。", "reasoning": ["不应发送"]},
        )

    monkeypatch.setattr(
        ruling_assistant,
        "settings",
        SimpleNamespace(ruling_assistant_api_url="https://example.test/api/answer"),
    )
    transport = httpx.MockTransport(handler)

    async def run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await ruling_assistant.get_ruling_conclusion("这张卡能发动吗？", client=client)

    assert asyncio.run(run()) == "可以发动。"


def test_ruling_assistant_rejects_response_without_short_answer(monkeypatch):
    monkeypatch.setattr(
        ruling_assistant,
        "settings",
        SimpleNamespace(ruling_assistant_api_url="https://example.test/api/answer"),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"reasoning": []}))

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await ruling_assistant.get_ruling_conclusion("问题", client=client)

    with pytest.raises(ValueError, match="shortAnswer"):
        asyncio.run(run())


def test_ruling_query_command_uses_space_separated_command_and_only_sends_conclusion():
    source = (ROOT / "hikari_bot/plugins/ruling_query.py").read_text(encoding="utf-8")

    assert 'on_cmd("裁定查询"' in source
    assert "get_ruling_conclusion(question)" in source
    assert 'f"{conclusion}\\n{DISCLAIMER}"' in source
    assert "reasoning" not in source
