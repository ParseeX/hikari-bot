import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from scripts.card_catalog import catalog as importer
from hikari_bot.features.card_prices.client import JhsUnavailable
from hikari_bot.features.card_prices.models import JhsPrice
from hikari_bot.features.card_prices.service import ComparisonService
from hikari_bot.features.card_prices.version_catalog import VersionCatalog, shared_version_catalog


NAME = '伝説の白き龍'
PACK = {'id': 10, 'name': '传说的白龙决斗套装'}


def response(ids=(1, 2, 3, 4)):
    return {'complete': True, 'card_id': 5581, 'versions': [
        {'id': i, 'card_id': 5581, 'object_type': 'card', 'name_jp': NAME,
         'name_cn': '传说的白龙', 'aliases': [], 'number': f'TEST-JP00{i}',
         'rarity': 'SR' if i % 2 else 'UR', 'packs': [PACK]} for i in ids]}


@pytest.fixture
def data(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    db = importer.connect(path)
    with db:
        importer.import_cards(db, {'10321': {'cid': 10321, 'id': 0, 'jp_name': NAME,
            'cn_name': '传说的白龙', 'data': {'type': 0x800021, 'ot': 1}}}, {})
    rows = [{**r, 'pack': PACK} for r in response((1, 2))['versions']]
    importer.import_pack(db, None, rows, product=PACK)
    db.close()
    return path


class Client:
    def __init__(self):
        self.calls = 0
        self.price_calls = 0
        self.payload = response()
        self.error = None

    async def versions(self, name):
        raise AssertionError('已有卡片身份，不应退回名称搜索')

    async def complete_versions(self, card_id):
        self.calls += 1
        assert card_id == 5581
        if self.error:
            raise self.error
        return self.payload

    async def prices(self, ids):
        self.price_calls += 1
        return {i: JhsPrice(i, Decimal(self.price_calls), None, None) for i in ids}


def test_partial_pack_cache_fills_all_versions_then_survives_restart(data):
    client = Client()
    async def run():
        service = ComparisonService(client, None, VersionCatalog(data))
        selected = await service.versions(NAME, 'SR', 'TEST', catalog_id=1)
        assert [v.id for v in selected] == [1, 3]
        # 新实例模拟 Bot 重启；前一次筛选不能污染完整缓存。
        client.error = JhsUnavailable('phone offline')
        restarted = ComparisonService(client, None, VersionCatalog(data))
        assert [v.id for v in await restarted.versions(NAME, catalog_id=1)] == [1, 2, 3, 4]
        assert client.calls == 1
    asyncio.run(run())
    with sqlite3.connect(data) as db:
        assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM jhs_version_products').fetchone()[0] == 4


def test_two_query_modes_share_one_inflight_fetch_and_prices_remain_live(data):
    async def run():
        client = Client()
        store = shared_version_catalog(data)
        assert store is shared_version_catalog(data)
        first, second = ComparisonService(client, None, store), ComparisonService(client, None, store)
        a, b = await asyncio.gather(first.versions(NAME, catalog_id=1), second.versions(NAME, catalog_id=1))
        assert a == b and client.calls == 1
        class Prices:
            async def search_prices(self, *args):
                return []
        first.cardrush = Prices()
        one = await first.compare(NAME, a)
        two = await first.compare(NAME, a)
        assert one[0].jhs.minimum == 1 and two[0].jhs.minimum == 2
        assert client.price_calls == 2
    asyncio.run(run())


def test_expired_cache_returns_immediately_and_refreshes_in_background(data):
    async def run():
        clock = [1000]
        client = Client()
        store = VersionCatalog(data, ttl=10, clock=lambda: clock[0])
        service = ComparisonService(client, None, store)
        await service.versions(NAME, catalog_id=1)
        clock[0] += 11
        gate = asyncio.Event()
        async def slow(card_id):
            await gate.wait()
            return response((1, 2, 3, 4, 5))
        client.complete_versions = slow
        old = await asyncio.wait_for(service.versions(NAME, catalog_id=1), .5)
        assert len(old) == 4
        task = store.tasks[1]
        gate.set()
        await task
        assert len(await service.versions(NAME, catalog_id=1)) == 5
    asyncio.run(run())


def test_failed_refresh_keeps_snapshot_and_uses_retry_backoff(data):
    async def run():
        clock = [1000]
        client = Client()
        store = VersionCatalog(data, ttl=10, retry_delay=30, clock=lambda: clock[0])
        service = ComparisonService(client, None, store)
        await service.versions(NAME, catalog_id=1)
        clock[0] += 11
        client.error = JhsUnavailable('unavailable')
        assert len(await service.versions(NAME, catalog_id=1)) == 4
        with pytest.raises(JhsUnavailable):
            await store.tasks[1]
        assert len(await service.versions(NAME, catalog_id=1)) == 4
        assert client.calls == 2
        clock[0] += 31
        client.error = None
        assert len(await service.versions(NAME, catalog_id=1)) == 4
        await store.tasks[1]
        assert client.calls == 3
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['empty', 'wrong_card', 'duplicate', 'missing_pack', 'incomplete'])
def test_invalid_response_never_marks_partial_list_complete(data, failure):
    client = Client()
    if failure == 'empty':
        client.payload['versions'] = []
    elif failure == 'wrong_card':
        client.payload['versions'][0]['card_id'] = 999
    elif failure == 'duplicate':
        client.payload['versions'].append(deepcopy(client.payload['versions'][0]))
    elif failure == 'missing_pack':
        client.payload['versions'][-1]['packs'] = []
    else:
        client.payload['complete'] = False
    with pytest.raises(JhsUnavailable):
        asyncio.run(ComparisonService(client, None, VersionCatalog(data)).versions(NAME, catalog_id=1))
    with sqlite3.connect(data) as db:
        assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM jhs_card_version_sync').fetchone()[0] == 0


def test_write_conflict_rolls_back_earlier_versions_and_product_links(data):
    with sqlite3.connect(data) as db:
        db.execute("UPDATE jhs_versions SET object_type='goods' WHERE jhs_version_id=2")
    payload = response((3, 2))
    store = VersionCatalog(data)
    with pytest.raises(ValueError, match='冲突'):
        store.save(1, NAME, [5581], [5581], [payload])
    with sqlite3.connect(data) as db:
        assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM jhs_version_products').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM jhs_card_version_sync').fetchone()[0] == 0


def test_refresh_retains_historical_rows_and_multiple_product_links(data):
    store = VersionCatalog(data)
    payload = response((1, 3))
    payload['versions'][0]['packs'].append({'id': 20, 'name': '另一个商品'})
    store.save(1, NAME, [5581], [5581], [payload])
    assert [v.id for v in store.load(1, NAME)[1]] == [1, 3]
    with sqlite3.connect(data) as db:
        assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM jhs_version_products WHERE jhs_version_id=1').fetchone()[0] == 2


def test_pack_import_of_new_version_invalidates_completeness(data):
    store = VersionCatalog(data)
    store.save(1, NAME, [5581], [5581], [response()])
    db = importer.connect(data)
    importer.import_pack(db, None, [{**response((5,))['versions'][0], 'pack': PACK}], product=PACK)
    db.close()
    assert store.load(1, NAME)[1] is None


def test_unmapped_card_can_discover_and_bind_one_verified_identity(data):
    with sqlite3.connect(data) as db:
        db.execute('UPDATE jhs_cards SET card_id=NULL')
    client = Client()
    async def search(name):
        from hikari_bot.features.card_prices.models import CardVersion
        return [CardVersion.from_dict(r) for r in response()['versions']]
    client.versions = search
    found = asyncio.run(ComparisonService(client, None, VersionCatalog(data)).versions(NAME, catalog_id=1))
    assert len(found) == 4
    assert VersionCatalog(data).load(1, NAME)[0] == [5581]
