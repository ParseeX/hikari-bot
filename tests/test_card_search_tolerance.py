"""从查卡入口验证标点、错字容错及歧义提示，卡片资料始终只读。"""
import asyncio
import importlib.util
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException

from hikari_bot.services import ygocard
from scripts.card_catalog import catalog as importer
from test_catalog_service import catalog, record
from test_ygocard_query import matchers


def add_cards(catalog, *records):
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {str(row['cid']): row for row in records}, {})


@pytest.mark.parametrize('query', [
    '青眼白隆', '青·眼白龙', '青白眼龙', 'Blue Eyes White Dragon',
    'Ｂｌｕｅ－Ｅｙｅｓ　Ｗｈｉｔｅ　Ｄｒａｇｏｎ',
])
def test_ordinary_lookup_tolerates_typos_and_punctuation(catalog, monkeypatch, query):
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    info = asyncio.run(ygocard.get_card_info(query))
    assert info and info['id'] == 1234
    assert catalog.image_ids(query) == [1234, 1235]


def test_three_character_name_accepts_one_typo(catalog, monkeypatch):
    add_cards(catalog, record(7, 7000, '灰流丽'))
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    info = asyncio.run(ygocard.get_card_info('灰流莉'))
    assert info and info['id'] == 7000


def test_long_names_allow_two_edits_but_short_names_do_not(catalog):
    add_cards(catalog, record(7, 7000, '测试系列的终极召唤龙'))
    info = catalog.search('测试系烈的终极召换龙', fuzzy=True)
    assert info and info['id'] == 7000
    assert catalog.search('青岩白隆', fuzzy=True) is None


def test_fuzzy_search_does_not_guess_short_queries_or_numbers(catalog):
    add_cards(catalog, record(7, 7000, 'No.39 希望皇 霍普'))
    for query in ['灰莉', '青隆', '1236', 'No.38 希望皇 霍普', '%', '_', 'x' * 129]:
        assert catalog.search(query, fuzzy=True) is None
    assert catalog.search('青眼白隆', fuzzy=False) is None
    assert catalog.image_ids('青眼白龙 异画1') == []


def test_ambiguous_corrections_offer_distinct_cards_and_exact_names_still_win(catalog):
    add_cards(catalog, record(7, 7000, '青眼黑龙'))
    with pytest.raises(ValueError) as error:
        catalog.search('青眼红龙', fuzzy=True)
    text = str(error.value)
    assert text.count('青眼白龙') == 1
    assert '青眼黑龙' in text
    assert '00001234' in text and '00007000' in text
    assert '重新查询' in text
    assert catalog.search('青眼白龙', fuzzy=True)['id'] == 1234
    assert catalog.search('青眼黑龙', fuzzy=True)['id'] == 7000


@pytest.mark.parametrize('command', ['效果查询', '卡密查询', '卡图查询', '共界计算'])
def test_commands_display_ambiguous_cards(catalog, matchers, monkeypatch, command):
    add_cards(catalog, record(7, 7000, '青眼黑龙'))
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    matcher = matchers[command]
    with pytest.raises(FinishedException):
        asyncio.run(matcher.callback(None, None, Message('青眼红龙')))
    assert len(matcher.messages) == 1
    text = str(matcher.messages[0])
    assert '青眼白龙' in text and '青眼黑龙' in text
    assert '重新查询' in text


def test_price_command_shows_candidates_before_requesting_prices(catalog, monkeypatch):
    add_cards(catalog, record(7, 7000, '青眼黑龙'))
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    spec = importlib.util.spec_from_file_location('price_tolerance_adapter',
                                                'hikari_bot/plugins/monitors/cardrush_query.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    async def no_prices(*args, **kwargs):
        raise AssertionError('未确定卡片身份时不能请求价格')

    monkeypatch.setattr(adapter, 'ComparisonService',
                        lambda *args: SimpleNamespace(versions=no_prices))

    class Matcher:
        def __init__(self):
            self.messages = []

        def handle(self):
            def register(callback):
                self.start = callback
                return callback
            return register

        def got(self, *args):
            return lambda callback: callback

        async def finish(self, message):
            self.messages.append(message)
            raise FinishedException

    matcher = Matcher()
    adapter.register_price_query(matcher, None)
    state = {}
    with pytest.raises(FinishedException):
        asyncio.run(matcher.start(state, Message('青眼红龙')))
    assert state == {}
    assert len(matcher.messages) == 1
    assert '青眼白龙' in matcher.messages[0] and '青眼黑龙' in matcher.messages[0]
