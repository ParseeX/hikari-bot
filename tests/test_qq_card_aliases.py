"""官方 QQ 查卡入口使用同一套容错规则和完整别名候选。"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.card_actions import CardActions
from hikari_bot.qq_official.commands import reply_for, is_group_request
from test_catalog_service import catalog, record
from scripts.card_catalog import catalog as importer
from test_qq_card_actions import CONFIG
from test_qq_preview import bot, event
from test_qq_migration import request


@pytest.mark.parametrize('command', ['卡密', '效果'])
def test_official_text_commands_enable_fuzzy(catalog, command):
    response = asyncio.run(reply_for(f'{command} 白龙青眼', catalog))
    assert ('00001234' if command == '卡密' else '青眼白龙') in response


@pytest.mark.parametrize('command', ['卡图', '效果', '卡价'])
def test_official_card_commands_enable_fuzzy(catalog, command):
    spy = Mock(wraps=catalog)
    pager = Mock(send=AsyncMock())
    action = CardActions(spy)
    asyncio.run(action.handle(command, '白龙青眼', CONFIG, bot(), event(), pager))
    spy.search.assert_called_once_with('白龙青眼', fuzzy=True)
    pager.send.assert_awaited_once()


def test_official_calculator_enables_fuzzy(catalog):
    result = asyncio.run(Queries(catalog).handle(None, '共界计算', '白龙青眼'))
    assert '2000' in result.text and '100000002' in result.text


def test_official_commands_display_all_alias_targets(catalog):
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {'7': record(7, 7000, '青眼黑龙')}, {})
    catalog.add_alias('1234', '小龙')
    catalog.add_alias('7000', '小龙')
    response = asyncio.run(reply_for('卡密 小龙', catalog))
    result = asyncio.run(Queries(catalog).handle(None, '共界计算', '小龙'))
    pager = Mock(send=AsyncMock())
    asyncio.run(CardActions(catalog).handle('效果', '小龙', CONFIG, bot(), event(), pager))
    for text in [response, result.text, pager.send.call_args.args[2]]:
        assert '青眼白龙' in text and '青眼黑龙' in text
        assert '00001234' in text and '00007000' in text


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group_at'])
def test_any_connected_user_can_add_shared_alias(catalog, kind):
    from hikari_bot.qq_official.admin import AdminQueries
    from hikari_bot.qq_official.config import PilotConfig
    admin = AdminQueries(PilotConfig('app', 'fake', admins=frozenset({'admin'})))
    queries = Queries(catalog, admin=admin)
    result = asyncio.run(queries.handle(request(kind=kind, user='ordinary-user'), '添加别名', '1234 社长的龙'))
    assert '已添加别名' in result.text
    assert '00001234' in asyncio.run(reply_for('卡密 社长的龙', catalog))
    assert is_group_request('添加别名 1234 社长的龙')
