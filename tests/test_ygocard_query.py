"""验证命令最终只发送一条按序多图消息，以及随机/每日卡池区分。"""
import asyncio
import base64
import importlib.util
import sys
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException

from hikari_bot.core import commands
from hikari_bot.services import ygocard


@pytest.fixture
def matchers(monkeypatch):
    registered = {}
    class Matcher:
        def __init__(self):
            self.messages = []
        def handle(self):
            def register(fn):
                self.callback = fn
                return fn
            return register
        async def finish(self, message):
            self.messages.append(message)
            raise FinishedException
    def register(name, **kwargs):
        registered[name] = Matcher()
        return registered[name]
    monkeypatch.setattr(nonebot, 'on_command', register)
    monkeypatch.setattr(nonebot, 'require', lambda _: None)
    monkeypatch.setattr(commands, 'on_cmd', register)
    scheduler = SimpleNamespace(scheduled_job=lambda *args, **kwargs: lambda fn: fn)
    monkeypatch.setitem(sys.modules, 'nonebot_plugin_apscheduler', SimpleNamespace(scheduler=scheduler))
    monkeypatch.setitem(sys.modules, 'hikari_bot.services.ygodeck', SimpleNamespace(generate_card_list_image=None))
    spec = importlib.util.spec_from_file_location('test_card_query_adapter', 'hikari_bot/plugins/ygocard_query.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return registered


def test_card_query_sends_one_message_with_all_images_in_order(matchers, monkeypatch):
    async def images(keyword):
        assert keyword == '青眼白龙'
        return [(1, b'original'), (2, None), (3, b'artwork')]
    monkeypatch.setattr(ygocard, 'get_card_images', images)
    matcher = matchers['卡图查询']
    with pytest.raises(FinishedException):
        asyncio.run(matcher.callback(None, None, Message('青眼白龙')))
    assert len(matcher.messages) == 1
    segments = matcher.messages[0]
    decoded = [base64.b64decode(s.data['file'].removeprefix('base64://'))
               for s in segments if s.type == 'image']
    assert decoded == [b'original', b'artwork']
    assert '第 2 张卡图加载失败' in segments.extract_plain_text()


@pytest.mark.parametrize('name', ['随机一卡', '每日一卡'])
def test_only_random_command_enables_artworks(matchers, monkeypatch, name):
    calls = []
    def random_card(*args, **kwargs):
        calls.append(kwargs)
        return 1235
    async def image(card_id, half):
        assert card_id == 1235 and not half
        return b'image'
    monkeypatch.setattr(ygocard, 'random_card', random_card)
    monkeypatch.setattr(ygocard, 'get_ygopic', image)
    with pytest.raises(FinishedException):
        asyncio.run(matchers[name].callback(None, SimpleNamespace(get_user_id=lambda: '1')))
    assert calls == ([{'include_artworks': True}] if name == '随机一卡' else [{}])
