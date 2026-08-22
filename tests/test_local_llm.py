import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from hikari_bot.services import local_llm


ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides):
    values = {
        "local_llm_api_base_url": "http://192.168.0.100:1234/v1",
        "local_llm_model": "qwen/qwen3.5-9b",
        "local_llm_api_key": "",
        "local_llm_system_prompt": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_local_llm_uses_lm_studio_chat_completions(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://192.168.0.100:1234/v1/chat/completions")
        assert json.loads(request.content) == {
            "model": "qwen/qwen3.5-9b",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
        }
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好！"}}]})

    monkeypatch.setattr(local_llm, "settings", _settings())
    transport = httpx.MockTransport(handler)

    async def run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await local_llm.get_local_llm_reply("你好", client=client)

    assert asyncio.run(run()) == "你好！"


def test_local_llm_includes_optional_system_prompt_and_api_key(monkeypatch):
    monkeypatch.setattr(
        local_llm,
        "settings",
        _settings(local_llm_system_prompt="简洁回答", local_llm_api_key="test-token"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        assert json.loads(request.content)["messages"] == [
            {"role": "system", "content": "简洁回答"},
            {"role": "user", "content": "测试"},
        ]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "收到"}}]},
        )

    transport = httpx.MockTransport(handler)

    async def run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await local_llm.get_local_llm_reply("测试", client=client)

    assert asyncio.run(run()) == "收到"


def test_local_llm_rejects_response_without_text_content(monkeypatch):
    monkeypatch.setattr(local_llm, "settings", _settings())
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})
    )

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await local_llm.get_local_llm_reply("测试", client=client)

    with pytest.raises(ValueError, match="text content"):
        asyncio.run(run())


def test_local_llm_plugin_requires_admin_group_at_mention():
    source = (ROOT / "hikari_bot/plugins/local_llm_chat.py").read_text(encoding="utf-8")

    assert "isinstance(event, GroupMessageEvent)" in source
    assert "str(event.user_id) in ADMIN" in source
    assert "_mentions_bot(event.get_message(), str(bot.self_id))" in source
    assert "bool(event.get_plaintext().strip())" in source
    assert "on_message(" in source
    assert "priority=100" in source
