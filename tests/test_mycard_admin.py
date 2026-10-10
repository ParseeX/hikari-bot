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
    cards = {123: {'id': 123, 'cn_name': '主卡甲'}, 456: {'id': 456, 'cn_name': '主卡乙'}}
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(
        by_id=lambda code: cards.get(code), search=lambda query: cards.get(123) if query == '主卡甲' else None))
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


def test_edit_rules_multilabel_main_only_conflict_and_logout(web, root):
    module, client = web
    response = client.post('/mc/admin/api/login', json={'password': PASSWORD})
    assert response.status_code == 200
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    assert client.get('/mc/admin/api/cards?q=主卡甲').json()['card']['id'] == 123
    state = client.get('/mc/admin/api/rules').json()
    original = state['revision']
    body = {'revision': original, 'label': '分类甲', 'cards': [123], 'match': 'any'}
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
