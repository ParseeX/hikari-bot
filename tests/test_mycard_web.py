import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hikari_bot.services.mycard_archive.queries import Leaderboard
from hikari_bot.services.mycard_archive.short_links import ShortLinks
from hikari_bot.services.mycard_archive.store import ArchiveStore
from test_mycard_decks import record
from test_mycard_archive import decode_ourygo


@pytest.fixture
def web(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        'mycard_web_test', Path(__file__).parents[1] / 'hikari_bot/plugins/web/routes/mycard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'settings', SimpleNamespace(mycard_archive_dir=tmp_path))

    async def forbidden_rank_query(self):
        pytest.fail('短链接访问不应依赖排行榜')

    monkeypatch.setattr(Leaderboard, 'players', forbidden_rank_query)
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app, follow_redirects=False) as client:
        yield module, client, ShortLinks(tmp_path / 'short-links.sqlite3'), tmp_path


def test_short_link_reads_latest_side_and_changed_deck_without_ranking(web):
    module, client, mapping, root = web
    store = ArchiveStore(root, set())
    record(store, 'first', 100, side=(3,))
    link = mapping.url('甲')
    first = client.get(link)
    assert first.status_code == 302
    assert first.headers['cache-control'] == 'no-store'
    assert first.headers['referrer-policy'] == 'no-referrer'
    record(store, 'second', 200, side=(4,))
    second = client.get(link)
    assert second.status_code == 302
    assert decode_ourygo(second.headers['location'])[2] == {3: 1, 4: 1}
    record(store, 'new_deck', 300, main=(99,), side=(5,))
    third = client.get(link)
    assert decode_ourygo(third.headers['location'])[0] == {99: 1}
    assert decode_ourygo(third.headers['location'])[2] == {5: 1}
    head = client.head(link)
    assert head.status_code == 302 and not head.content
    assert head.headers['location'] == third.headers['location']
    assert ShortLinks(mapping.path).url('甲') == link


@pytest.mark.parametrize('code', ['甲', 'AbCdEfGh1234', '', 'x' * 101, '中文 空格/#?', '\x01bad'])
def test_unknown_code_and_old_username_never_read_archive_or_create_mapping(web, monkeypatch, code):
    module, client, mapping, root = web

    def forbidden(player):
        pytest.fail('未知短码不能读取卡组库')

    monkeypatch.setattr(module, 'latest_links', forbidden)
    response = client.get('/mc/' + quote(code, safe=''))
    assert response.status_code == 404
    assert response.headers['cache-control'] == 'no-store'
    assert not mapping.path.exists()


def test_known_mapping_still_rejects_raw_username_and_unknown_token(web):
    module, client, mapping, root = web
    mapping.url('甲')
    assert client.get('/mc/' + quote('甲')).status_code == 404
    assert client.get('/mc/NoSuchCode123').status_code == 404


def test_valid_code_without_archived_deck_is_404(web):
    module, client, mapping, root = web
    ArchiveStore(root, set())
    assert client.get(mapping.url('尚未收录')).status_code == 404


def test_database_failure_does_not_expose_internal_details(web, monkeypatch):
    module, client, mapping, root = web

    def failed_lookup(name):
        raise sqlite3.OperationalError('internal path must not be shown')

    monkeypatch.setattr(module, 'latest_links', failed_lookup)
    response = client.get(mapping.url('甲'))
    assert response.status_code == 503
    assert 'internal path' not in response.text


def test_multiple_links_are_preserved_and_html_escaped(web, monkeypatch):
    module, client, mapping, root = web
    monkeypatch.setattr(module, 'latest_links', lambda player: [
        'http://deck.ourygo.top/?d=one&name="test"', 'http://deck.ourygo.top/?d=two'])
    response = client.get(mapping.url('<script>alert(1)</script>'))
    assert response.status_code == 200
    assert response.text.count('<a href=') == 2
    assert '<script>' not in response.text
    assert '&amp;name=&quot;test&quot;' in response.text
