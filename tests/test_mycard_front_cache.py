import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from hikari_bot.services.mycard_archive.front_stats import FrontStats
from hikari_bot.services.mycard_history import history_summary, month_start_point, month_trend

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()


def match(player='0', *, before=1200, after=1210, start='2026-10-01T00:00:00Z', end='2026-10-01T00:10:00Z', winner=None):
    return {'usernamea': player, 'usernameb': 'opponent', 'type': 'athletic',
            'pta_ex': before, 'pta': after, 'start_time': start, 'end_time': end,
            'winner': player if winner is None else winner}


def test_history_complete_month_zero_delta_and_score_order():
    records = [match(start='2026-10-02T00:00:00Z', end='2026-10-02T00:10:00Z', before=1210, after=1190, winner='opponent'),
               match(), match(before=1210, after=1210, start='2026-10-03T00:00:00Z', end='2026-10-03T00:01:00Z'),
               match(start='2026-09-30T15:00:00Z', end='2026-09-30T15:30:00Z')]
    result = history_summary({'total':4, 'data':records}, '0', NOW)
    assert result['month_games'] == 2 and result['month_win_rate'] == 50
    assert [p['score'] for p in result['trend']] == [1000, 1210, 1190]
    assert result['month'] == '2026-10'
    incomplete = history_summary({'total':1001, 'data':records}, '0', NOW)
    assert incomplete['month_games'] is None and incomplete['month_win_rate'] is None
    assert incomplete['trend'] == result['trend']


def test_month_boundary_and_empty_history_only_add_known_reset_point():
    row = match(start='2026-09-30T15:59:00Z', end='2026-09-30T16:01:00Z')
    assert history_summary({'total':1, 'data':[row]}, '0', NOW)['month_games'] == 0
    row = match(start='2026-09-30T16:00:00Z', end='2026-09-30T16:10:00Z')
    assert history_summary({'total':1, 'data':[row]}, '0', NOW)['month_games'] == 1
    result = history_summary({'total':0, 'data':[]}, '0', NOW)
    assert result['month_games'] == 0 and result['month_win_rate'] is None
    assert result['trend'] == [month_start_point(2026,10)]


@pytest.mark.parametrize('change', [{'pta':None}, {'pta':float('nan')}, {'type':'entertain'}, {'usernamea':'wrong'}, {'end_time':'bad'}, {'winner':None}])
def test_malformed_history_is_not_a_month_total(change):
    with pytest.raises((ValueError, TypeError)):
        history_summary({'total':1,'data':[{**match(), **change}]}, '0', NOW)


def test_duplicate_history_cannot_claim_complete_coverage():
    with pytest.raises(ValueError):
        history_summary({'total':2,'data':[match(),match()]}, '0', NOW)


def test_cache_persists_limits_concurrency_no_overlap_and_retains_old(tmp_path):
    async def run():
        calls=[]; running=0; maximum=0
        async def players(): return [str(i) for i in range(100)]
        async def info(player):
            nonlocal running,maximum
            calls.append(player);running+=1;maximum=max(maximum,running)
            await asyncio.sleep(.001);running-=1
            return {'arena_rank':int(player)+1,'pt':1300}
        async def history(player): return {'total':1,'data':[match(player)]}
        path=tmp_path/'cache.json'
        cache=FrontStats(path,leaderboard=SimpleNamespace(players=players),fetch=info,history_fetch=history,clock=lambda:NOW,request_pause=0)
        assert cache.interval == 900
        assert sorted(await asyncio.gather(cache.refresh(),cache.refresh())) == [False, True]
        assert maximum<=2 and len(calls)==100
        first=cache.snapshot(); assert first['stats']['0']['month_games']==1
        first['stats']['0']['rank']=999
        assert cache.snapshot()['stats']['0']['rank']==1
        restored=FrontStats(path,clock=lambda:NOW)
        assert restored.snapshot()==cache.snapshot()
        completed = []
        cache.on_refreshed = lambda: completed.append(cache.snapshot()['pending_count'])
        # 失败字段反复重试，成功字段不重取；中间快照仍可供页面读取。
        attempts = {'info': 0, 'history': 0}
        waits = []
        async def retry_info(player):
            if player == '0':
                attempts['info'] += 1
                if attempts['info'] < 3: raise TimeoutError()
            return {'arena_rank': int(player)+1, 'pt': 1400}
        async def retry_history(player):
            if player == '0':
                attempts['history'] += 1
                if attempts['history'] == 1: return {'total': 1001, 'data': [match(player)]}
            return {'total': 1, 'data': [match(player, after=1220)]}
        async def backoff(delay):
            waits.append(delay)
            assert completed == []
            assert await cache.refresh() is False
            assert cache.snapshot()['stats']['0']['score'] == 1300
            assert json.loads(path.read_text())['pending_count'] > 0
        cache.fetch, cache.history_fetch = retry_info, retry_history
        cache._backoff = backoff
        await cache.refresh()
        assert completed == [0]
        assert attempts == {'info': 3, 'history': 2}
        assert waits == [5, 10]
        assert cache.snapshot()['pending_count'] == 0
        assert cache.snapshot()['stats']['0']['score'] == 1400
        assert cache.snapshot()['stats']['0']['trend'][-1]['score'] == 1220
        next_month=FrontStats(path,clock=lambda:datetime(2026,11,1,tzinfo=timezone.utc).timestamp()).snapshot()
        assert next_month['stats']['0']['month_games'] is None and next_month['stats']['0']['trend']==[]
    asyncio.run(run())


def test_scheduler_repeats_and_shutdown_cancels(tmp_path):
    async def run():
        cache=FrontStats(interval=.01); count=0
        async def refresh():
            nonlocal count
            count+=1
        cache.refresh=refresh
        cache.start();task=cache.task;cache.start();assert cache.task is task
        await asyncio.sleep(1.05)
        assert count>=2
        await cache.close(); assert task.done() and cache.task is None
    asyncio.run(run())


def test_corrupt_cache_is_not_served(tmp_path):
    path=tmp_path/'cache.json';path.write_text('{bad')
    assert FrontStats(path).snapshot()['players']==[]


def test_latest_thousand_cover_month_without_lifetime_history():
    from datetime import timedelta
    end = datetime(2026, 10, 2, tzinfo=timezone.utc)
    records = []
    for i in range(1000):
        at = end + timedelta(minutes=i)
        records.append(match(start=(at-timedelta(seconds=30)).isoformat(), end=at.isoformat()))
    result = history_summary({'total': 5000, 'data': records}, '0', NOW)
    assert result['history_complete'] and result['month_games'] == 1000
    assert result['month_win_rate'] == 100 and len(result['trend']) == 1001
    truncated = history_summary({'total': 5000, 'data': records[:999]}, '0', NOW)
    assert not truncated['history_complete'] and truncated['month_games'] is None
    # 最近窗口混有上月，只计本月；北京时间跨月边界已有独立用例。
    records[0] = match(start='2026-09-30T15:00:00Z', end='2026-09-30T15:30:00Z')
    assert history_summary({'total':5000, 'data':records}, '0', NOW)['month_games'] == 999


def test_leaderboard_retries_then_failed_field_keeps_old_until_cancelled(tmp_path):
    async def run():
        list_calls = 0
        async def players():
            nonlocal list_calls
            list_calls += 1
            if list_calls < 3: raise TimeoutError()
            return [str(i) for i in range(100)]
        async def info(player): return {'arena_rank':int(player)+1, 'pt':1300}
        async def history(player): return {'total':1, 'data':[match(player)]}
        cache=FrontStats(tmp_path/'cache.json',leaderboard=SimpleNamespace(players=players),fetch=info,history_fetch=history,clock=lambda:NOW,request_pause=0)
        delays=[]
        async def no_wait(delay): delays.append(delay)
        cache._backoff=no_wait
        await cache.refresh()
        assert list_calls==3 and delays==[5,10]
        before=cache.snapshot()['stats']
        async def broken(player): raise TimeoutError()
        entered=asyncio.Event()
        async def wait_retry(delay):
            entered.set()
            await asyncio.Event().wait()
        cache.fetch=broken;cache._backoff=wait_retry
        task=asyncio.create_task(cache.refresh())
        await asyncio.wait_for(entered.wait(),2)
        assert cache.snapshot()['pending_count']==100
        assert cache.snapshot()['stats']==before
        assert await cache.refresh() is False
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not cache.lock.locked()
    asyncio.run(run())


def test_retry_delay_is_capped_and_cancellable(monkeypatch):
    delays=[]
    async def sleep(delay): delays.append(delay)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    monkeypatch.setattr('hikari_bot.services.mycard_archive.front_stats.random.uniform',lambda *_:1.2)
    async def run():
        cache=FrontStats()
        await cache._backoff(5);await cache._backoff(300)
        assert delays==[6,300]
    asyncio.run(run())


def test_retry_crossing_month_refreshes_every_history():
    async def run():
        now = NOW
        calls = {'info':0, 'history':0}
        async def players(): return [str(i) for i in range(100)]
        async def info(player):
            calls['info'] += 1
            return {'arena_rank':int(player)+1, 'pt':1300}
        async def history(player):
            calls['history'] += 1
            if now==NOW and player=='0': raise TimeoutError()
            return {'total':1, 'data':[match(player)]}
        async def previous(player,year,month): return 1400
        cache=FrontStats(leaderboard=SimpleNamespace(players=players),fetch=info,history_fetch=history,previous_fetch=previous,clock=lambda:now,request_pause=0)
        async def next_month(delay):
            nonlocal now
            now=datetime(2026,11,1,tzinfo=timezone.utc).timestamp()
        cache._backoff=next_month
        await cache.refresh()
        assert calls=={'info':100,'history':200}
        assert all(row['month']=='2026-11' and row['month_games']==0 and row['trend']==[month_start_point(2026,11,1400)] for row in cache.snapshot()['stats'].values())
    asyncio.run(run())


def test_restart_prioritizes_pending_cache():
    async def run():
        cache=FrontStats(clock=lambda:NOW)
        cache.state.update(updated_at=NOW,pending_count=1)
        refreshed=asyncio.Event();delays=[]
        async def backoff(delay): delays.append(delay)
        async def refresh(): refreshed.set()
        cache._backoff=backoff;cache.refresh=refresh
        cache.start()
        await asyncio.wait_for(refreshed.wait(),1)
        await cache.close()
        assert delays==[5]
    asyncio.run(run())


@pytest.mark.parametrize('start,durations,expected', [
    # 已有缓存的更新时间不影响整刻；采集耗时不会让下一轮漂移。
    (7*60+12.5,[83,24,12],[900,1800,2700]),
    # 跨过一个整刻的长轮次不补跑、不重叠，回到下一整刻。
    (7*60,[901,90,12],[900,2700,3600]),
    (59*60+59.5,[83,24,12],[3600,4500,5400]),
    (23*3600+59*60+59,[83,24,12],[86400,87300,88200]),
])
def test_scheduler_starts_on_wall_clock_quarters(monkeypatch,start,durations,expected):
    async def run():
        now=NOW+start;started=[]
        async def sleep(delay):
            nonlocal now
            assert 0 < delay <= 60
            now+=delay
        monkeypatch.setattr(asyncio,'sleep',sleep)
        cache=FrontStats(clock=lambda:now)
        cache.state.update(updated_at=NOW+123,pending_count=0)
        async def refresh():
            nonlocal now
            started.append(now-NOW)
            if len(started)==3: raise asyncio.CancelledError()
            now+=durations[len(started)-1]
        cache.refresh=refresh
        with pytest.raises(asyncio.CancelledError):await cache._run()
        assert started==expected
    asyncio.run(run())


def test_empty_cache_refreshes_once_then_aligns(monkeypatch):
    async def run():
        now=NOW+123;started=[]
        async def sleep(delay):
            nonlocal now
            now+=delay
        monkeypatch.setattr(asyncio,'sleep',sleep)
        cache=FrontStats(clock=lambda:now)
        async def refresh():
            started.append(now-NOW)
            if len(started)==2:raise asyncio.CancelledError()
        cache.refresh=refresh
        with pytest.raises(asyncio.CancelledError):await cache._run()
        assert started==[123,900]
    asyncio.run(run())


def test_quarter_wait_rechecks_wall_clock_after_adjustment(monkeypatch):
    async def run():
        now=NOW+850;delays=[]
        async def sleep(delay):
            nonlocal now
            delays.append(delay)
            now = NOW+840 if len(delays)==1 else now+delay
        monkeypatch.setattr(asyncio,'sleep',sleep)
        cache=FrontStats(clock=lambda:now)
        await cache._wait_next_slot()
        assert now==NOW+900 and delays==[50,60]
    asyncio.run(run())


def test_ozxdno_reset_crossing_match_does_not_enter_october_trend():
    # 实际边界：北京时间 9/30 23:43 开局，10/1 00:01 结束，记录仍为重置前 PT。
    cross = match('ozxdno', before=1495.34493367106, after=1503.34493367106,
                  start='2026-09-30T15:43:10.000Z', end='2026-09-30T16:01:21.000Z')
    october = match('ozxdno', before=1008, after=1025.07623620852,
                    start='2026-10-02T03:36:38.000Z', end='2026-10-02T03:52:27.000Z')
    result = history_summary({'total':2, 'data':[october,cross]}, 'ozxdno', NOW)
    assert [p['score'] for p in result['trend']] == [1000,1025.07623620852]
    assert result['trend'][0]['time'] == datetime(2026,9,30,16,tzinfo=timezone.utc).timestamp()
    assert result['month_games'] == 1
    from hikari_bot.services.mycard import is_specific_month
    assert not is_specific_month(cross,10,2026)
    assert is_specific_month(cross,9,2026)
    assert is_specific_month(october,10,2026)


@pytest.mark.parametrize('start,end,year,month,expected', [
    ('2026-09-30T15:59:00Z','2026-09-30T16:01:00Z',2026,10,False),
    ('2026-09-30T16:00:00Z','2026-09-30T16:10:00Z',2026,10,True),
    ('2026-12-31T15:59:00Z','2026-12-31T16:01:00Z',2027,1,False),
    ('2026-12-31T16:00:00Z','2026-12-31T16:10:00Z',2027,1,True),
    ('2026-10-01T00:00:00+08:00','2026-10-01T00:10:00+08:00',2026,10,True),
])
def test_bot_history_query_uses_beijing_start_month(monkeypatch,start,end,year,month,expected):
    from hikari_bot.services import mycard
    row=match('ozxdno',start=start,end=end)
    async def fetch(player): return [row]
    monkeypatch.setattr(mycard,'fetch_player_history',fetch)
    assert asyncio.run(mycard.mycard_get_records('ozxdno',month,year)) == ([row] if expected else [])


@pytest.mark.parametrize('month', [1,4,7,10])
@pytest.mark.parametrize('previous', [None,800,1000,1600])
def test_quarter_reset_always_starts_at_1000_at_beijing_midnight(month,previous):
    from zoneinfo import ZoneInfo
    point = month_start_point(2026,month,previous)
    assert point == {'time':datetime(2026,month,1,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp(),'score':1000}


@pytest.mark.parametrize('previous,expected', [(800,800),(1000,1000),(1400,1200),(1427.63981934622,1213.81990967311)])
def test_nonquarter_reset_only_halves_excess_and_ignores_first_pre_score(previous,expected):
    records = [match(before=9999,after=1234,start='2026-11-02T00:00:00Z',end='2026-11-02T00:10:00Z')]
    # Bot 与网页通过同一个函数构造点；不会把首场赛前分数插在月初与赛后之间。
    expected_points = [{'time':datetime(2026,10,31,16,tzinfo=timezone.utc).timestamp(),'score':expected},
                       {'time':datetime(2026,11,2,0,10,tzinfo=timezone.utc).timestamp(),'score':1234}]
    assert month_trend(records,'0',2026,11,previous) == expected_points
    now = datetime(2026,11,15,tzinfo=timezone.utc).timestamp()
    assert history_summary({'total':1,'data':records},'0',now,previous)['trend'] == expected_points


@pytest.mark.parametrize('previous', [None,True,float('nan'),float('inf'),'1400'])
def test_unknown_nonquarter_reset_does_not_invent_1000(previous):
    with pytest.raises(ValueError): month_start_point(2026,11,previous)


def test_previous_month_score_reads_snapshot_not_crossing_match_or_correction(monkeypatch):
    from hikari_bot.services import mycard
    calls=[]
    async def get(path,params):
        calls.append((path,params))
        return {'username':'ozxdno','season':'2026-08','pt':1427.63981934622,'correction':-213.819909673108}
    monkeypatch.setattr(mycard,'_api_get',get)
    assert asyncio.run(mycard.fetch_previous_month_score('ozxdno',2026,9)) == 1427.63981934622
    assert calls == [('arena/historyScore',{'username':'ozxdno','season':'2026-08'})]
    assert asyncio.run(mycard.fetch_previous_month_score('ozxdno',2027,1)) is None
    assert len(calls) == 1


@pytest.mark.parametrize('response', [None,'',{}, {'username':'wrong','season':'2026-10','pt':1400},
    {'username':'0','season':'2026-09','pt':1400},{'username':'0','season':'2026-10','pt':None}])
def test_invalid_previous_snapshot_is_retryable(monkeypatch,response):
    from hikari_bot.services import mycard
    async def get(path,params): return response
    monkeypatch.setattr(mycard,'_api_get',get)
    with pytest.raises(ValueError): asyncio.run(mycard.fetch_previous_month_score('0',2026,11))


def test_nonquarter_snapshot_failure_keeps_old_trend_and_retries(tmp_path):
    async def run():
        now=datetime(2026,11,15,tzinfo=timezone.utc).timestamp()
        async def players(): return [str(i) for i in range(100)]
        async def info(player): return {'arena_rank':int(player)+1,'pt':1300}
        async def history(player): return {'total':1,'data':[match(player,start='2026-11-02T00:00:00Z',end='2026-11-02T00:10:00Z')]}
        async def previous(player,year,month): return 1400
        cache=FrontStats(tmp_path/'cache.json',leaderboard=SimpleNamespace(players=players),fetch=info,
                         history_fetch=history,previous_fetch=previous,clock=lambda:now,request_pause=0)
        await cache.refresh()
        old=cache.snapshot()['stats']['0']['trend']
        calls={}
        async def flaky(player,year,month):
            calls[player]=calls.get(player,0)+1
            if player=='0' and calls[player]==1: raise TimeoutError()
            return 1600
        async def backoff(delay):
            assert cache.snapshot()['pending_count']==1
            assert cache.snapshot()['stats']['0']['trend']==old
            assert await cache.refresh() is False
        cache.previous_fetch=flaky;cache._backoff=backoff
        await cache.refresh()
        assert cache.snapshot()['pending_count']==0
        assert cache.snapshot()['stats']['0']['trend'][0]['score']==1300
        assert calls['0']==2 and sum(calls.values())==101
    asyncio.run(run())
