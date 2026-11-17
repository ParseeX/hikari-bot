"""低价值提示静默/限频，保留操作失败、权限与自定义文案。"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.qq_official.alias_choices import AliasChoices
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.callbacks import Action, CallbackController
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.output_templates import SCHEMA, default, present, upgrade_spec
from hikari_bot.qq_official.prices import PriceQueries
from test_qq_migration import bot, event, request
from test_qq_alias_choices import click


CONFIG = PilotConfig('app', 'fake', users=frozenset({'u', 'other'}), groups=frozenset({'g', 'g2'}))


def test_busy_and_empty_continue_are_limited_per_user_and_session():
    async def run():
        clock = [100.]
        controller = CallbackController(CONFIG, AsyncMock(), clock=lambda: clock[0])
        client = bot()
        for kind, group in [('c2c', 'g'), ('group', 'g'), ('group', 'g2')]:
            incoming = event(kind, group=group)
            proxy = controller.wrap(client, incoming)
            for key in ['paging.plugin_e278b7fd', 'paging.media_busy', 'paging.delivery_1', 'paging.delivery_1']:
                await proxy.send(incoming, present(key))
        assert client.send_to_c2c.await_count == 2
        assert client.send_to_group.await_count == 4
        other = event(user='other')
        await controller.wrap(client, other).send(other, present('paging.media_busy'))
        assert client.send_to_c2c.await_count == 3
        incoming = event()
        proxy = controller.wrap(client, incoming)
        for _ in range(2):
            await proxy.send(incoming, present('operations.admin_768af6cc'))
        assert client.send_to_c2c.await_count == 5
        clock[0] += 31
        await proxy.send(incoming, present('paging.media_busy'))
        assert client.send_to_c2c.await_count == 6
    asyncio.run(run())


@pytest.mark.parametrize('write', [False, True])
@pytest.mark.parametrize('failure', ['late', 'ack'])
def test_query_callback_failures_silent_but_valid_write_failure_is_reported(write, failure):
    async def run():
        client = SimpleNamespace(self_id='synthetic-app', put_interaction=AsyncMock(), send_to_c2c=AsyncMock())
        controller = CallbackController(PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'})), AsyncMock())
        controller.actions['handle'] = Action('synthetic-app', 'only-user', 'button',
            '撤销别名 ' + 'a' * 32 if write else '效果 1234', controller.clock() + 60,
            message_id='test-reply', ready=True)
        incoming = click({'id': 'button', 'action': {'data': 'handle'}})
        if failure == 'late':
            incoming.timestamp = (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat()
        else:
            client.put_interaction.side_effect = TimeoutError
        await controller.handle(client, incoming)
        assert client.send_to_c2c.await_count == int(write)
        assert not controller.tasks
        controller.processor.assert_not_awaited()
    asyncio.run(run())


def test_disabled_automatic_parser_and_stale_candidate_are_silent():
    async def run():
        notifications = SimpleNamespace(bus=SimpleNamespace(setting=Mock(return_value=False)),
                                        handle=AsyncMock(return_value=None))
        links = SimpleNamespace(parse=AsyncMock())
        queries = Queries(None, notifications=notifications, links=links)
        result = await queries.handle(request('https://www.bilibili.com/video/BV1xx411c7mD'), '', '')
        assert not result.text
        explicit = await queries.handle(request('bm BV1xx411c7mD'), 'bm', 'BV1xx411c7mD')
        assert '已关闭' in explicit.text
        links.parse.assert_not_awaited()
        client = AsyncMock()
        assert await AliasChoices(None).handle(client, event(), '候选页', 'missing 0')
        client.send.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('fails', [False, True])
def test_old_price_result_is_silent_and_cannot_replace_new_session(fails):
    async def run():
        queries = PriceQueries(None, None, None)
        entered, release = asyncio.Event(), asyncio.Event()
        async def versions(*args, **kwargs):
            entered.set()
            await release.wait()
            if fails:
                raise TimeoutError
            return [object()]
        queries.comparison = SimpleNamespace(versions=versions)
        task = asyncio.create_task(queries.start({'jp_name': 'card'}, 'u'))
        await entered.wait()
        queries.cancel('u')
        queries.sessions['u'] = 'new-session'
        release.set()
        assert await task == ''
        assert queries.sessions['u'] == 'new-session'
    asyncio.run(run())


def test_empty_stale_reply_does_not_discard_newer_pending_pages():
    from hikari_bot.qq_official.delivery import ReplyPager, Pending, context
    async def run():
        pager = ReplyPager()
        client, incoming = bot(), event()
        key = context(client, incoming)
        saved = Pending('new-result', 0, pager.clock() + 60)
        pager.pending[key] = saved
        await pager.send(client, incoming, '')
        assert pager.pending[key] is saved
        client.send_to_c2c.assert_not_awaited()
    asyncio.run(run())


def test_default_copy_upgrade_preserves_user_text_and_modes():
    key = 'paging.markdown_failed'
    old = default(key)
    old['template'] = SCHEMA['states'][key]['previous_default_templates'][0]
    upgraded = upgrade_spec(key, old)
    assert upgraded['template'] == SCHEMA['states'][key]['template']
    assert 'HTTP' in old['template']
    custom = {**old, 'template': '我的提示：{recovery}'}
    assert upgrade_spec(key, custom) == custom
    assert 'attachments.business_38bd1f82' not in SCHEMA['states']
    assert 'price.prices_3' not in SCHEMA['states']
