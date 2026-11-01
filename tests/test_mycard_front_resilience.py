"""单个玩家持续失败及官方重复历史记录的回归验证。"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from hikari_bot.services import mycard
from hikari_bot.services.mycard_archive.front_stats import FrontStats
from hikari_bot.services.mycard_archive.series_stats import SeriesStats
from hikari_bot.services.mycard_history import history_summary


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()


def history_rows(count):
    start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    return [{'usernamea': '0', 'usernameb': 'opponent', 'type': 'athletic',
             'start_time': (start - timedelta(minutes=i)).isoformat(),
             'end_time': (start - timedelta(minutes=i) + timedelta(seconds=30)).isoformat(),
             'pta_ex': 1200, 'pta': 1210, 'winner': '0'} for i in range(count)]


def test_duplicate_source_row_refills_the_actual_thousand_record_window(monkeypatch):
    records = history_rows(1000)
    raw = records[:476] + [records[475]] + records[476:]
    calls = []

    async def get(path, params):
        calls.append((path, params.copy()))
        # 官方 getMany 合并同一场重复记录，SQL LIMIT 和 total 仍计入重复行。
        unique = {row['start_time']: row for row in raw[:params['page_num']]}
        return {'total': 5892, 'data': list(unique.values())}

    monkeypatch.setattr(mycard, '_api_get', get)
    payload = asyncio.run(mycard.fetch_player_history_page('0'))
    assert len(payload['data']) == 1000
    assert [params['page_num'] for _, params in calls] == [1000, 1001]
    summary = history_summary(payload, '0', NOW, 1400)
    assert summary['history_complete'] and summary['month_games'] == 0


def test_duplicate_in_short_lifetime_history_counts_unique_matches(monkeypatch):
    records = history_rows(2)

    async def get(path, params):
        return {'total': 3, 'data': records}

    monkeypatch.setattr(mycard, '_api_get', get)
    payload = asyncio.run(mycard.fetch_player_history_page('0'))
    assert payload['total'] == 2 and payload['source_total'] == 3
    assert history_summary(payload, '0', NOW, 1400)['history_complete']


def test_normal_history_does_not_add_requests(monkeypatch):
    calls = []
    records = history_rows(3)

    async def get(path, params):
        calls.append(params)
        return {'total': 3, 'data': records}

    monkeypatch.setattr(mycard, '_api_get', get)
    assert asyncio.run(mycard.fetch_player_history_page('0')) == {'total': 3, 'data': records}
    assert len(calls) == 1


def test_refill_is_bounded_and_does_not_claim_truncated_history_complete(monkeypatch):
    calls = []

    async def get(path, params):
        calls.append(params['page_num'])
        return {'total': 5892, 'data': history_rows(1)}

    monkeypatch.setattr(mycard, '_api_get', get)
    with pytest.raises(ValueError):
        asyncio.run(mycard.fetch_player_history_page('0'))
    assert len(calls) <= 3 and max(calls) <= 2000


@pytest.mark.parametrize('failure', ['info', 'history', 'unexpected'])
def test_one_failure_publishes_successes_and_does_not_pin_next_leaderboard(tmp_path, failure):
    async def run():
        players = [str(i) for i in range(100)]
        list_calls = 0
        field_calls = 0
        published = []

        async def leaderboard():
            nonlocal list_calls
            list_calls += 1
            return list(players)

        async def info(player):
            nonlocal field_calls
            if player == '0' and failure in ('info', 'unexpected'):
                field_calls += 1
                if failure == 'unexpected':
                    raise RuntimeError('异常玩家响应')
                raise TimeoutError('资料不可用')
            return {'arena_rank': int(player) + 1, 'pt': 1400}

        async def history(player):
            nonlocal field_calls
            if player == '0' and failure == 'history':
                field_calls += 1
                return {'total': 5892, 'data': history_rows(999)}
            return {'total': 0, 'data': []}

        async def previous(*args):
            return 1400

        cache = FrontStats(tmp_path / 'front.json', leaderboard=SimpleNamespace(players=leaderboard),
                           fetch=info, history_fetch=history, previous_fetch=previous,
                           request_pause=0, clock=lambda: NOW)

        def build():
            snapshot = cache.snapshot()
            return {'series': [], 'players': [dict(snapshot['stats'][p], player=p) for p in snapshot['players']],
                    'total': 100, 'updated_at': snapshot['updated_at']}

        series = SeriesStats(tmp_path / 'series.json', build, clock=lambda: NOW)

        def on_refreshed():
            published.append(cache.snapshot()['pending_count'])
            series.request_refresh()

        cache.on_refreshed = on_refreshed

        async def backoff(delay):
            # 真实通知边界：重试等待期间，前排页已有成功玩家的新资料。
            assert published and series.event.is_set()
            series.event.clear()
            await series.refresh()
            page = json.loads(series.read_page(1))
            assert page['players'][1]['score'] == 1400
            assert await cache.refresh() is False

        cache._backoff = backoff
        assert await asyncio.wait_for(cache.refresh(), 2)
        assert field_calls == 3 and published == [1, 1, 1]
        assert cache.snapshot()['pending_count'] == 1
        assert not cache.lock.locked()
        # 下一轮重新读取榜单和已成功的字段，不能永远重试旧名单。
        players[0] = '100'
        assert await asyncio.wait_for(cache.refresh(), 2)
        assert list_calls == 2 and cache.snapshot()['players'][0] == '100'
        assert cache.snapshot()['pending_count'] == 0

    asyncio.run(run())
