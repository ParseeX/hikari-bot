"""群/私聊效果按钮一致，卡价罕贵按钮绑定当次查询与原会话。"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import Comparison
from hikari_bot.qq_official.card_actions import CardActions
from hikari_bot.qq_official.callbacks import CallbackController
from hikari_bot.qq_official.commands import parse_command
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.qq_official.prices import PriceQueries
from test_qq_alias_choices import CONFIG, click, messages, source
from test_qq_preview import INFO, NOW
from test_history_markdown import transport
from test_catalog_service import catalog


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
@pytest.mark.parametrize('length', [1, 500, 2000])
def test_effect_buttons_are_available_in_both_chats_and_last_long_page(kind, length):
    async def run():
        info = {**INFO, 'text': {**INFO['text'], 'desc': '完整效果。' * length}}
        client = transport()
        pager = ReplyPager(now=lambda: NOW)
        action = CardActions(Mock(search=Mock(return_value=info)), image_loader=AsyncMock(), price_backend=Mock())
        await action.handle('效果', 'card', CONFIG, NamedFileBot(client), source(kind), pager)
        while pager.pending:
            incoming = source(kind); incoming.id = 'continue-' + str(len(messages(client)))
            await pager.send(NamedFileBot(client), incoming)
        sent = [m.json for m in messages(client)]
        buttons = [b for row in sent[-1]['keyboard']['content']['rows'] for b in row['buttons']]
        assert [b['render_data']['label'] for b in buttons] == ['查看卡图', '查询卡价']
        assert all('keyboard' not in m for m in sent[:-1])
        assert all(m['msg_type'] == 2 for m in sent)
    asyncio.run(run())


@pytest.mark.parametrize('change', ['new-query', 'cancel', 'expired', 'other-session'])
def test_old_rarity_button_cannot_select_current_query(change):
    async def run():
        clock = [0]
        backend = PriceQueries(None, None, None, clock=lambda: clock[0])
        backend.comparison = Mock(versions=AsyncMock(return_value=[CardVersion(1, 'BOX-001', 'UR')]),
                                  compare=AsyncMock())
        await backend.start(INFO, 'owner')
        generation, _ = backend.selection_options('owner')
        if change == 'new-query':
            await backend.start({**INFO, 'jp_name': 'another card'}, 'owner')
        elif change == 'cancel':
            backend.cancel('owner')
        elif change == 'expired':
            clock[0] = 181
        assert await backend.choose('1', 'other' if change == 'other-session' else 'owner',
                                    expected_generation=generation) == ''
        backend.comparison.compare.assert_not_awaited()
        if change == 'new-query':
            assert backend.sessions['owner'][1] == 'another card'
    asyncio.run(run())


def test_plain_text_rarity_selection_still_supports_manual_choice():
    from hikari_bot.qq_official.output_templates import default, draft
    async def run():
        backend = PriceQueries(None, None, None)
        version = CardVersion(1, 'BOX-001', 'UR')
        backend.comparison = Mock(versions=AsyncMock(return_value=[version]),
            compare=AsyncMock(return_value=[Comparison(version, None, ())]))
        client = transport()
        action = CardActions(Mock(search=Mock(return_value=INFO)), price_backend=backend)
        spec = default('price.prompt'); spec['mode'] = 'text'
        with draft('price.prompt', spec):
            await action.handle('卡价', 'card', CONFIG, NamedFileBot(client), source('group'), ReplyPager(now=lambda: NOW))
        payload = messages(client)[0].json
        assert payload['msg_type'] == 0 and '1. UR' in payload['content']
        assert 'BOX-' not in payload['content'] and 'mqqapi:' not in payload['content']
        assert 'keyboard' not in payload
        assert 'BOX-001' in await backend.choose('1', ('synthetic-app', 'group', 'synthetic-group', 'only-user'))
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
def test_rarity_click_returns_price_once_without_alias_notice_or_pack_in_options(kind):
    async def run():
        versions = [CardVersion(i, 'BOX-00' + str(i), rarity, INFO['jp_name'])
                    for i, rarity in enumerate(['UR', 'SR'], 1)]
        backend = PriceQueries(None, None, None)
        backend.comparison = Mock(versions=AsyncMock(return_value=versions), compare=AsyncMock(
            side_effect=lambda name, selected, **kw: [Comparison(selected[0], JhsPrice(selected[0].id, Decimal('2'), None, None), ())]))
        client = transport(); client.put_interaction = AsyncMock()
        pager = ReplyPager(now=lambda: NOW)
        action = CardActions(Mock(search=Mock(return_value=INFO)), price_backend=backend)
        async def process(proxy, incoming):
            command, argument = parse_command(incoming.get_plaintext())
            await action.handle(command, argument, CONFIG, proxy, incoming, pager)
        controller = CallbackController(CONFIG, process)
        incoming = source(kind)
        await action.handle('卡价', 'card', CONFIG, controller.wrap(NamedFileBot(client), incoming), incoming, pager)
        payload = messages(client)[0].json
        assert payload['msg_type'] == 2 and 'mqqapi://aio/inlinecmd?' in payload['markdown']['content']
        assert 'BOX-' not in payload['markdown']['content']
        buttons = [b for row in payload['keyboard']['content']['rows'] for b in row['buttons']]
        assert [b['render_data']['label'] for b in buttons] == ['SR', 'UR']
        event = click(buttons[0], kind)
        await controller.handle(NamedFileBot(client), event)
        await asyncio.gather(*controller.tasks)
        await controller.handle(NamedFileBot(client), event.model_copy(update={'id': 'duplicate'}))
        backend.comparison.compare.assert_awaited_once_with(INFO['jp_name'], [versions[1]], japanese=False)
        result = messages(client)[-1].json
        assert '2.00 元' in result['content'] and 'BOX-002' in result['content']
        assert '添加别名' not in result['content']
        assert len(messages(client)) == 2
    asyncio.run(run())


def test_plugin_rarity_buttons_paginate_and_reject_foreign_clicks(catalog):
    import subprocess
    import sys
    script = r"""
import asyncio, sys
from pathlib import Path
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, 'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp', _env_file=[], log_level='ERROR')
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.prices import PriceQueries
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import Comparison
from test_qq_alias_choices import source, click, messages
from test_history_markdown import transport
controllers=[]; factory=plugin.CallbackController
def controller(*args, **kwargs):
    value=factory(*args, **kwargs); controllers.append(value); return value
plugin.CallbackController=controller
catalog=CardCatalog(Path(sys.argv[1]))
config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user','other'}),
                  accept_joined_groups=True,catalog_path=catalog.path)
async def run():
    backend=PriceQueries(None,None,None)
    rarities=['CR','GR','HR','N','P','R','SE','SR','UR']
    versions=[CardVersion(i+1,'BOX-'+str(i),r,'name') for i,r in enumerate(rarities)]
    backend.comparison=Mock(versions=AsyncMock(return_value=versions),compare=AsyncMock(
        side_effect=lambda name, selected, **kw:[Comparison(selected[0],JhsPrice(1,Decimal('3'),None,None),())]))
    handler=plugin.register(config,queries=Queries(catalog),price_backend=backend).handlers[0].call
    ctl=controllers[0]
    for kind in ['c2c','group','group-no-at']:
        client=transport();client.put_interaction=AsyncMock()
        incoming=source(kind);incoming.id=kind;incoming.content='卡价 1234'
        await handler(client,incoming)
        buttons=[b for row in messages(client)[-1].json['keyboard']['content']['rows'] for b in row['buttons']]
        assert [b['render_data']['label'] for b in buttons]==rarities[:8]+['下一页']
        foreign={'user_openid':'other'} if kind=='c2c' else {'group_member_openid':'other'}
        await ctl.handle(NamedFileBot(client),click(buttons[0],kind,id=kind+'-foreign',**foreign))
        assert len(messages(client))==1
        await ctl.handle(NamedFileBot(client),click(buttons[-1],kind,id=kind+'-page'))
        await asyncio.gather(*ctl.tasks)
        last=messages(client)[-1].json
        buttons=[b for row in last['keyboard']['content']['rows'] for b in row['buttons']]
        assert [b['render_data']['label'] for b in buttons]==['UR','上一页']
        await ctl.handle(NamedFileBot(client),click(buttons[0],kind,id=kind+'-select'))
        await asyncio.gather(*ctl.tasks)
        result=messages(client)[-1].json
        assert '3.00 元' in result['content'] and 'BOX-8' in result['content']
        assert '添加别名' not in result['content']
        assert len(messages(client))==3
asyncio.run(run())
"""
    result = subprocess.run([sys.executable, '-c', script, str(catalog.path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-2000:]
