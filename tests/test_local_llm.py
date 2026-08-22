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
        "local_llm_image_max_count": 4,
        "local_llm_image_max_bytes": 10 * 1024 * 1024,
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


def test_local_llm_sends_qq_image_as_openai_image_url(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url == httpx.URL("https://qq.example.test/image.png")
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"png-bytes",
            )

        assert request.url == httpx.URL("http://192.168.0.100:1234/v1/chat/completions")
        assert json.loads(request.content)["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是什么？"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,cG5nLWJ5dGVz"},
                    },
                ],
            }
        ]
        return httpx.Response(200, json={"choices": [{"message": {"content": "一张图片"}}]})

    monkeypatch.setattr(local_llm, "settings", _settings())
    transport = httpx.MockTransport(handler)

    async def run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await local_llm.get_local_llm_reply(
                "这是什么？",
                image_urls=["https://qq.example.test/image.png"],
                client=client,
            )

    assert asyncio.run(run()) == "一张图片"


def test_local_llm_uses_default_prompt_for_image_only_message(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"content-type": "image/webp"},
                content=b"webp-bytes",
            )
        assert json.loads(request.content)["messages"][0]["content"][0] == {
            "type": "text",
            "text": local_llm.DEFAULT_IMAGE_PROMPT,
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": "图片描述"}}]})

    monkeypatch.setattr(local_llm, "settings", _settings())
    transport = httpx.MockTransport(handler)

    async def run() -> str:
        async with httpx.AsyncClient(transport=transport) as client:
            return await local_llm.get_local_llm_reply(
                "",
                image_urls=["https://qq.example.test/image.webp"],
                client=client,
            )

    assert asyncio.run(run()) == "图片描述"


def test_local_llm_plugin_requires_admin_group_at_mention():
    source = (ROOT / "hikari_bot/plugins/local_llm_chat.py").read_text(encoding="utf-8")

    assert "isinstance(event, GroupMessageEvent)" in source
    assert "str(event.user_id) in ADMIN" in source
    assert "_mentions_bot(event.original_message, str(bot.self_id))" in source
    assert "_contains_image(event.get_message())" in source
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


def test_local_llm_accepts_image_only_at_mention(monkeypatch):
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
                {
                    "type": "image",
                    "data": {"url": "https://qq.example.test/image.png"},
                },
            ],
            "raw_message": f"[CQ:at,qq={bot_id}][CQ:image,file=test.png]",
            "font": 0,
            "sender": {},
            "group_id": 30003,
        }
    )
    monkeypatch.setattr(local_llm_chat, "ADMIN", frozenset({"20002"}))
    bot = SimpleNamespace(self_id=bot_id)

    _check_at_me(bot, event)

    assert not event.get_plaintext().strip()
    assert asyncio.run(local_llm_chat._is_authorized_group_prompt(bot, event))
