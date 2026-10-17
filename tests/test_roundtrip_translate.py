"""在独立进程中验证真实 NoneBot 分发，避免污染其他测试的插件注册表。"""

import subprocess
import sys
from pathlib import Path


def test_roundtrip_dispatch_and_failures():
    result = subprocess.run(
        [sys.executable, "-c", _SCENARIO],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_SCENARIO = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, Message, MessageEvent
from nonebot.message import handle_event

nonebot.init(driver="~fastapi", command_start={""}, log_level="ERROR")
plugin = nonebot.load_plugin("hikari_bot.plugins.roundtrip_translate")
assert plugin is not None
module = plugin.module
from nonebot_plugin_easy_translate import fanyi
from nonebot_plugin_easy_translate.config import var

bot = Bot(Adapter(nonebot.get_driver()), "10000")
var.handle_bot = bot
replies = []
calls = []
clients = []
failure_at = None
empty_at = None

class Client:
    closed = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True

class Translator:
    def __init__(self, proxies=None):
        assert proxies == "test-proxy"
        self.client = Client()
        clients.append(self.client)
    async def translate(self, text, *, src, dest):
        calls.append((text, src, dest))
        if len(calls) == failure_at:
            raise RuntimeError("private upstream error")
        if len(calls) == empty_at:
            return SimpleNamespace(text=" ")
        return SimpleNamespace(text="日本語" if dest == "ja" else "返回中文[CQ:at,qq=123]")

async def send(self, event, message, **kwargs):
    replies.append(Message(message))
    return {"message_id": 1}

# 与 base.py 相同优先级的白名单拦截，验证新命令不会越过它。
guard = nonebot.on_message(priority=1, block=False)
@guard.handle()
async def guard_group(event: MessageEvent, matcher: nonebot.matcher.Matcher):
    if getattr(event, "group_id", None) == 999:
        matcher.stop_propagation()

def event(text, group=None):
    payload = dict(time=0, self_id=10000, post_type="message", message_id=1,
                   message_type="private", sub_type="friend", user_id=123,
                   message=Message(text), original_message=Message(text),
                   raw_message=text, font=0, sender={"user_id": 123})
    if group is not None:
        payload.update(message_type="group", sub_type="normal", group_id=group)
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    return (GroupMessageEvent if group is not None else PrivateMessageEvent)(**payload)

async def dispatch(text, group=None):
    replies.clear()
    calls.clear()
    clients.clear()
    await handle_event(bot, event(text, group))

async def main():
    global failure_at, empty_at
    with patch.object(module, "Translator", Translator), \
         patch.object(module.pc, "easy_translate_http_proxy", "test-proxy"), \
         patch.object(Bot, "send", send):
        for text in ("中译中 你好", "中译中你好", "中译中\n你好"):
            await dispatch(text)
            assert calls == [("你好", "zh-cn", "ja"), ("日本語", "ja", "zh-cn")], calls
            assert len(replies) == 1, replies
            assert replies[0].extract_plain_text() == "返回中文[CQ:at,qq=123]"
            assert all(segment.type == "text" for segment in replies[0])
            assert all(client.closed for client in clients)

        await dispatch("中译中 第一行\n第二行", 123)
        assert calls[0][0] == "第一行\n第二行" and len(replies) == 1

        for text in ("中译中", "中译中   "):
            await dispatch(text)
            assert not calls and len(replies) == 1
            assert "用法" in replies[0].extract_plain_text()

        for failure_at in (1, 2):
            await dispatch("中译中 你好")
            assert len(calls) == failure_at
            assert len(replies) == 1 and replies[0].extract_plain_text() == "翻译失败，请稍后再试。"
            assert clients[0].closed
        failure_at = None
        for empty_at in (1, 2):
            await dispatch("中译中 你好")
            assert len(calls) == empty_at
            assert len(replies) == 1 and "翻译失败" in replies[0].extract_plain_text()
            assert clients[0].closed
        empty_at = None

        await dispatch("中译中 你好", 999)
        assert not calls and not replies
        var.handle_bot = None
        await dispatch("中译中 你好", 123)
        assert not calls and not replies
        var.handle_bot = bot

        for text in ("翻译 hello", "中译日 你好", "日译中 こんにちは"):
            assert await fanyi.check_rule(bot, event(text), {})
            assert not await module.roundtrip.check_rule(bot, event(text), {})
        for text in ("中译中 你好", "中译中你好", "中译中"):
            assert not await fanyi.check_rule(bot, event(text), {})

asyncio.run(main())
'''
