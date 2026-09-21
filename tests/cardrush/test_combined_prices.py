import asyncio
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException, RejectedException

from hikari_bot.features.card_prices.client import JhsClient, JhsUnavailable
from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import (
    Comparison, ComparisonService, format_comparison, group_rarities,
)
from hikari_bot.features.cardrush.models import PriceRecord
from hikari_bot.features.cardrush.repository import PriceRepository
from hikari_bot.features.cardrush.service import CardrushService


def test_exact_edition_and_rarity_matching_with_real_database(tmp_path):
    repo = PriceRepository(tmp_path / 'prices.db')
    records = [
        PriceRecord(1, '原石の皇脈', 100, 'スーパー', 'LOCR-JP076', None),
        PriceRecord(2, '原石の皇脈', 200, 'レリーフ', 'LOCR-JP076', None),
        PriceRecord(3, '原石の皇脈', 300, 'スーパー', 'ROTA-JP058', None),
        PriceRecord(4, '原石の皇脈改', 999, 'スーパー', 'LOCR-JP076', None),
        PriceRecord(5, '原石の皇脈', 888, 'スーパー', 'LOCR-JP076A', None),
        PriceRecord(6, '原石の皇脈', 777, 'シークレットSPECIALREDVer.', 'LOCR-JP076', None),
    ]
    repo.save_prices(records, observed_at='2026-09-21T00:00:00Z')
    class Jhs:
        async def prices(self, ids):
            return {i: JhsPrice(i, Decimal('.50'), Decimal('2.20'), '2026.09.20') for i in ids}
    versions = [CardVersion(504445, 'LOCR-JP076', 'SR'), CardVersion(124551, 'ROTA-JP058', 'SR')]
    rows = asyncio.run(ComparisonService(Jhs(), CardrushService(repo)).compare('原石の皇脈', versions))
    assert [[p.product_id for p in r.cardrush] for r in rows] == [[1], [3]]
    text = '\n'.join(format_comparison('原石の皇脈', 'SR', rows))
    assert '集换社最低价：0.50 元\n' in text and '集换价：2.20 元\n' in text
    assert 'Cardrush 买取价：100 円' in text and 'Cardrush 买取价：300 円' in text
    assert '（' not in text and '）' not in text
    assert '999' not in text and '888' not in text


def test_both_sources_run_concurrently_and_partial_failure_is_preserved():
    async def run():
        jhs_started, db_started = asyncio.Event(), asyncio.Event()
        class Jhs:
            async def prices(self, ids):
                jhs_started.set()
                await db_started.wait()
                raise JhsUnavailable('unavailable')
        class Db:
            async def search_prices(self, *args):
                db_started.set()
                await jhs_started.wait()
                return [SimpleNamespace(name='原石の皇脈', model_number='LOCR-JP076', rarity='スーパー', price=100, changed_at='2026-09-20')]
        return await asyncio.wait_for(ComparisonService(Jhs(), Db()).compare('原石の皇脈', [CardVersion(1, 'LOCR-JP076', 'SR')]), 1)
    rows = asyncio.run(run())
    text = format_comparison('原石の皇脈', 'SR', rows)[0]
    assert '集换社：暂时不可用' in text and '100 円' in text


def test_rarity_selection_keeps_all_printings_and_excludes_fuzzy_names():
    versions = [CardVersion(1, 'A-JP001', 'SR', '原石の皇脈'),
                CardVersion(2, 'B-JP001', 'SR', '原石の皇脈'),
                CardVersion(3, 'A-JP001', 'UTR', '原石の皇脈'),
                CardVersion(4, 'C-JP001', 'SR', '原石の皇脈改')]
    class Jhs:
        async def versions(self, name):
            assert name == '原石の皇脈'
            return versions
    result = asyncio.run(ComparisonService(Jhs(), None).versions('原石の皇脈'))
    groups = group_rarities(result + [result[0]])
    assert [v.id for v in groups['SR']] == [1, 2]
    assert [v.id for v in groups['UTR']] == [3]


def test_missing_money_is_not_displayed_as_zero_and_mismatched_ids_fail():
    row = Comparison(CardVersion(1, 'A-JP001', 'SR'), JhsPrice(1, None, Decimal('0'), None), ())
    text = format_comparison('カード', 'SR', [row])[0]
    assert '最低价：暂无数据' in text and '集换价：0.00 元' in text
    client = JhsClient('http://localhost', 'not-a-real-secret')
    async def post(*args):
        return {'prices': [{'id': 999, 'min_price': '1'}]}
    client._post = post
    with pytest.raises(JhsUnavailable):
        asyncio.run(client.prices([1]))


def test_request_preserves_japanese_original_and_never_uses_login_token(monkeypatch):
    captured = []
    transport = httpx.MockTransport(lambda req: captured.append(req) or httpx.Response(200, json={'versions': []}))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    asyncio.run(JhsClient('http://localhost:8791', 'bridge-only-token').versions('青眼の白龍 ＜特別版＞'))
    import json
    assert json.loads(captured[0].content) == {'name_jp': '青眼の白龍 ＜特別版＞'}
    assert captured[0].headers['Authorization'] == 'Bearer bridge-only-token'


def test_interaction_select_cancel_invalid_and_separate_state(monkeypatch):
    path = Path('hikari_bot/plugins/monitors/cardrush_query.py')
    spec = importlib.util.spec_from_file_location('price_query_test_adapter', path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    calls = []
    class Service:
        async def versions(self, name, rarity, prefix, names_cn):
            assert name == '原石の皇脈'
            return [CardVersion(1, 'LOCR-JP076', 'SR'), CardVersion(2, 'LOCR-JP076', 'UTR')]
        async def compare(self, name, versions):
            calls.append([v.id for v in versions])
            return [Comparison(v, JhsPrice(v.id, Decimal('.5'), Decimal('2.2'), None), ()) for v in versions]
    async def card_info(name):
        return {'jp_name': '原石の皇脈'}
    monkeypatch.setattr(adapter, 'get_card_info', card_info)
    monkeypatch.setattr(adapter, 'ComparisonService', lambda *args: Service())
    class Matcher:
        messages = []
        def handle(self):
            def register(fn):
                self.start = fn
                return fn
            return register
        def got(self, *args):
            def register(fn):
                self.selected = fn
                return fn
            return register
        async def send(self, text):
            self.messages.append(text)
        async def finish(self, text=None):
            if text:
                self.messages.append(text)
            raise FinishedException
        async def reject(self, text):
            self.messages.append(text)
            raise RejectedException
    matcher = Matcher()
    adapter.register_price_query(matcher, None)
    first, second = {}, {}
    async def run():
        await matcher.start(first, Message('原石之皇脉'))
        await matcher.start(second, Message('原石之皇脉'))
        with pytest.raises(RejectedException):
            await matcher.selected(first, Message('99'))
        with pytest.raises(FinishedException):
            await matcher.selected(first, Message('SR'))
        with pytest.raises(FinishedException):
            await matcher.selected(second, Message('取消'))
        second['price_expires'] = 0
        with pytest.raises(FinishedException):
            await matcher.selected(second, Message('1'))
    asyncio.run(run())
    assert calls == [[1]]
    assert any('集换社最低价' in m and 'Cardrush' in m for m in matcher.messages)
    assert '已取消卡价查询。' in matcher.messages
    assert not any('正在查询' in m for m in matcher.messages)


def test_live_chinese_origin_alias_is_not_mistaken_for_japanese():
    actual = CardVersion.from_dict({'id': 504445, 'card_id': 16724,
        'number': 'LOCR-JP076', 'rarity': 'SR', 'name_jp': '',
        'name_cn': '原石之皇脉', 'aliases': ['原石的皇脉']})
    other = CardVersion(2, 'A-JP001', 'SR', '', '原石之龙', 99)
    class Jhs:
        async def versions(self, name):
            assert name == '原石の皇脈'
            return [actual, other]
    service = ComparisonService(Jhs(), None)
    assert asyncio.run(service.versions('原石の皇脈', names_cn=('原石的皇脉',))) == [actual]
    with pytest.raises(JhsUnavailable):
        asyncio.run(service.versions('原石の皇脈'))
