"""卡图整组发送与被动额度续传；模拟上传，保留真实 SDK 序列化。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nonebot.adapters.qq import Bot
from test_qq_preview import INFO, NOW, bot, event
from hikari_bot.qq_official.card_actions import CardActions
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.results import ResultDelivery


def client():
    value = bot()
    value._extract_qq_media = Bot._extract_qq_media
    value.post_c2c_files = value.post_group_files = AsyncMock(return_value=SimpleNamespace(file_info='image-info'))
    value.send_to_group = lambda **kwargs: Bot.send_to_group(value, **kwargs)
    value.post_group_messages = lambda **kwargs: Bot.post_group_messages(value, **kwargs)
    return value


CONFIG = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'}), accept_joined_groups=True)


@pytest.fixture(autouse=True)
def plain_gallery_mode():
    from hikari_bot.qq_official.output_templates import OVERRIDE, default
    spec = default('art.gallery'); spec['mode'] = 'text'
    token = OVERRIDE.set({'art.gallery': spec})
    yield
    OVERRIDE.reset(token)


@pytest.mark.parametrize('kind,total', [('c2c', 1), ('c2c', 4), ('group', 5), ('group-no-at', 3)])
def test_default_query_sends_whole_set_without_navigation(kind, total):
    async def run():
        source = event('group' if kind == 'group-no-at' else kind)
        if kind == 'group-no-at':
            from nonebot.adapters.qq.event import GroupMessageCreateEvent
            source = GroupMessageCreateEvent.model_validate(source.model_dump())
        value = client()
        ids = list(range(100, 100 + total))
        catalog = Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=ids))
        loader = AsyncMock(side_effect=lambda code: str(code).encode())
        action = CardActions(catalog, image_loader=loader)
        await action.handle('卡图', '卡名', CONFIG, value, source, ReplyPager(now=lambda: NOW))
        uploads = value.post_c2c_files.call_args_list
        assert [c.kwargs['file_data'] for c in uploads] == [str(code).encode() for code in ids]
        assert all(c.kwargs['srv_send_msg'] is False for c in uploads)
        payloads = [c.args[0].json for c in value._request.call_args_list]
        assert [p['msg_type'] for p in payloads] == [7] * total
        assert [p['msg_seq'] for p in payloads] == list(range(1, total + 1))
        assert all(p['msg_id'] == source.id for p in payloads)
        assert not action.result_delivery.has_pending(value, source)
    asyncio.run(run())


@pytest.mark.parametrize('kind,first', [('c2c', 3), ('group', 4)])
def test_large_set_keeps_remaining_images_for_continue_without_reloading(kind, first):
    async def run():
        value, source = client(), event(kind)
        catalog = Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=list(range(7))))
        loader = AsyncMock(side_effect=lambda code: str(code).encode())
        pager = ReplyPager(now=lambda: NOW)
        delivery = ResultDelivery(pager)
        action = CardActions(catalog, image_loader=loader, result_delivery=delivery)
        await action.handle('卡图查询', '卡名', CONFIG, value, source, pager)
        assert value.post_c2c_files.await_count == first
        assert '继续' in value._request.call_args.args[0].json['content']
        assert delivery.has_pending(value, source)
        following = event(kind); following.id = 'continue-message'
        await delivery.send(value, following)
        assert value.post_c2c_files.await_count == 7 and loader.await_count == 7
        assert not delivery.has_pending(value, following)
        assert [c.kwargs['file_data'] for c in value.post_c2c_files.call_args_list] == [str(i).encode() for i in range(7)]
        payloads = [c.args[0].json for c in value._request.call_args_list]
        assert payloads[-1]['msg_id'] == following.id
    asyncio.run(run())


def test_failed_image_keeps_its_position_and_duplicate_event_does_not_resend():
    async def run():
        value, source = client(), event()
        loader = AsyncMock(side_effect=[b'first', OSError('synthetic failure'), b'third'])
        action = CardActions(Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=[1,2,3])), image_loader=loader)
        pager = ReplyPager(now=lambda: NOW)
        await action.handle('卡图', '卡名', CONFIG, value, source, pager)
        payloads = [c.args[0].json for c in value._request.call_args_list]
        assert [p['msg_type'] for p in payloads] == [7, 0, 7]
        assert '2' in payloads[1]['content'] and '暂不可用' in payloads[1]['content']
        assert [c.kwargs['file_data'] for c in value.post_c2c_files.call_args_list] == [b'first', b'third']
        await action.handle('卡图', '卡名', CONFIG, value, source, pager)
        assert loader.await_count == 3 and value._request.await_count == 3
    asyncio.run(run())


def test_send_failure_reports_pending_without_automatic_retry():
    async def run():
        value, source = client(), event()
        original = value._request.return_value
        value._request.side_effect = [TimeoutError('synthetic'), original, original, original]
        pager = ReplyPager(now=lambda: NOW)
        delivery = ResultDelivery(pager)
        action = CardActions(Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=[1,2])),
                             image_loader=AsyncMock(return_value=b'image'), result_delivery=delivery)
        await action.handle('卡图', '卡名', CONFIG, value, source, pager)
        assert value.post_c2c_files.await_count == 1
        assert '卡图发送未完成' in value._request.call_args.args[0].json['content']
        assert delivery.has_pending(value, source)
        following = event(); following.id = 'continue'
        await delivery.send(value, following)
        assert value.post_c2c_files.await_count == 3
        assert not delivery.has_pending(value, following)
    asyncio.run(run())


def test_plugin_continue_uses_same_image_queue(tmp_path):
    import subprocess
    import sys
    script = '''
import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace,MethodType
from unittest.mock import AsyncMock,Mock
import nonebot
nonebot.logger.remove()
nonebot.init(driver="~aiohttp",_env_file=[],log_level="ERROR")
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.card_actions import CardActions
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.output_templates import OVERRIDE,default
spec=default('art.gallery');spec['mode']='text';OVERRIDE.set({'art.gallery':spec})
catalog=Mock(search=Mock(return_value={'id':123}),image_ids=Mock(return_value=list(range(7))))
plugin.CardActions=lambda _,**kwargs: CardActions(catalog,**kwargs)
config=PilotConfig('app','fake',users=frozenset({'user'}))
client=SimpleNamespace(self_id='app',send_to_c2c=AsyncMock())
client.send=MethodType(Bot.send,client)
loader=AsyncMock(side_effect=lambda code:str(code).encode())
def event(id,text):
 return C2CMessageCreateEvent.model_validate(dict(id=id,content=text,timestamp=datetime.now(timezone.utc).isoformat(),author={'id':'user','user_openid':'user'}))
async def run():
 handler=plugin.register(config,image_loader=loader).handlers[0].call
 await handler(client,event('first','卡图 123'))
 assert client.send_to_c2c.await_count==4
 assert '继续' in str(client.send_to_c2c.call_args.kwargs['message'])
 await handler(client,event('second','继续'))
 calls=client.send_to_c2c.call_args_list
 images=[c.kwargs['message']['file_image'][0].data['content'] for c in calls if c.kwargs['message']['file_image']]
 assert images==[str(i).encode() for i in range(7)]
 assert loader.await_count==7
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2000:]
