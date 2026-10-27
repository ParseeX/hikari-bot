import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hikari_bot.services.mycard_archive.queries import Leaderboard
from hikari_bot.services.mycard_archive.front_stats import FrontStats
from hikari_bot.services.mycard_archive.store import ArchiveStore
from test_mycard_decks import record
from test_mycard_archive import decode_ourygo


async def refresh_until_retry(cache):
    # 路由测试读取失败后的部分成功快照；重试行为由缓存测试单独覆盖。
    async def stop(delay): raise asyncio.CancelledError()
    cache._backoff = stop
    with pytest.raises(asyncio.CancelledError):
        await cache.refresh()


def fake_catalog(by_id):
    return SimpleNamespace(by_id=by_id, cards_by_id=lambda codes: {code: by_id(code) for code in codes})


@pytest.fixture
def web(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('mc_front_test', Path('hikari_bot/plugins/web/routes/mycard.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'settings', SimpleNamespace(mycard_archive_dir=tmp_path))
    monkeypatch.setattr(module, 'catalog', lambda: fake_catalog(lambda code: {'id': code}))
    async def fetch():
        return [{'username': '甲' if i == 0 else f'玩家{i+1}'} for i in range(100)]
    board = Leaderboard(fetch=fetch)
    async def fetch_stats(player):
        rank = 1 if player == '甲' else int(player.removeprefix('玩家'))
        return {'arena_rank': rank, 'pt': 1500 - rank, 'athletic_all': 500,
                'athletic_wl_ratio': '60.00'}
    async def history(player): raise TimeoutError()
    module.front_stats = FrontStats(leaderboard=board, fetch=fetch_stats, history_fetch=history, request_pause=0)
    module.series_stats = module.SeriesStats(tmp_path/'series.json', module.build_deck_stats)
    asyncio.run(refresh_until_retry(module.front_stats))
    store = ArchiveStore(tmp_path, set())
    record(store, 'old', 100, main=(99,), side=(88,))
    record(store, 'latest', 200, main=(1, 1, 2), side=(3,))
    (tmp_path/'deck-rules.json').write_text(json.dumps({'version': 1, 'rules': [
        {'label': '甲分类', 'cards': [1]}, {'label': '乙分类', 'cards': [2]},
        {'label': '甲分类', 'cards': [1, 2]}], 'representatives': {'甲分类': 123}}), encoding='utf-8')
    asyncio.run(module.series_stats.refresh())
    app = FastAPI(); app.include_router(module.router)
    with TestClient(app) as client:
        yield module, client, tmp_path


def test_art_subjects_are_public_read_only_and_immediate(web):
    module, client, root = web
    path = root/'deck-rules.json'
    before = path.read_bytes()
    assert client.get('/mc/api/art-subjects').json() == {'subjects': {}}
    assert path.read_bytes() == before
    data = json.loads(before)
    data['art_subjects'] = {'123': {'x': .2, 'y': .1, 'width': .6, 'height': .7}}
    path.write_text(json.dumps(data), encoding='utf-8')
    saved = path.read_bytes()
    response = client.get('/mc/api/art-subjects')
    assert response.json() == {'subjects': data['art_subjects']}
    assert response.headers['cache-control'] == 'no-store'
    assert path.read_bytes() == saved


def test_pages_multiple_labels_and_exact_latest_deck(web):
    module, client, root = web
    before = (root/'archive.sqlite3').read_bytes()
    first = client.get('/mc/api/leaderboard').json()
    assert first['page_size'] == 10 and first['total'] == 100
    assert len(first['players']) == 10
    row = first['players'][0]
    assert row['player'] == '甲' and row['rank'] == 1
    assert row['score'] == 1499
    assert row['deck']['name'] == '乙分类甲分类'
    assert row['month_games'] is None and row['month_win_rate'] is None
    assert row['deck']['categories'] == [{'label': '乙分类', 'image': '/mc/api/art/2'},
                                        {'label': '甲分类', 'image': '/mc/api/art/123'}]
    main, extra, side = decode_ourygo(row['deck']['links'][0])
    assert main == {1: 2, 2: 1} and side == {3: 1}
    assert first['players'][1]['deck'] is None
    second = client.get('/mc/api/leaderboard?page=2').json()
    assert second['players'][0]['rank'] == 11
    last = client.get('/mc/api/leaderboard?page=10').json()
    assert [r['rank'] for r in last['players']] == list(range(91, 101))
    for page in ('0', '11', 'abc', '1.5'):
        assert client.get('/mc/api/leaderboard?page=' + page).status_code == 422
    assert (root/'archive.sqlite3').read_bytes() == before
    assert not (root/'short-links.sqlite3').exists()


def test_rank_is_official_and_individual_failure_keeps_decks(web):
    module, client, root = web
    async def fetch_stats(player):
        if player == '甲':
            return {'arena_rank': '7', 'pt': 1432}
        raise TimeoutError()
    module.front_stats.fetch = fetch_stats
    module.front_stats.semaphore = asyncio.Semaphore(2)
    asyncio.run(refresh_until_retry(module.front_stats))
    asyncio.run(module.series_stats.refresh())
    rows = client.get('/mc/api/leaderboard').json()['players']
    assert rows[0]['rank'] == 7 and rows[0]['deck']
    assert rows[1]['rank'] == 2 and rows[1]['score'] == 1498  # 请求失败保留旧值


def test_unknown_classification_empty_mapping_and_art_failure(web, monkeypatch):
    module, client, root = web
    async def failed(code): return None
    monkeypatch.setattr(module, 'card_art', failed)
    assert client.get('/mc/api/art/123').status_code == 404
    assert client.get('/mc/api/art/999').status_code == 404
    async def image(code): return b'test-image'
    monkeypatch.setattr(module, 'card_art', image)
    assert client.get('/mc/api/art/123').headers['content-type'] == 'image/jpeg'
    assert client.get('/mc/api/art/2').status_code == 200
    (root/'deck-rules.json').write_text('{"version":1,"rules":[]}')
    asyncio.run(module.series_stats.refresh())
    assert client.get('/mc/api/leaderboard').json()['players'][0]['deck']['categories'] == []


def test_default_uses_category_order_and_invalid_category_has_no_image(web, monkeypatch):
    module, client, root = web
    data = {'version': 1, 'rules': [{'label': '分类', 'cards': [999, 9, 1]},
                                  {'label': '分类', 'cards': [2]},
                                  {'label': '失效分类', 'cards': [3]}]}
    path = root/'deck-rules.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(module, 'catalog', lambda: fake_catalog(
        lambda code: {'id': code} if code in (9, 1, 2) else None))
    before = path.read_bytes()
    asyncio.run(module.series_stats.refresh())
    row = client.get('/mc/api/leaderboard').json()['players'][0]
    # 9 不在玩家构筑中，仍按分类的首张有效卡取图，不能改取玩家持有的 1。
    assert row['deck']['categories'] == [{'label': '分类', 'image': '/mc/api/art/9'}]
    assert path.read_bytes() == before and 'representatives' not in json.loads(before)
    data['rules'][0]['cards'] = [999]
    data['rules'][1]['cards'] = [999, 1]
    path.write_text(json.dumps(data), encoding='utf-8')
    assert module.effective_representatives(data) == {'分类': 1}
    monkeypatch.setattr(module, 'catalog', lambda: fake_catalog(lambda code: None))
    asyncio.run(module.series_stats.refresh())
    row = client.get('/mc/api/leaderboard').json()['players'][0]
    assert row['deck']['categories'] == [{'label': '分类', 'image': None}]


def test_rank_failure_and_corrupt_rules_fail_closed(web, monkeypatch):
    module, client, root = web
    previous = client.get('/mc/api/leaderboard').content
    (root/'deck-rules.json').write_text('invalid')
    with pytest.raises(ValueError):
        asyncio.run(module.series_stats.refresh())
    assert client.get('/mc/api/leaderboard').content == previous
    module.front_stats.state['players'] = []
    module.series_stats = module.SeriesStats(root/'missing.json', module.build_deck_stats)
    response = client.get('/mc/api/leaderboard')
    assert response.status_code == 503 and 'private' not in response.text


def test_page_reads_snapshot_without_upstream_requests(web, monkeypatch):
    module, client, root = web
    async def forbidden(*args): raise AssertionError('访客不应请求上游')
    module.front_stats.fetch = forbidden
    module.front_stats.history_fetch = forbidden
    module.front_stats.leaderboard.players = forbidden
    def no_work(*args): raise AssertionError('翻页不应查库或计算')
    monkeypatch.setattr(module.front_stats, 'snapshot', no_work)
    monkeypatch.setattr(module, 'front_rows', no_work)
    monkeypatch.setattr(module.series_stats, 'build', no_work)
    all_players = []
    for page in range(1, 11):
        response = client.get(f'/mc/api/leaderboard?page={page}')
        assert response.status_code == 200
        data = response.json()
        assert data['page'] == page and data['page_size'] == len(data['players']) == 10
        all_players.extend(row['player'] for row in data['players'])
    assert len(set(all_players)) == 100
    assert not module.series_stats.event.is_set()


def test_series_counts_current_deck_once_and_excludes_outside_top100(web):
    module, client, root = web
    before = (root/'archive.sqlite3').read_bytes()
    asyncio.run(module.series_stats.refresh())
    result = client.get('/mc/api/deck-stats').json()
    assert result['total'] == 100 and len(result['players']) == 100
    # 甲的两条同名规则、多张卡、历史卡组均不能累计次数；乙有归档但不在榜单内。
    assert {item['label']:item['count'] for item in result['series']} == {'甲分类':1,'乙分类':1}
    assert '乙' not in {row['player'] for row in result['players']}
    assert result['players'][0]['deck']['name'] == '乙分类甲分类'
    assert result['updated_at'] == module.front_stats.snapshot()['updated_at']
    assert (root/'archive.sqlite3').read_bytes() == before


def test_series_sorted_by_count_and_players_by_official_rank(web,monkeypatch):
    from hikari_bot.services.mycard_archive.replay import Deck
    module,client,root = web
    versions = {
        '甲':SimpleNamespace(deck=Deck('甲',(1,1,2),(),()),updated_at=200),
        '玩家2':SimpleNamespace(deck=Deck('玩家2',(1,),(),()),updated_at=200),
        '玩家3':SimpleNamespace(deck=Deck('玩家3',(99,),(),()),updated_at=200),
        '榜外玩家':SimpleNamespace(deck=Deck('榜外玩家',(2,),(),()),updated_at=200),
    }
    calls=[]
    def latest(self,players):calls.append(list(players));return versions
    monkeypatch.setattr(module.DeckLibrary,'latest_many',latest)
    module.front_stats.state['stats']['甲']['rank']=8
    module.front_stats.state['stats']['玩家2']['rank']=1
    async def forbidden(*args):raise AssertionError('访客不应请求上游')
    module.front_stats.fetch=forbidden;module.front_stats.history_fetch=forbidden
    module.front_stats.leaderboard.players=forbidden
    asyncio.run(module.series_stats.refresh())
    response=client.get('/mc/api/deck-stats');assert response.status_code==200
    result=response.json()
    assert len(calls)==1 and len(calls[0])==100 and '榜外玩家' not in calls[0]
    assert result['series']==[
        {'label':'甲分类','count':2,'image':'/mc/api/art/123','highest_rank':1,'partners':[{'label':'乙分类','count':1}]},
        {'label':'乙分类','count':1,'image':'/mc/api/art/2','highest_rank':8,'partners':[{'label':'甲分类','count':1}]}]
    members=[row for row in result['players'] if row['deck'] and any(c['label']=='甲分类' for c in row['deck']['categories'])]
    assert [row['player'] for row in members]==['玩家2','甲']
    assert [row['rank'] for row in result['players']]==sorted(row['rank'] for row in result['players'])
    assert next(row for row in result['players'] if row['player']=='玩家3')['deck']['categories']==[]
    assert response.headers['cache-control']=='no-store'


def test_series_empty_or_invalid_rules_and_missing_snapshot(web):
    module,client,root=web
    (root/'deck-rules.json').write_text('{"version":1,"rules":[]}',encoding='utf-8')
    asyncio.run(module.series_stats.refresh())
    response=client.get('/mc/api/deck-stats');assert response.status_code==200
    assert response.json()['series']==[] and response.json()['total']==100
    (root/'deck-rules.json').write_text('broken',encoding='utf-8')
    with pytest.raises(ValueError):
        asyncio.run(module.series_stats.refresh())
    assert client.get('/mc/api/deck-stats').json() == response.json()
    module.front_stats.state['players']=[]
    with pytest.raises(ValueError):
        asyncio.run(module.series_stats.refresh())
    module.series_stats = module.SeriesStats(root/'missing.json', module.build_deck_stats)
    assert client.get('/mc/api/deck-stats').status_code==503


def test_series_partners_deduplicated_sorted_and_missing_rank(web, monkeypatch):
    module, client, _ = web
    def row(rank, labels):
        return {'rank': rank, 'deck': {'categories': [{'label': label, 'image': None} for label in labels]}}
    fake_rows = [
        row(12, ['A', 'A', 'B', 'C']), row(3, ['A', 'C']), row(None, ['A', 'B', 'C']),
        row(7, ['A', 'D']), row(None, ['E'])] + [row(None, []) for _ in range(95)]
    for player, entry in zip(module.front_stats.state['players'], fake_rows): entry['player'] = player
    monkeypatch.setattr(module, 'front_rows', lambda *args, **kwargs: fake_rows)
    asyncio.run(module.series_stats.refresh())
    summaries = {entry['label']: entry for entry in client.get('/mc/api/deck-stats').json()['series']}
    assert summaries['A'] == {'label': 'A', 'count': 4, 'image': None, 'highest_rank': 3,
                              'partners': [{'label': 'C', 'count': 3}, {'label': 'B', 'count': 2}, {'label': 'D', 'count': 1}]}
    assert summaries['B']['partners'] == [{'label': 'A', 'count': 2}, {'label': 'C', 'count': 2}]
    assert summaries['E']['partners'] == [] and summaries['E']['highest_rank'] is None


def test_series_visitors_only_read_prebuilt_bytes(web, monkeypatch):
    module, client, _ = web
    asyncio.run(module.series_stats.refresh())
    expected = client.get('/mc/api/deck-stats').content
    def forbidden(*args): raise AssertionError('访客不应读榜单、查卡组或重新计算')
    monkeypatch.setattr(module.front_stats, 'snapshot', forbidden)
    monkeypatch.setattr(module, 'front_rows', forbidden)
    monkeypatch.setattr(module.series_stats, 'build', forbidden)
    for _ in range(5):
        response = client.get('/mc/api/deck-stats')
        assert response.status_code == 200 and response.content == expected
    assert not module.series_stats.event.is_set()


def test_card_stats_are_precomputed_and_art_is_limited_to_snapshot(web, monkeypatch):
    module, client, _ = web
    expected = client.get('/mc/api/card-stats')
    assert expected.status_code == 200
    data = expected.json()
    assert data['total'] == 100 and data['recorded_players'] == 1
    cards = {card['code']: card for card in data['cards']}
    assert set(cards) == {1,2,3}
    assert cards[1]['users'] == 1 and cards[1]['main_average'] == 2 and cards[1]['side_average'] == 0
    assert cards[3]['side_average'] == 1
    def forbidden(*args, **kwargs): raise AssertionError('卡片统计请求不应查询卡组或主库')
    monkeypatch.setattr(module, 'catalog', forbidden)
    monkeypatch.setattr(module, 'front_rows', forbidden)
    monkeypatch.setattr(module.front_stats, 'snapshot', forbidden)
    for _ in range(3): assert client.get('/mc/api/card-stats').content == expected.content
    calls=[]
    async def art(code): calls.append(code); return b'image'
    monkeypatch.setattr(module,'card_art',art)
    assert client.get('/mc/api/card-art/1').status_code == 200
    assert client.get('/mc/api/card-art/9999').status_code == 404
    assert calls == [1]
