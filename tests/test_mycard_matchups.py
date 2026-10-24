import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from hikari_bot.services.mycard_archive.matchups import build_matchups, compact_history, results, timestamp
from hikari_bot.services.mycard_archive.front_stats import FrontStats
from hikari_bot.services.mycard_archive.series_stats import SeriesStats


NOW = datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()
START = '2026-10-15T02:00:00Z'
END = '2026-10-15T02:30:00Z'


def record(**changes):
    return {'usernamea': 'a', 'usernameb': 'b', 'start_time': START, 'end_time': END,
            'winner': 'a', 'userscorea': 2, 'userscoreb': 1, **changes}


def snapshot(*records):
    return {'players': ['a', 'b'], 'updated_at': NOW,
            'stats': {name: {'month': '2026-10', '_matches': deepcopy(list(records))} for name in ('a', 'b')}}


def archive(path, *, key='one', labels=(('A',), ('B',)), games=3, status='complete', start=None, end=None):
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE IF NOT EXISTS matches(id,players,status,finished); '
                        'CREATE TABLE IF NOT EXISTS replays(match_id,ordinal,metadata);')
        db.execute('INSERT INTO matches VALUES(?,?,?,?)', (key, '["a","b"]', status, timestamp(end or END)))
        for index in range(games):
            replay = {'start_time': int(timestamp(start or START)) + 3 + index * 300,
                      'decks': [{'player': name, 'main': [index], 'extra': [], 'labels': list(tags)}
                                for name, tags in zip(('a', 'b'), labels)]}
            db.execute('INSERT INTO replays VALUES(?,?,?)', (key, index + 1, json.dumps(replay)))


def build(state, path):
    return build_matchups(state, path, lambda deck: deck['labels'], {'A': 123})


def test_reuses_current_month_top100_results_and_deduplicates_both_histories(tmp_path):
    path = tmp_path/'archive.db'
    archive(path, labels=(('A', 'A', 'C'), ('B',)))
    raw = record(type='athletic')
    extracted = compact_history([raw, {**raw, 'usernameb': 'outside'},
                                 {**raw, 'start_time': '2026-09-30T15:59:00Z'},
                                 {**raw, 'type': 'entertain'}], ['a', 'b'], '2026-10')
    assert extracted == [record()]
    data = build(snapshot(*extracted), path)
    assert data['effective_matches'] == 1
    assert [(r['a'], r['b'], r['games']) for r in data['pairs']] == [('A','B',1),('B','C',1)]
    assert data['pairs'][0]['rate_a'] == 100 and data['pairs'][0]['rate_b'] == 0
    assert data['pairs'][1]['wins_b'] == 1
    assert data['pairs'][0]['image_a'] == '/mc/api/art/123'
    assert data['pairs'][0]['image_b'] is None
    totals = {row['label']: row for row in data['series_totals']}
    assert totals['B']['games'] == 1 and totals['B']['losses'] == 1
    assert totals['A']['wins'] == totals['C']['wins'] == 1


@pytest.mark.parametrize('change', [
    {'winner': 'draw', 'userscorea': 1, 'userscoreb': 1},
    {'winner': 'unknown'}, {'winner': 'b'}, {'userscoreb': -9}, {'userscorea': None},
    {'usernameb': 'outside'}, {'start_time': '2026-09-30T15:59:59Z'},
    {'end_time': '2026-12-01T00:00:00Z'}, {'start_time': '2026-10-15T02:00:00'},
])
def test_ignores_draws_invalid_results_outside_players_and_month(change):
    assert results(snapshot(record(**change))) == []


def test_beijing_month_and_no_pt_heuristic():
    row = record(start_time='2026-09-30T16:00:00Z')
    assert len(results(snapshot(row))) == 1
    assert compact_history([{**row, 'type':'athletic', 'pta':1000,'pta_ex':1000}], ['a','b'],'2026-10') == [row]


def test_conflicts_reversed_sides_and_missing_old_cache():
    state = snapshot(record())
    state['stats']['b']['_matches'] = [record(usernamea='b',usernameb='a',userscorea=1,userscoreb=2)]
    assert len(results(state)) == 1
    state['stats']['b']['_matches'] = [record(winner='b',userscorea=1,userscoreb=2)]
    assert results(state) == []
    del state['stats']['a']['_matches']
    assert results(state) is None


@pytest.mark.parametrize('labels', [(('A',),('A',)), (('A','B'),('A','B')), ((),('B',))])
def test_mirrors_shared_pair_ambiguity_and_unclassified_not_in_denominator(tmp_path, labels):
    path=tmp_path/'archive.db';archive(path,labels=labels)
    data=build(snapshot(record()),path)
    assert data['pairs']==[] and data['effective_matches']==0
    assert data['series_totals'] == []


def test_shared_series_only_excludes_conflicting_pair(tmp_path):
    path=tmp_path/'archive.db';archive(path,labels=(('A','B'),('A','B','C')))
    data=build(snapshot(record()),path)
    assert {(r['a'],r['b']) for r in data['pairs']}=={('A','C'),('B','C')}
    assert data['effective_matches']==1
    # 共有 A、B 属于内战，均胜率只计唯一归属的 C，且不能因两个对手标签翻倍。
    assert data['series_totals'] == [{'label':'C','wins':0,'losses':1,'games':1,'rate':0}]


def test_overall_rate_counts_each_match_once_across_all_opponent_series(tmp_path):
    path=tmp_path/'archive.db'
    archive(path,labels=(('A',),('B','C','D')))
    later=record(start_time='2026-10-15T03:00:00Z',end_time='2026-10-15T03:30:00Z',winner='b',userscorea=1,userscoreb=2)
    archive(path,key='two',labels=(('A',),('OUTSIDE_DISPLAY',)),start=later['start_time'],end=later['end_time'])
    third=record(start_time='2026-10-15T04:00:00Z',end_time='2026-10-15T04:30:00Z',winner='b',userscorea=1,userscoreb=2)
    archive(path,key='three',labels=(('A',),('B',)),start=third['start_time'],end=third['end_time'])
    data=build(snapshot(record(),later,third),path)
    total=next(row for row in data['series_totals'] if row['label']=='A')
    assert total == {'label':'A','wins':1,'losses':2,'games':3,'rate':pytest.approx(100/3)}
    assert sum(row['games'] for row in data['pairs'] if row['a']=='A') == 5


@pytest.mark.parametrize('mode', ['missing_replay','duplicate_archive','partial','wrong_time'])
def test_incomplete_or_nonunique_archive_not_guessed(tmp_path,mode):
    path=tmp_path/'archive.db'
    archive(path,games=2 if mode=='missing_replay' else 3,
            status='partial' if mode=='partial' else 'complete',
            start='2026-10-15T03:00:00Z' if mode=='wrong_time' else START)
    if mode=='duplicate_archive':archive(path,key='duplicate')
    assert build(snapshot(record()),path)['effective_matches']==0


def test_one_archive_cannot_match_two_official_results(tmp_path):
    path=tmp_path/'archive.db';archive(path)
    data=build(snapshot(record(),record(end_time='2026-10-15T02:31:00Z')),path)
    assert data['effective_matches']==0


def test_repeated_opponents_are_distinct_and_use_each_matches_first_deck(tmp_path):
    path=tmp_path/'archive.db';archive(path)
    later=record(start_time='2026-10-15T03:00:00Z',end_time='2026-10-15T03:30:00Z',winner='b',userscorea=1,userscoreb=2)
    archive(path,key='two',start=later['start_time'],end=later['end_time'])
    data=build(snapshot(record(),later),path)
    assert data['effective_matches']==2 and data['pairs'][0]['games']==2
    assert data['pairs'][0]['rate_a']==50
    with sqlite3.connect(path) as db:
        row=json.loads(db.execute("SELECT metadata FROM replays WHERE match_id='one' AND ordinal=2").fetchone()[0])
        row['decks'][0]['labels']=['LATER_SIDE']
        db.execute("UPDATE replays SET metadata=? WHERE match_id='one' AND ordinal=2",(json.dumps(row),))
    assert build(snapshot(record(),later),path)==data


def test_front_refresh_saves_bounded_results_without_extra_upstream_requests(tmp_path):
    async def run():
        names=['a','b']+[str(i) for i in range(98)]
        calls=[]
        async def players():return names
        async def info(player):return {'arena_rank':names.index(player)+1,'pt':1200}
        async def history(player):
            calls.append(player)
            rows=[{**record(), 'type':'athletic','pta_ex':1100,'pta':1110,'ptb_ex':1100,'ptb':1090}] if player in ('a','b') else []
            return {'total':len(rows),'data':rows}
        cache=FrontStats(tmp_path/'cache.json',leaderboard=SimpleNamespace(players=players),fetch=info,history_fetch=history,clock=lambda:NOW,request_pause=0)
        await cache.refresh()
        assert len(calls)==100 and cache.state['stats']['a']['_matches']==[record()]
        assert FrontStats(cache.path).state['stats']['b']['_matches']==[record()]
    asyncio.run(run())


def test_preencoded_cache_restart_failure_and_month_rollover(tmp_path):
    async def run():
        clock=[NOW]
        match={'month':'2026-10','updated_at':NOW,'pairs':[],'effective_matches':0,'total':100}
        payload={'players':[{'player':str(i)} for i in range(100)],'series':[],'total':100,'updated_at':NOW,'match_stats':match}
        cache=SeriesStats(tmp_path/'cache.json',lambda:payload,clock=lambda:clock[0])
        await cache.refresh(); previous=cache.read_matchups()
        assert json.loads(previous)==match and 'match_stats' not in json.loads(cache.read())
        assert SeriesStats(cache.path,lambda:payload,clock=lambda:NOW).read_matchups()==previous
        match['updated_at']=NOW+1
        with pytest.raises(ValueError):await cache.refresh()
        assert cache.read_matchups()==previous
        clock[0]=datetime(2026,11,1,tzinfo=timezone.utc).timestamp()
        assert cache.read_matchups() is None
    asyncio.run(run())


def test_pending_round_does_not_recompute_matchups(tmp_path):
    state=snapshot(record());state['pending_count']=1
    assert build(state,tmp_path/'missing.db') is None


def test_route_only_reads_bytes_and_private_history_stays_private(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    spec=importlib.util.spec_from_file_location('mc_match_test',Path('hikari_bot/plugins/web/routes/mycard.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    content=b'{"pairs":[],"effective_matches":0}'
    monkeypatch.setattr(module,'series_stats',SimpleNamespace(read_matchups=lambda:content))
    app=FastAPI();app.include_router(module.router)
    with TestClient(app) as client:
        for _ in range(3):
            response=client.get('/mc/api/match-stats')
            assert response.content==content and response.headers['cache-control']=='no-store'
        module.series_stats.read_matchups=lambda:None
        assert client.get('/mc/api/match-stats').status_code==503
    (tmp_path/'deck-rules.json').write_text('{"version":1,"rules":[]}',encoding='utf-8')
    monkeypatch.setattr(module,'settings',SimpleNamespace(mycard_archive_dir=tmp_path))
    monkeypatch.setattr(module,'effective_representatives',lambda data:{})
    row=module.front_rows(['a'],{'a':{'rank':1,'_matches':[record()]}},latest={})[0]
    assert '_matches' not in row and row['rank']==1
