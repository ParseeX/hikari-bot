import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hikari_bot.services.mycard_archive import admin_auth
from hikari_bot.services.mycard_archive.admin_auth import AdminAuth, LoginLimited, LoginRejected, password_record
from hikari_bot.services.mycard_archive.presentation import classify, read_rules
from hikari_bot.services.mycard_archive.replay import Deck
from hikari_bot.services.mycard_archive.rule_admin import RuleAdmin, StaleRules


PASSWORD = 'test-only-password'


@pytest.mark.parametrize('legacy', [False, True])
def test_manual_deck_admin_auth_edit_and_refresh(web, root, monkeypatch, legacy):
    from hikari_bot.services.mycard_archive.presentation import deck_url
    module, client = web
    endpoint = '/mc/admin/api/manual-decks'
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint, json={}).status_code == 401
    assert client.post('/mc/admin/api/login', json={'password': PASSWORD}).status_code == 200
    refreshes = []
    client.app.state.mc_series_cache = SimpleNamespace(request_refresh=lambda: refreshes.append(True))
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id': code}))
    initial = client.get(endpoint).json()
    assert initial['decks'] == []
    link = deck_url(Deck('分享者', tuple(range(1, 41)), (101,), (102,)))
    if legacy:
        link = ('http://deck.ourygo.top/ydk/show.html?ygotype=deck&v=0&'
                'main=33396948_7902349_70903634_8124921_44519536_70368879*35&extra=&side=&name=test')
    body = {'revision': initial['revision'], 'player': '我的账号', 'link': link}
    assert client.post(endpoint, json=body, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post(endpoint, json={**body, 'link': 'https://invalid.test/'}).status_code == 400
    assert client.post(endpoint, json=body).status_code == 200
    assert refreshes == [True]
    assert client.post(endpoint, json=body).status_code == 409
    saved = client.get(endpoint).json()
    row = saved['decks'][0]
    assert (row['main_count'], row['extra_count'], row['side_count']) == ((40, 0, 0) if legacy else (40, 1, 1))
    if legacy:
        from hikari_bot.services.mycard_archive.manual_decks import parse_link
        assert parse_link('我的账号', row['link']).main.count(70368879) == 35
    assert client.post(endpoint, json={'revision': saved['revision'], 'id': row['id'],
                                     'player': '修改后的用户名', 'link': None}).status_code == 200
    renamed = client.get(endpoint).json()['decks'][0]
    assert renamed['player'] == '修改后的用户名'
    assert renamed['updated_at'] == row['updated_at']
    assert len(refreshes) == 2
    assert not (root / 'archive.sqlite3').exists()


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'admin-password.json').write_text(json.dumps(password_record(PASSWORD)))
    (tmp_path / 'deck-rules.json').write_text('{"version":1,"rules":[]}')
    return tmp_path


def test_auth_limits_failed_attempts_and_revokes_session_on_reset(root, monkeypatch):
    auth = AdminAuth(root)
    for _ in range(5):
        with pytest.raises(LoginRejected):
            auth.login('wrong', 'client')
    with pytest.raises(LoginLimited):
        auth.login(PASSWORD, 'client')
    token = auth.login(PASSWORD, 'another-client')
    assert auth.valid(token)
    assert token.encode() not in auth.db_path.read_bytes()
    now = admin_auth.time.time()
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now + 13 * 3600)
    assert not auth.valid(token)
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now)
    assert auth.valid(token)
    auth.password_path.write_text(json.dumps(password_record('changed-password')))
    assert not auth.valid(token)


@pytest.fixture
def web(root, monkeypatch):
    spec = importlib.util.spec_from_file_location('mc_admin_test', Path('hikari_bot/plugins/web/routes/mycard_admin.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'settings', SimpleNamespace(mycard_archive_dir=root, card_catalog_path=root/'cards.db'))
    cards = {123: {'id': 123, 'cn_name': '主卡甲', 'data': {'type': 0x21}},
             456: {'id': 456, 'cn_name': '主卡乙', 'data': {'type': 2}}}
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(
        by_id=lambda code: cards.get(code), search_many=lambda query: [c for c in cards.values() if query in c['cn_name']]))
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app, base_url='https://ygo.xyk.one', headers={'Origin': module.ORIGIN}) as client:
        yield module, client


def test_admin_page_is_public_but_all_data_and_mutations_require_login(web):
    module, client = web
    assert client.get('/mc/admin').status_code == 200
    for path in ['rules', 'cards?q=主卡甲']:
        assert client.get('/mc/admin/api/' + path).status_code == 401
    for path in ['rules', 'rules/delete', 'logout']:
        assert client.post('/mc/admin/api/' + path, json={}).status_code == 401
    assert client.post('/mc/admin/api/login', json={'password': PASSWORD}, headers={'Origin': 'https://evil.invalid'}).status_code == 403


def test_classification_search_only_returns_main_deck_cards(web, monkeypatch):
    module, client = web
    # 主卡灵摆、仪式保留；额外灵摆仍要排除，数字卡号查询也不能绕过筛选。
    types = [0x11, 0x21, 0x81, 0x1000021, 2, 4,
             0x41, 0x2001, 0x800001, 0x4000001, 0x1000041, 0x1002001, 0x1800001, 0x4001, None]
    cards = [{'id': i + 1, 'cn_name': f'系列{i}', 'data': {'type': kind}}
             for i, kind in enumerate(types)]
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(
        search_many=lambda query: [c for c in cards if str(c['id']) == query] if query.isdigit() else cards))
    assert client.post('/mc/admin/api/login', json={'password': PASSWORD}).status_code == 200
    result = client.get('/mc/admin/api/cards', params={'q': '系列'}).json()
    assert [c['id'] for c in result['cards']] == [1, 2, 3, 4, 5, 6]
    assert result['card'] == result['cards'][0]
    assert client.get('/mc/admin/api/cards?q=7').json() == {'cards': [], 'card': None}


def test_edit_rules_multilabel_main_only_conflict_and_logout(web, root):
    module, client = web
    response = client.post('/mc/admin/api/login', json={'password': PASSWORD})
    assert response.status_code == 200
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    assert client.get('/mc/admin/api/cards?q=主卡甲').json()['cards'][0]['id'] == 123
    assert len(client.get('/mc/admin/api/cards?q=主卡').json()['cards']) == 2
    assert client.get('/mc/admin/api/cards?q=不存在').json()['cards'] == []
    state = client.get('/mc/admin/api/rules').json()
    original = state['revision']
    body = {'revision': original, 'label': '分类甲', 'cards': [123], 'match': 'any'}
    assert client.post('/mc/admin/api/rules', json={**body, 'match':'all'}).status_code == 422
    assert client.post('/mc/admin/api/rules', json=body).status_code == 200
    assert client.post('/mc/admin/api/rules', json=body).status_code == 409
    state = client.get('/mc/admin/api/rules').json()
    body.update(revision=state['revision'], label='分类乙', cards=[456])
    assert client.post('/mc/admin/api/rules', json=body, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/mc/admin/api/rules', json=body).status_code == 200
    rules = read_rules(root/'deck-rules.json')
    assert classify(Deck('甲', (123, 456), (), ()), rules)['labels'] == ['分类甲', '分类乙']
    assert classify(Deck('甲', (123,), (456,), (456,)), rules)['labels'] == ['分类甲']
    state = client.get('/mc/admin/api/rules').json()
    assert client.post('/mc/admin/api/rules', json={**body, 'revision':state['revision'], 'index':1, 'label':'新分类'}).status_code == 200
    state = client.get('/mc/admin/api/rules').json()
    assert client.post('/mc/admin/api/rules/delete', json={'revision':state['revision'],'index':0}).status_code == 200
    assert read_rules(root/'deck-rules.json')[0]['label'] == '新分类'
    assert len(read_rules(root/'deck-rules.json.bak')) == 2
    assert client.post('/mc/admin/api/logout', json={}).status_code == 200
    assert client.get('/mc/admin/api/rules').status_code == 401


def test_unconfigured_password_fails_closed(web, root):
    module, client = web
    (root/'admin-password.json').unlink()
    assert client.post('/mc/admin/api/login', json={'password':PASSWORD}).status_code == 503
    assert client.get('/mc/admin/api/rules').status_code == 401


def test_rule_can_save_more_than_twenty_selected_cards(web, root, monkeypatch):
    module, client = web
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id':code}))
    assert client.post('/mc/admin/api/login', json={'password':PASSWORD}).status_code == 200
    revision = client.get('/mc/admin/api/rules').json()['revision']
    cards = list(range(100, 130))
    assert client.post('/mc/admin/api/rules', json={
        'revision': revision, 'label':'系列', 'cards':cards, 'match':'any',
    }).status_code == 200
    assert read_rules(root/'deck-rules.json')[0]['cards'] == cards


def test_atomic_rule_changes_preserve_old_rules_and_reject_stale_input(root):
    manager = RuleAdmin(root/'deck-rules.json')
    _, revision = manager.read()
    manager.change(revision, rule={'label':'甲','cards':[123],'match':'any'})
    saved = manager.path.read_bytes()
    with pytest.raises(StaleRules):
        manager.change(revision, index=0)
    _, revision = manager.read()
    with pytest.raises(ValueError):
        manager.change(revision, rule={'label':'坏规则','cards':[]})
    assert manager.path.read_bytes() == saved


def test_representative_persistence_conflicts_clear_and_shared_label(web, root):
    module, client = web
    client.post('/mc/admin/api/login', json={'password': PASSWORD})
    manager = RuleAdmin(root/'deck-rules.json')
    for code in (123, 456):
        _, rev = manager.read()
        manager.change(rev, rule={'label': '混合', 'cards': [code]})
    state = client.get('/mc/admin/api/rules').json()
    assert state['effective_representatives'] == {'混合': 123}
    assert state['representatives'] == {}
    body = {'revision': state['revision'], 'label': '混合', 'card_id': 456}
    assert client.post('/mc/admin/api/representatives', json=body,
                       headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/mc/admin/api/representatives', json={**body, 'card_id': 999}).status_code == 400
    assert client.post('/mc/admin/api/representatives', json={**body, 'card_id': True}).status_code == 422
    assert client.post('/mc/admin/api/representatives', json=body).status_code == 200
    assert client.post('/mc/admin/api/representatives', json=body).status_code == 409
    assert RuleAdmin(manager.path).read()[0]['representatives'] == {'混合': 456}
    state = client.get('/mc/admin/api/rules').json()
    assert state['representatives'] == {'混合': 456} and state['names']['456'] == '主卡乙'
    assert state['effective_representatives'] == {'混合': 456}
    manager.change(state['revision'], index=0)
    data, rev = manager.read()
    assert data['representatives'] == {'混合': 456}
    assert client.post('/mc/admin/api/representatives', json={**body, 'revision': rev, 'card_id': 123}).status_code == 200
    data, rev = manager.read()
    assert data['representatives'] == {'混合': 123}
    assert client.post('/mc/admin/api/representatives', json={**body, 'revision': rev, 'card_id': None}).status_code == 200
    assert manager.read()[0]['representatives'] == {}
    assert client.get('/mc/admin/api/rules').json()['effective_representatives'] == {'混合': 456}
    assert json.loads(manager.path.with_suffix('.json.bak').read_text('utf-8'))['representatives'] == {'混合': 123}



def test_representative_search_includes_extra_deck(web, monkeypatch):
    module, client = web
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(search_many=lambda q: [
        {'id': 888, 'cn_name': '额外代表卡', 'data': {'type': 0x41}}]))
    client.post('/mc/admin/api/login', json={'password': PASSWORD})
    assert client.get('/mc/admin/api/cards?q=额外').json()['cards'] == []
    assert client.get('/mc/admin/api/cards?q=额外&purpose=representative').json()['cards'][0]['id'] == 888


def test_private_player_history_preserves_manual_changes(web, monkeypatch):
    from hikari_bot.services.mycard_archive.presentation import deck_url
    module, client = web
    endpoint = '/mc/admin/api/player-decks'
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint + '?player=甲').status_code == 401
    client.post('/mc/admin/api/login', json={'password': PASSWORD})
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id': code}))
    manual = '/mc/admin/api/manual-decks'
    for main in [tuple(range(1, 41)), tuple(reversed(range(1, 41))), tuple(range(2, 42)), tuple(range(1, 41))]:
        state = client.get(manual).json()
        body = {'revision': state['revision'], 'player': '甲',
                'link': deck_url(Deck('甲', main, (), ()))}
        if state['decks']:
            body['id'] = state['decks'][0]['id']
        assert client.post(manual, json=body).status_code == 200
    result = client.get(endpoint + '?player=甲').json()
    assert len(result['versions']) == 3
    assert result['versions'][0]['main'] == list(range(1, 41))
    assert result['versions'][1]['main'] == list(range(2, 42))
    assert result['versions'][2]['main'] == list(range(1, 41))
    assert client.get(endpoint).json()['players'] == ['甲']
