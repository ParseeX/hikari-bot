import asyncio
from datetime import datetime, timezone
import json
import threading

import pytest

from hikari_bot.services.mycard_archive.series_stats import SeriesStats


NOW = datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()


def payload(updated=1):
    return {'series': [], 'players': [{'player': str(i), 'month_games': 3, 'month_win_rate': 50,
                                      'trend': [{'time': NOW, 'score': 1000}]} for i in range(100)],
            'total': 100, 'updated_at': updated}


def test_card_stats_publish_persist_and_fail_with_the_whole_snapshot(tmp_path):
    async def run():
        data=payload()
        data['card_stats']={'cards':[{'code':1,'users':2,'main_average':1.5,'side_average':1}],
                            'total':100,'recorded_players':90,'updated_at':1}
        cache=SeriesStats(tmp_path/'cache.json',lambda:data)
        await cache.refresh()
        previous=cache.read_cards()
        assert json.loads(previous)==data['card_stats']
        assert 'card_stats' not in json.loads(cache.read())
        restored=SeriesStats(cache.path,lambda:data)
        assert restored.read_cards()==previous and restored.has_card(1) and not restored.has_card(2)
        data['card_stats']['updated_at']=2
        with pytest.raises(ValueError): await cache.refresh()
        assert cache.read_cards()==previous
        assert json.loads(cache.read_page(1))['updated_at']==1
    asyncio.run(run())


async def until(predicate):
    for _ in range(500):
        if predicate(): return
        await asyncio.sleep(.01)
    raise AssertionError('后台任务未完成')


def test_persistence_failed_build_or_save_keeps_old_and_month_boundary(tmp_path, monkeypatch):
    async def run():
        clock = [NOW]
        cache = SeriesStats(tmp_path/'cache.json', payload, clock=lambda: clock[0])
        assert cache.read() is None
        await cache.refresh()
        original = cache.read()
        original_pages = [cache.read_page(i) for i in range(1, 11)]
        assert SeriesStats(cache.path, payload, clock=lambda: NOW).read() == original
        restored = SeriesStats(cache.path, payload, clock=lambda: NOW)
        assert [restored.read_page(i) for i in range(1, 11)] == original_pages
        def fail(): raise OSError('failed')
        cache.build = fail
        with pytest.raises(OSError): await cache.refresh()
        assert cache.read() == original
        assert [cache.read_page(i) for i in range(1, 11)] == original_pages
        cache.build = lambda: payload(2)
        with monkeypatch.context() as patch:
            patch.setattr(type(cache.path), 'replace', lambda *args: fail())
            with pytest.raises(OSError): await cache.refresh()
        assert cache.read() == original
        assert json.loads(cache.path.read_text())['payload']['updated_at'] == 1
        clock[0] = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
        expired = json.loads(cache.read())
        assert expired['players'][0]['month_games'] is None
        assert expired['players'][0]['trend'] == []
        assert expired['updated_at'] == 1
        for page in range(1, 11):
            data = json.loads(cache.read_page(page))
            assert all(row['month_games'] is None and row['trend'] == [] for row in data['players'])
            assert data['updated_at'] == 1
    asyncio.run(run())


def test_worker_retries_and_does_not_recompute_on_reads(tmp_path):
    async def run():
        attempts = []
        def build():
            attempts.append(1)
            if len(attempts) < 3: raise ValueError('not ready')
            return payload(len(attempts))
        cache = SeriesStats(tmp_path/'cache.json', build, retry_base=.01, retry_max=.02)
        cache.start(); task = cache.task; cache.start(); assert cache.task is task
        try:
            await until(lambda: cache.read() is not None)
            for _ in range(100): assert cache.read()
            await asyncio.sleep(.04)
            assert len(attempts) == 3
            cache.request_refresh()
            await until(lambda: json.loads(cache.read())['updated_at'] == 4)
        finally:
            await cache.close()
        assert task.done()
    asyncio.run(run())


def test_notifications_during_build_are_not_lost_or_overlapped(tmp_path):
    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = []
        def build():
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
            return payload(len(calls))
        cache = SeriesStats(tmp_path/'cache.json', build)
        cache.start()
        try:
            await until(entered.is_set)
            for _ in range(10): cache.request_refresh()
            assert len(calls) == 1 and cache.read() is None
            release.set()
            await until(lambda: cache.read() and json.loads(cache.read())['updated_at'] == 2)
            await asyncio.sleep(.03)
            assert len(calls) == 2
        finally:
            release.set()
            await cache.close()
    asyncio.run(run())


@pytest.mark.parametrize('content', ['broken', '{}', '{"version":2,"month":"2026-10","payload":{}}'])
def test_invalid_saved_cache_is_not_served(tmp_path, content):
    path = tmp_path/'cache.json'; path.write_text(content)
    assert SeriesStats(path, payload).read() is None


def test_pages_preserve_source_order_and_publish_with_series(tmp_path):
    async def run():
        state = payload()
        state['player_order'] = [row['player'] for row in reversed(state['players'])]
        cache = SeriesStats(tmp_path/'cache.json', lambda: state)
        await cache.refresh()
        assert 'player_order' not in json.loads(cache.read())
        assert json.loads(cache.read())['ranking_order'] == state['player_order']
        restored = SeriesStats(cache.path, lambda: state)
        assert json.loads(restored.read())['ranking_order'][:10] == state['player_order'][:10]
        assert json.loads(cache.read())['players'][0]['player'] == '0'
        assert json.loads(cache.read_page(1))['players'][0]['player'] == '99'
        assert json.loads(cache.read_page(10))['players'][-1]['player'] == '0'
        previous = [cache.read_page(i) for i in range(1, 11)]
        state['player_order'][0] = state['player_order'][1]
        with pytest.raises(ValueError): await cache.refresh()
        assert [cache.read_page(i) for i in range(1, 11)] == previous
    asyncio.run(run())
