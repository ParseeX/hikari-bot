"""每日/随机一卡恢复旧版前缀触发，保持业务规则与权限。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.qq_official.admin import AdminQueries
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.commands import is_group_request, parse_command
from hikari_bot.services.platform import Result
from test_qq_migration import request


@pytest.mark.parametrize('command', ['每日一卡', '随机一卡'])
@pytest.mark.parametrize('suffix', ['', '今天好运', ' 来一张', '！', '\n来一张'])
@pytest.mark.parametrize('prefix', ['', ' /'])
def test_card_prefix_is_recognized_without_a_separator(command, suffix, prefix):
    text = prefix + command + suffix
    assert parse_command(text) == (command, suffix.strip())
    assert is_group_request(text)


@pytest.mark.parametrize('text', ['今天每日一卡', '每日一', '卡图青眼白龙', '绑定player', '重启服务器现在'])
def test_other_commands_and_embedded_mentions_do_not_gain_prefix_matching(text):
    assert parse_command(text) == (text, '')
    assert not is_group_request(text)


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group_at'])
def test_daily_suffix_does_not_change_the_draw(kind):
    async def run():
        catalog = Mock(random_id=Mock(return_value=123))
        loader = AsyncMock(return_value=b'card-image')
        queries = Queries(catalog, image_loader=loader)
        seeds = []
        for text in ['每日一卡', '每日一卡今天好运', '/每日一卡 来一张']:
            result = await queries.handle(request(text, kind=kind), *parse_command(text))
            assert result.images == (b'card-image',)
            seeds.append(catalog.random_id.call_args.args[0])
        assert seeds[0] == seeds[1] == seeds[2]
        assert loader.await_count == 3
    asyncio.run(run())


@pytest.mark.parametrize('kind,user,allowed', [('c2c', 'u', True), ('c2c', 'other', False), ('group', 'u', False), ('group_at', 'u', False)])
def test_random_suffix_reaches_existing_control_only_for_private_admin(kind, user, allowed):
    async def run():
        bus = Mock()
        bus.status.return_value = (True, '')
        bus.enqueue.return_value = 'job'
        bus.response.return_value = ('done', Result(images=(b'random-card',)))
        admin = AdminQueries(SimpleNamespace(app_id='app', admins={'u'}), bus=bus)
        text = '随机一卡来一张'
        result = await Queries(None, admin=admin).handle(
            request(text, kind=kind, user=user), *parse_command(text))
        assert result is not None
        if allowed:
            assert result.images == (b'random-card',)
            body = json.loads(bus.enqueue.call_args.args[3])
            assert body == {'action': 'random_card', 'argument': '来一张'}
        else:
            assert not result.images
            bus.enqueue.assert_not_called()
    asyncio.run(run())
