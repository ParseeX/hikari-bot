import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hikari_bot.services.mycard_archive.queries import Leaderboard
from hikari_bot.services.mycard_archive.store import ArchiveStore
from test_mycard_decks import record
from test_mycard_archive import decode_ourygo


@pytest.fixture
def web(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        'mycard_web_test', Path(__file__).parents[1] / 'hikari_bot/plugins/web/routes/mycard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = ['甲'] + [f'玩家{i}' for i in range(2, 101)]

    async def fetch():
        return [{'username': name} for name in names]

    monkeypatch.setattr(module, 'leaderboard', Leaderboard(fetch, ttl=0))
    monkeypatch.setattr(module, 'settings', SimpleNamespace(mycard_archive_dir=tmp_path))
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app, follow_redirects=False) as client:
        yield module, client, names, tmp_path


def test_redirect_reads_latest_deck_and_side_without_caching(web):
    module, client, names, root = web
    store = ArchiveStore(root, set())
    record(store, 'first', 100, side=(3,))
    first = client.get('/mc/' + quote('甲'))
    assert first.status_code == 302
    assert first.headers['cache-control'] == 'no-store'
    assert first.headers['location'].startswith('http://deck.ourygo.top/?')
    record(store, 'second', 200, side=(4,))
    second = client.get('/mc/' + quote('甲'))
    assert second.status_code == 302
    assert second.headers['location'] != first.headers['location']
    assert decode_ourygo(second.headers['location'])[2] == {3: 1, 4: 1}
    head = client.head('/mc/' + quote('甲'))
    assert head.status_code == 302 and not head.content
    assert head.headers['location'] == second.headers['location']


def test_outside_top_100_cannot_read_library(web, monkeypatch):
    module, client, names, root = web

    def forbidden(player):
        pytest.fail('不在榜的请求不能读取卡组库')

    monkeypatch.setattr(module, 'latest_links', forbidden)
    names.append('第101名')
    response = client.get('/mc/' + quote('第101名'))
    assert response.status_code == 403
    assert response.headers['cache-control'] == 'no-store'


def test_rank_100_allowed_but_missing_deck_is_404(web):
    module, client, names, root = web
    ArchiveStore(root, set())
    assert client.get('/mc/' + quote(names[99])).status_code == 404


def test_expired_rank_snapshot_does_not_grant_access_on_failure(web, monkeypatch):
    module, client, names, root = web
    monkeypatch.setattr(module, 'latest_links', lambda player: ['http://deck.ourygo.top/'])
    assert client.get('/mc/' + quote('甲')).status_code == 302

    async def fail():
        raise TimeoutError()

    module.leaderboard.fetch = fail
    assert client.get('/mc/' + quote('甲')).status_code == 503
    names.remove('甲')
    assert client.get('/mc/' + quote('甲')).status_code == 503


def test_drop_out_of_ranking_revokes_redirect(web, monkeypatch):
    module, client, names, root = web
    monkeypatch.setattr(module, 'latest_links', lambda player: ['http://deck.ourygo.top/'])
    assert client.get('/mc/' + quote('甲')).status_code == 302
    names[0] = '新玩家'
    assert client.get('/mc/' + quote('甲')).status_code == 403


def test_encoded_username_and_database_failure(web, monkeypatch):
    module, client, names, root = web
    player = '中文 空格/#?'
    names[0] = player
    seen = []

    def failed_lookup(name):
        seen.append(name)
        raise sqlite3.OperationalError('internal path must not be shown')

    monkeypatch.setattr(module, 'latest_links', failed_lookup)
    response = client.get('/mc/' + quote(player, safe=''))
    assert seen == [player]
    assert response.status_code == 503
    assert 'internal path' not in response.text


def test_multiple_links_are_preserved_and_html_escaped(web, monkeypatch):
    module, client, names, root = web
    names[0] = '<script>alert(1)</script>'
    monkeypatch.setattr(module, 'latest_links', lambda player: [
        'http://deck.ourygo.top/?d=one&name="test"', 'http://deck.ourygo.top/?d=two'])
    response = client.get('/mc/' + quote(names[0], safe=''))
    assert response.status_code == 200
    assert response.text.count('<a href=') == 2
    assert '<script>' not in response.text
    assert '&amp;name=&quot;test&quot;' in response.text


@pytest.mark.parametrize('player', ['', 'x' * 101, '\x01bad'])
def test_invalid_username_rejected(web, player):
    module, client, names, root = web
    assert client.get('/mc/' + quote(player, safe='')).status_code == 400
