"""简中共享查价入口，隔离分区、价格来源和曲线按钮。"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.features.card_prices.client import JhsClient, JhsUnavailable
from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import ComparisonService, group_rarities
from hikari_bot.features.card_prices.simplified import SimplifiedVersions
from hikari_bot.qq_official.prices import PriceQueries
from hikari_bot.qq_official.price_history import HistoryButtons
from hikari_bot.qq_official.commands import is_group_request

NAME = '灰流うらら'
JP = CardVersion(500828, 'LPST-JP009', 'PSER', NAME, '灰流丽', 8072)


def payload(rows=None):
    if rows is None:
        rows = [dict(id=143689, number='SD48-SC017', rarity='N'),
                dict(id=140802, number='TTP1-SC080', rarity='SER'),
                dict(id=58651, number='SD35-SC019', rarity='N')]
    return dict(complete=True, card_id=8072, name_jp=NAME, game_sub_key='sc', versions=[
        dict(**row, card_id=8072, name_jp=NAME, name_cn='灰流丽', game_sub_key='sc',
             object_type='card', packs=[dict(id=4842, name='主题卡组')]) for row in rows])


def client(data=None):
    return Mock(versions=AsyncMock(return_value=[JP]),
                complete_versions=AsyncMock(return_value=payload() if data is None else data),
                prices=AsyncMock(side_effect=lambda ids, **kw: {
                    i: JhsPrice(i, Decimal('4'), Decimal('4.50'), '2026-11-18') for i in ids}))


def test_query_combines_partitions_and_caches_complete_sc_list_without_writing_jp_cache():
    async def run():
        jhs = client()
        cache = Mock(get=AsyncMock(return_value=[JP]))
        service = ComparisonService(jhs, None, cache)
        for _ in range(2):
            result = await service.versions(NAME, names_cn=('灰流丽',), catalog_id=1, include_simplified=True)
            groups = group_rarities(result)
            assert list(groups) == ['PSER', '简中N', '简中SER']
            assert [v.number for v in groups['简中N']] == ['SD35-SC019', 'SD48-SC017']
            assert groups['简中SER'][0].rarity == 'SER'
        jhs.complete_versions.assert_awaited_once_with(8072, game_sub_key='sc')
        assert cache.get.await_count == 2
        only_sc = await service.versions(NAME, '简中N', names_cn=('灰流丽',), catalog_id=1, include_simplified=True)
        assert len(only_sc) == 2 and all(v.game_sub_key == 'sc' and v.rarity == 'N' for v in only_sc)
        assert await service.versions(NAME, catalog_id=1) == [JP]
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['wrong-name', 'wrong-partition', 'wrong-owner', 'row-partition', 'duplicate', 'missing-complete'])
def test_invalid_sc_identity_is_not_cached_or_silently_hidden(problem):
    async def run():
        data = payload()
        if problem == 'wrong-name': data['name_jp'] = '別のカード'
        if problem == 'wrong-partition': data['game_sub_key'] = 'ocg'
        if problem == 'wrong-owner': data['versions'][0]['card_id'] = 999
        if problem == 'row-partition': data['versions'][0]['game_sub_key'] = 'ocg'
        if problem == 'duplicate': data['versions'].append(data['versions'][0])
        if problem == 'missing-complete': data['complete'] = False
        source = SimplifiedVersions(client(data))
        with pytest.raises(JhsUnavailable):
            await source.get(NAME, ('灰流丽',), [JP])
        assert not source.cache
    asyncio.run(run())


def test_empty_sc_result_is_valid_and_cache_expires():
    async def run():
        now = [0]
        jhs = client(payload([]))
        source = SimplifiedVersions(jhs, ttl=10, clock=lambda: now[0])
        assert await source.get(NAME, (), [JP]) == []
        jhs.complete_versions.return_value = payload()
        assert await source.get(NAME, (), [JP]) == []
        now[0] = 11
        assert len(await source.get(NAME, (), [JP])) == 3
    asyncio.run(run())


def test_sc_without_jp_versions_discovers_and_verifies_same_identity():
    async def run():
        jhs = client()
        jhs.versions.return_value = [CardVersion.from_dict(payload()['versions'][0])]
        result = await SimplifiedVersions(jhs).get(NAME, ('灰流丽',), [])
        assert len(result) == 3
        jhs.versions.assert_awaited_once_with(NAME, game_sub_key='sc')
    asyncio.run(run())


def test_sc_quote_is_single_markdown_with_all_versions_and_no_cardrush_or_curve():
    async def run():
        jhs = client()
        cardrush = Mock(search_prices=AsyncMock(return_value=[]))
        backend = PriceQueries(jhs, cardrush, None)
        await backend.start({'jp_name': NAME, 'cn_name': '灰流丽'}, 'user')
        assert backend.selection_options('user')[1] == ('PSER', '简中N', '简中SER')
        quote = await backend.choose('简中N', 'user')
        assert quote.mode == 'markdown'
        assert f'## {NAME} | 简中N' in quote
        assert '### SD48-SC017' in quote and '### SD35-SC019' in quote
        assert 'TTP1-SC080' not in quote
        assert quote.count('集换社最低价') == 2 and quote.count('集换价') == 2
        assert 'Cardrush' not in quote and '数据库暂无' not in quote
        cardrush.search_prices.assert_not_awaited()
        jhs.prices.assert_awaited_once_with([58651, 143689], game_sub_key='sc')
        assert HistoryButtons(Mock(), Mock()).quote_keyboard(None, None, quote) is None
        assert is_group_request('简中N', price_active=True)
        assert not is_group_request('简中N')
        # 专门的日版在售查询不提供简中入口。
        await backend.start({'jp_name': NAME}, 'user', japanese=True)
        assert backend.selection_options('user')[1] == ('PSER',)
    asyncio.run(run())


def test_sc_price_requests_keep_partition_across_batches_and_validate_id_set():
    async def run():
        jhs = JhsClient('http://unused', 'test-only')
        jhs._post = AsyncMock(side_effect=lambda path, data: {'prices': [
            dict(id=i, min_price='4', market_price='4.5') for i in data['version_ids']]})
        result = await jhs.prices(list(range(1, 24)), game_sub_key='sc')
        assert len(result) == 23
        assert [c.args[1]['game_sub_key'] for c in jhs._post.await_args_list] == ['sc', 'sc']
        jhs._post.return_value = {'prices': [{'id': 99}]}
        jhs._post.side_effect = None
        with pytest.raises(JhsUnavailable):
            await jhs.prices([1], game_sub_key='sc')
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
@pytest.mark.parametrize('manual', [False, True])
def test_sc_button_or_manual_choice_sends_all_versions_in_one_reply(kind, manual):
    from hikari_bot.qq_official.card_actions import CardActions
    from hikari_bot.qq_official.callbacks import CallbackController
    from hikari_bot.qq_official.commands import parse_command
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.media import NamedFileBot
    from test_qq_alias_choices import CONFIG, source, messages, click
    from test_qq_preview import NOW
    from test_history_markdown import transport

    async def run():
        backend = PriceQueries(client(), Mock(search_prices=AsyncMock(return_value=[])), None)
        action = CardActions(Mock(search=Mock(return_value={'jp_name': NAME, 'cn_name': '灰流丽'})), price_backend=backend)
        pager = ReplyPager(now=lambda: NOW)
        client_bot = transport(); client_bot.put_interaction = AsyncMock()
        async def process(bot, incoming):
            command, argument = parse_command(incoming.get_plaintext())
            await action.handle(command, argument, CONFIG, bot, incoming, pager)
        ctl = CallbackController(CONFIG, process)
        event = source(kind)
        await action.handle('卡价', '灰流丽', CONFIG, ctl.wrap(NamedFileBot(client_bot), event), event, pager)
        buttons = [b for row in messages(client_bot)[-1].json['keyboard']['content']['rows'] for b in row['buttons']]
        assert [b['render_data']['label'] for b in buttons] == ['PSER', '简中N', '简中SER']
        button = next(b for b in buttons if b['render_data']['label'] == '简中N')
        if manual:
            event = source(kind); event.id = 'manual-sc'
            assert await action.handle('简中N', '', CONFIG, NamedFileBot(client_bot), event, pager)
        else:
            await ctl.handle(NamedFileBot(client_bot), click(button, kind))
            await asyncio.gather(*ctl.tasks)
        sent = messages(client_bot)
        assert len(sent) == 2
        result = sent[-1].json
        assert result['msg_type'] == 2
        assert 'SD48-SC017' in result['markdown']['content'] and 'SD35-SC019' in result['markdown']['content']
        assert 'TTP1-SC080' not in result['markdown']['content']
        assert 'Cardrush' not in result['markdown']['content'] and 'keyboard' not in result
        assert not pager.pending
    asyncio.run(run())
