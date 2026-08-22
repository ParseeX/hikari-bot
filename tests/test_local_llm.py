import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.bot import _check_at_me

from hikari_bot.plugins import local_llm_chat
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
    assert "_mentions_bot(event.original_message, str(bot.self_id))" in source
    assert "bool(event.get_plaintext().strip())" in source
    assert "on_message(" in source
    assert "priority=6" in source


def test_local_llm_runs_before_global_deck_collector():
    local_llm_source = (ROOT / "hikari_bot/plugins/local_llm_chat.py").read_text(
        encoding="utf-8"
    )
    match_source = (ROOT / "hikari_bot/plugins/ygomatch_query.py").read_text(
        encoding="utf-8"
    )

    assert "priority=6" in local_llm_source
    assert "collect_deck = on_message(priority=10)" in match_source


def test_local_llm_accepts_at_mention_after_onebot_preprocessing(monkeypatch):
    bot_id = "10001"
    event = GroupMessageEvent.model_validate(
        {
            "time": 0,
            "self_id": int(bot_id),
            "post_type": "message",
            "sub_type": "normal",
            "user_id": 20002,
            "message_type": "group",
            "message_id": 1,
            "message": [
                {"type": "at", "data": {"qq": bot_id}},
                {"type": "text", "data": {"text": "hello"}},
            ],
            "raw_message": f"[CQ:at,qq={bot_id}]hello",
            "font": 0,
            "sender": {},
            "group_id": 30003,
        }
    )
    monkeypatch.setattr(local_llm_chat, "ADMIN", frozenset({"20002"}))
    bot = SimpleNamespace(self_id=bot_id)

    _check_at_me(bot, event)

    assert event.is_tome()
    assert asyncio.run(local_llm_chat._is_authorized_group_prompt(bot, event))


def test_local_llm_logs_mention_gate_results_without_prompt_content(monkeypatch):
    bot_id = "10001"
    event = GroupMessageEvent.model_validate(
        {
            "time": 0,
            "self_id": int(bot_id),
            "post_type": "message",
            "sub_type": "normal",
            "user_id": 20002,
            "message_type": "group",
            "message_id": 1,
            "message": [
                {"type": "at", "data": {"qq": bot_id}},
                {"type": "text", "data": {"text": "private prompt"}},
            ],
            "raw_message": f"[CQ:at,qq={bot_id}]private prompt",
            "font": 0,
            "sender": {},
            "group_id": 30003,
        }
    )
    monkeypatch.setattr(local_llm_chat, "ADMIN", frozenset({"20002"}))

    async def fake_is_allowed_group(group_id: int) -> bool:
        assert group_id == 30003
        return True

    logs = []

    async def fake_log_message(message: str) -> None:
        logs.append(message)

    monkeypatch.setattr(local_llm_chat, "is_allowed_group", fake_is_allowed_group)
    monkeypatch.setattr(local_llm_chat, "log_message", fake_log_message)
    _check_at_me(SimpleNamespace(self_id=bot_id), event)

    asyncio.run(
        local_llm_chat._log_mention_gate_result(SimpleNamespace(self_id=bot_id), event)
    )

    assert logs == [
        "[local_llm_chat] mention group_id=30003 user_id=20002 "
        "group_allowed=True superuser=True has_text=True"
    ]
    assert "private prompt" not in logs[0]
