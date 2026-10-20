import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.manual_decks import ManualDecks, StaleManualDecks, parse_link
from hikari_bot.services.mycard_archive.presentation import deck_url
from hikari_bot.services.mycard_archive.replay import Deck


def share(offset=0):
    return deck_url(Deck('分享者', tuple(range(1 + offset, 41 + offset)), (101,), (102,)))


@pytest.fixture
def manual(tmp_path):
    return ManualDecks(tmp_path / 'manual-decks.json')


def save(manual, player='我的账号', link=None, entry_id=None):
    return manual.save(manual.read()[1], entry_id=entry_id, player=player,
                       link=share() if link is None else link,
                       catalog=SimpleNamespace(by_id=lambda code: {'id': code}))


def test_link_roundtrip_and_invalid_payloads():
    deck = parse_link('绑定用户名', share())
    assert deck.player == '绑定用户名'
    assert deck.main == tuple(range(1, 41))
    assert deck.extra == (101,) and deck.side == (102,)
    for bad in ['https://example.com/', 'file://host/?ygotype=deck&d=AAA',
                'https://example.com/?ygotype=deck&d=@@@', share() + '&d=AAA',
                share().replace('v=1', 'v=2'), share().replace('ygotype=deck', 'ygotype=other'),
                deck_url(Deck('x', (1, 2), ())),
                deck_url(Deck('x', tuple(range(1, 41)), (), (1, 1, 1)))]:
        with pytest.raises(ValueError):
            parse_link('x', bad)


def test_binding_update_conflict_and_failed_save_preserve_file(manual):
    _, old_revision = manual.read()
    with patch('hikari_bot.services.mycard_archive.manual_decks.time.time', return_value=100):
        entry = save(manual)
    original = manual.path.read_bytes()
    with pytest.raises(StaleManualDecks):
        manual.save(old_revision, entry_id=entry['id'], player='x', link=share(), catalog=None)
    with pytest.raises(ValueError, match='已有'):
        save(manual)
    with pytest.raises(ValueError, match='未找到'):
        manual.save(manual.read()[1], entry_id=entry['id'], player='x', link=share(),
                    catalog=SimpleNamespace(by_id=lambda _: None))
    assert manual.path.read_bytes() == original
    renamed = manual.save(manual.read()[1], entry_id=entry['id'], player='新用户名',
                          link=None, catalog=None)
    assert renamed['updated_at'] == 100
    assert renamed['main'] == entry['main']
    assert manual.path.with_suffix('.json.bak').read_bytes() == original
    changed = save(manual, '新用户名', share(10), entry['id'])
    assert changed['main'] == list(range(11, 51))
    assert len(manual.read()[0]) == 1


def test_manual_current_deck_does_not_modify_match_history(manual, tmp_path):
    db_path = tmp_path / 'archive.sqlite3'
    with sqlite3.connect(db_path) as db:
        DeckLibrary.initialize(db)
        db.execute('INSERT INTO deck_observations VALUES(?,?,?,?,?,?,?)',
                   ('real-match', '我的账号', json.dumps([500]), '[]', '[]', 50, 5000))
    library = DeckLibrary(db_path)
    before = db_path.read_bytes()
    with patch('hikari_bot.services.mycard_archive.manual_decks.time.time', return_value=100):
        entry = save(manual)
    # 旧对局即使延迟归档，也不覆盖较新的手动上传。
    assert library.latest('我的账号').deck.main == tuple(range(1, 41))
    assert library.latest('我的账号').observations == 0
    assert library.history('我的账号')[0].deck.main == (500,)
    assert set(range(1, 41)) <= set(library.card_ids())
    assert db_path.read_bytes() == before
    manual.save(manual.read()[1], entry_id=entry['id'], player='另一个账号', link=None, catalog=None)
    assert library.latest('我的账号').deck.main == (500,)
    assert library.latest('另一个账号').deck.side == (102,)
    with sqlite3.connect(db_path) as db:
        db.execute('INSERT INTO deck_observations VALUES(?,?,?,?,?,?,?)',
                   ('new-match', '另一个账号', '[600]', '[]', '[]', 101, 102))
    assert library.latest('另一个账号').deck.main == (600,)
    assert library.latest('未收录') is None


def test_failed_atomic_write_preserves_current_record(manual):
    save(manual)
    before = manual.path.read_bytes()
    with patch('hikari_bot.services.mycard_archive.manual_decks.atomic_write', side_effect=OSError):
        with pytest.raises(OSError):
            save(manual, '另一个账号')
    assert manual.path.read_bytes() == before
