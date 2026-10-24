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


def test_short_link_opens_internal_profile_without_ranking(web):
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
    assert second.headers['location'] == '/mc/player/?code=' + link.rsplit('/', 1)[-1]
    record(store, 'new_deck', 300, main=(99,), side=(5,))
    third = client.get(link)
    assert third.headers['location'] == second.headers['location']
    head = client.head(link)
    assert head.status_code == 302 and not head.content
    assert head.headers['location'] == third.headers['location']
    assert ShortLinks(mapping.path).url('甲') == link


@pytest.mark.parametrize('language,policy', [('en', 'no-cache'), ('zh', 'public, max-age=3600')])
def test_card_image_refresh_token_and_language_cache_policy(web, monkeypatch, language, policy):
    module, client, _, _ = web
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id': code}))
    calls = []
    async def image(code, info, refresh):
        calls.append((code, refresh))
        return b'image', language
    monkeypatch.setattr(module, 'full_card_image', image)
    response = client.get('/mc/api/card-image/42?refresh=page-2')
    assert response.status_code == 200 and response.content == b'image'
    assert response.headers['cache-control'] == policy
    assert response.headers['x-card-image-language'] == language
    assert calls == [(42, 'page-2')]
    assert client.get('/mc/api/card-image/42?refresh=' + 'x' * 65).status_code == 422


def test_stable_image_url_caches_fallback_and_etag_avoids_retransmission(web, monkeypatch):
    module, client, _, _ = web
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id': code}))
    async def image(*args): return b'english-image', 'en'
    monkeypatch.setattr(module, 'full_card_image', image)
    first = client.get('/mc/api/card-image/42')
    assert first.headers['cache-control'] == 'public, max-age=3600'
    second = client.get('/mc/api/card-image/42', headers={'If-None-Match':first.headers['etag']})
    assert second.status_code == 304 and second.content == b''
    # 图源临时回退时，不能把英文写进中文版本地址的浏览器缓存。
    mismatch = client.get('/mc/api/card-image/42?v=' + 'a' * 64)
    assert mismatch.headers['cache-control'] == 'no-store'
    matching = client.get('/mc/api/card-image/42?v=' + first.headers['etag'].strip('"'))
    assert matching.headers['cache-control'] == 'public, max-age=3600'


@pytest.mark.parametrize('image', [None, b'chinese-image'])
def test_refresh_returns_only_language_and_content_version(web, monkeypatch, image):
    import hashlib
    module, client, _, _ = web
    monkeypatch.setattr(module, 'catalog', lambda: SimpleNamespace(by_id=lambda code: {'id': code}))
    async def check(*args): return image
    monkeypatch.setattr(module, 'check_chinese_image', check)
    response = client.get('/mc/api/card-image/42/refresh')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == {'language':'zh' if image else 'en',
                               'version':hashlib.sha256(image).hexdigest() if image else None}
    assert len(response.content) < 150


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


def test_valid_code_without_archived_deck_opens_profile_empty_state(web):
    module, client, mapping, root = web
    ArchiveStore(root, set())
    assert client.get(mapping.url('尚未收录')).status_code == 302


def test_database_failure_does_not_expose_internal_details(web, monkeypatch):
    module, client, mapping, root = web

    def failed_lookup(self, code):
        raise sqlite3.OperationalError('internal path must not be shown')

    link = mapping.url('甲')
    monkeypatch.setattr(ShortLinks, 'player', failed_lookup)
    response = client.get(link)
    assert response.status_code == 503
    assert 'internal path' not in response.text


def test_short_link_does_not_interpolate_player_in_html(web, monkeypatch):
    module, client, mapping, root = web
    monkeypatch.setattr(module, 'latest_links', lambda player: [
        'http://deck.ourygo.top/?d=one&name="test"', 'http://deck.ourygo.top/?d=two'])
    response = client.get(mapping.url('<script>alert(1)</script>'))
    assert response.status_code == 302
    assert response.headers['location'].startswith('/mc/player/?code=')
    assert '<script>' not in response.text


def test_profile_returns_latest_deck_and_whitelisted_stats(web, monkeypatch):
    module, client, mapping, root = web
    store = ArchiveStore(root, set())
    record(store, 'first', 100, side=(3,))
    record(store, 'second', 200, side=(4,))
    class Catalog:
        def deck_sort_data(self, codes): return {}
        def cards_by_id(self, codes): return {}
    monkeypatch.setattr(module, 'catalog', lambda: Catalog())
    monkeypatch.setattr(module, 'deck_title', lambda *args: '测试卡组')
    monkeypatch.setattr(module.RuleAdmin, 'read', lambda self: ({'rules': []}, 'revision'))
    monkeypatch.setattr(module.front_stats, 'snapshot', lambda: {
        'players': ['甲'], 'stats': {'甲': {'rank': 1, 'score': 1234, '_history': ['private'], 'qq': 'private'}}})
    response = client.get('/mc/api/player', params={'name': '甲'})
    assert response.status_code == 200
    data = response.json()
    assert data['deck']['side'] == [3, 4]
    assert data['stats']['rank'] == 1 and 'qq' not in data['stats'] and '_history' not in data['stats']
    assert 'catalog_id' not in str(data)
    record(store, 'changed', 300, main=(99,), side=(5,))
    assert client.get('/mc/api/player', params={'name': '甲'}).json()['deck']['main'] == [99]
    ydk = client.get('/mc/api/player', params={'name': '甲', 'format': 'ydk'})
    assert ydk.status_code == 200 and '#main\n99\n#extra' in ydk.text and '!side\n5\n' in ydk.text
    assert "filename*=UTF-8''" in ydk.headers['content-disposition']
    assert client.get('/mc/api/player', params={'name': '非榜单玩家'}).status_code == 404
    code = mapping.url('甲').rsplit('/', 1)[-1]
    monkeypatch.setattr(module.front_stats, 'snapshot', lambda: {'players': [], 'stats': {}})
    assert client.get('/mc/api/player', params={'code': code}).json()['deck']['main'] == [99]
    assert client.get('/mc/api/player', params={'code': code, 'name': '甲'}).status_code == 400
    assert client.get('/mc/api/player').status_code == 400
    assert client.get('/mc/api/player', params={'code': 'x' * 12}).status_code == 404
