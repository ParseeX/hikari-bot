from concurrent.futures import ThreadPoolExecutor
import sqlite3

from hikari_bot.services.mycard_archive import short_links
from hikari_bot.services.mycard_archive.short_links import ShortLinks


def test_parallel_first_queries_and_restart_keep_one_code_per_player(tmp_path):
    path = tmp_path / 'short-links.sqlite3'
    names = ['甲', '乙'] * 12
    with ThreadPoolExecutor(max_workers=8) as pool:
        urls = list(pool.map(lambda name: ShortLinks(path).url(name), names))
    assert len(set(urls[::2])) == len(set(urls[1::2])) == 1
    assert urls[0] != urls[1]
    reopened = ShortLinks(path)
    for name, url in zip(names, urls):
        assert reopened.url(name) == url
        assert reopened.player(url.rsplit('/', 1)[1]) == name
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM player_links').fetchone()[0] == 2


def test_collision_retries_without_overwriting_existing_player(tmp_path, monkeypatch):
    codes = iter(['AAAAAAAAAAAA', 'AAAAAAAAAAAA', 'BBBBBBBBBBBB'])
    monkeypatch.setattr(short_links.secrets, 'token_urlsafe', lambda n: next(codes))
    links = ShortLinks(tmp_path / 'short-links.sqlite3')
    assert links.url('甲').endswith('/AAAAAAAAAAAA')
    assert links.url('乙').endswith('/BBBBBBBBBBBB')
    assert links.player('AAAAAAAAAAAA') == '甲'
    assert links.player('BBBBBBBBBBBB') == '乙'
