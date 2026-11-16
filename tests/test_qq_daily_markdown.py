"""每日/随机一卡在真实官方 matcher 中使用 Markdown，保留权限与原图。"""
import asyncio
import subprocess
import sys

import pytest

from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.qq_official.results import ResultDelivery
from hikari_bot.services.platform import Result
from test_history_markdown import picture, transport
from test_qq_preview import event


def test_daily_and_random_matcher_send_markdown_but_keep_permissions():
    script = r'''
import asyncio, sys
from unittest.mock import Mock, AsyncMock
sys.path.insert(0, 'tests')
import nonebot
nonebot.logger.remove()
nonebot.init(driver='~aiohttp', _env_file=[], log_level='ERROR')
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.admin import AdminQueries
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.platform import Result
from test_history_markdown import picture, transport
from test_qq_preview import event

async def run():
    config = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user', 'other'}),
                         admins=frozenset({'only-user'}), accept_joined_groups=True)
    bus = Mock()
    bus.status.return_value = (True, '')
    bus.enqueue.return_value = 'job'
    bus.response.return_value = ('done', Result(images=(picture(),)))
    queries = Queries(Mock(random_id=Mock(return_value=123)),
                      image_loader=AsyncMock(return_value=picture()),
                      admin=AdminQueries(config, bus=bus))
    handler = register(config, queries=queries).handlers[0].call
    cases = [('每日一卡今天好运', 'c2c', 'only-user', True),
             ('每日一卡', 'group', 'only-user', True),
             ('每日一卡！', 'group-no-at', 'only-user', True),
             ('随机一卡来一张', 'c2c', 'only-user', True),
             ('随机一卡', 'group', 'only-user', False),
             ('随机一卡！', 'group-no-at', 'only-user', False),
             ('随机一卡', 'c2c', 'other', False)]
    for index, (text, kind, user, allowed) in enumerate(cases):
        client = transport()
        source = event('group' if kind == 'group-no-at' else kind, user=user)
        if kind == 'group-no-at':
            source = GroupMessageCreateEvent.model_validate(source.model_dump())
        source.id, source.content = f'event-{index}', text
        await handler(client, source)
        messages = [c.args[0].json for c in client._request.call_args_list
                    if c.args[0].url.path.endswith('/messages')]
        assert len(messages) == 1, (text, kind, messages)
        payload = messages[0]
        assert payload['msg_id'] == source.id and payload['msg_seq'] == 1
        if allowed:
            assert payload['msg_type'] == 2
            assert payload['markdown'] == {
                'content': '![第 1 张卡图 #8px #4px](qqbot-media://image/v1/test-token)',
                'force_verify_image_resource': True}
            assert not payload.get('media') and not payload.get('content')
            assert client.adapter.request.call_args.args[0].content == picture()
            await handler(client, source)
            assert len([c for c in client._request.call_args_list
                        if c.args[0].url.path.endswith('/messages')]) == 1
        else:
            assert payload['msg_type'] == 0 and not payload.get('markdown')
            client.adapter.request.assert_not_called()
    assert bus.enqueue.call_count == 1
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.parametrize('failure', ['upload', 'rejected', 'timeout'])
def test_failed_draw_keeps_markdown_for_explicit_continue(failure):
    async def run():
        from nonebot.adapters.qq.exception import ActionFailed
        from nonebot.drivers import Response
        client = transport()
        original = client._request.side_effect
        async def request(req):
            if failure == 'upload' and req.url.path.endswith('/prepare'):
                raise ActionFailed(Response(400, content='{"code":40035007}'))
            if req.url.path.endswith('/messages') and req.json['msg_type'] == 2:
                if failure == 'rejected':
                    raise ActionFailed(Response(403, content='{"code":40034141}'))
                if failure == 'timeout':
                    raise TimeoutError('private-detail')
            return await original(req)
        client._request.side_effect = request
        wrapper, source = NamedFileBot(client), event()
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(wrapper, source, Result(images=(picture(),)), markdown_images=True)
        messages = [c.args[0].json for c in client._request.call_args_list
                    if c.args[0].url.path.endswith('/messages')]
        assert [p['msg_type'] for p in messages] == ([0] if failure == 'upload' else [2, 0])
        assert '未改成普通文字或拆开发送' in messages[-1]['content']
        assert delivery.has_pending(wrapper, source)
        client._request.side_effect = original
        source.id = 'continue'
        await delivery.send(wrapper, source)
        messages = [c.args[0].json for c in client._request.call_args_list
                    if c.args[0].url.path.endswith('/messages')]
        assert messages[-1]['msg_type'] == 2 and messages[-1]['msg_id'] == 'continue'
        assert not delivery.has_pending(wrapper, source)
    asyncio.run(run())
