"""对照 YGOPro 整理顺序，并验证 YDK、链接、旧归档导出的卡片保真。"""
from collections import Counter
import json
from types import SimpleNamespace
import zipfile

import pytest

from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.services.mycard_archive import sorting
from hikari_bot.services.mycard_archive.presentation import deck_url
from hikari_bot.services.mycard_archive.presentation import deck_links
from hikari_bot.services.mycard_archive.library import DeckVersion
from hikari_bot.services.mycard_archive.replay import Deck
from hikari_bot.services.mycard_archive.store import ArchiveStore
from scripts.card_catalog import catalog as cat
from test_card_catalog import card
from test_mycard_archive import decode_ourygo, replay


def ordered(data, codes=None):
    catalog = SimpleNamespace(deck_sort_data=lambda _: data)
    return sorting.sort_sections(codes if codes is not None else reversed(list(data)), catalog=catalog)[0]


def test_ygopro_type_groups_and_monster_subtypes():
    data = {
        10: (4, 0, 0, 0), 20: (2, 0, 0, 0), 30: (33, 4, 1000, 1000),
        40: (17, 1, 500, 500), 50: (2 | 0x10000, 0, 0, 0),
        60: (33, 8, 1000, 1000), 70: (1 | 0x40, 8, 2000, 2000),
        80: (1 | 0x2000, 8, 2000, 2000), 90: (1 | 0x800000, 4, 2000, 2000),
        100: (1 | 0x4000000, 3, 2000, 0x145), 110: (1 | 0x80, 8, 2000, 2000),
        120: (17 | 0x1000000, (8 << 24) | (8 << 16) | 7, 2000, 2000),
        130: (33 | 0x1000000, 3, 2000, 2000),
    }
    assert ordered(data) == (120, 40, 60, 30, 130, 70, 110, 80, 90, 100, 20, 50, 10)


def test_level_attack_defense_and_code_ties():
    data = {10: (33, 4, 1000, 1000), 20: (33, 4, 1000, 1000),
            30: (33, 4, 1000, 2000), 40: (33, 4, 2000, 0),
            50: (33, 5, -2, -2), 60: (33, 4, -2, 3000),
            70: (33, 4, 0, 0), 80: (33, 4, 0xfffffffe, 3000)}
    assert ordered(data) == (50, 40, 30, 10, 20, 70, 60, 80)


def test_link_arrows_and_pendulum_scales_do_not_sort_as_defense_or_level():
    assert ordered({10: (0x4000021, 3, 2000, 1), 20: (0x4000021, 3, 2000, 0x145)}) == (10, 20)
    assert ordered({10: (0x1000021, 4, 1000, 0),
                    20: (0x1000021, (9 << 24) | (9 << 16) | 3, 3000, 0)}) == (10, 20)


def test_spell_trap_subtypes_then_code():
    assert ordered({90: (2, 0, 0, 0), 80: (2 | 0x80, 0, 0, 0),
                    70: (2 | 0x10000, 0, 0, 0), 60: (2 | 0x20000, 0, 0, 0),
                    50: (2 | 0x40000, 0, 0, 0), 40: (2 | 0x80000, 0, 0, 0),
                    30: (4, 0, 0, 0), 20: (4 | 0x20000, 0, 0, 0),
                    10: (4 | 0x100000, 0, 0, 0)}) == (90, 80, 70, 60, 50, 40, 30, 20, 10)


def test_unknown_cards_and_duplicates_are_preserved(tmp_path):
    assert ordered({20: (33, 4, 1000, 0), 30: (None, None, None, None)},
                   [30, 10, 20, 20]) == (20, 20, 10, 30)
    missing = CardCatalog(tmp_path / 'absent.sqlite3')
    assert sorting.sort_sections([30, 10, 20, 20], [], catalog=missing) == ((10, 20, 20, 30), ())
    assert not missing.path.exists()


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path = tmp_path / 'cards.sqlite3'
    db = cat.connect(path)
    with db:
        cat.import_cards(db, {str(cid): card(cid=cid, passcode=code, type=t, level=level)
                             for cid, code, t, level in [(1, 123, 4, 0), (2, 456, 2, 0), (3, 789, 33, 4)]}, {})
        db.execute("INSERT INTO card_artwork_ids VALUES(790,3,'test')")
    db.close()
    monkeypatch.setattr(sorting, 'settings', SimpleNamespace(card_catalog_path=path))
    return CardCatalog(path)


def test_real_catalog_artwork_keeps_original_codes(catalog):
    assert sorting.sort_sections([123, 790, 456, 789], catalog=catalog)[0] == (789, 790, 456, 123)


def test_ydk_and_link_use_same_order_in_each_section(catalog):
    original = (123, 789, 456, 789)
    deck = Deck('甲', original, original, original)
    expected = (789, 789, 456, 123)
    lines = deck.ydk().splitlines()
    for marker in ('#main', '#extra', '!side'):
        start = lines.index(marker) + 1
        assert tuple(map(int, lines[start:start + 4])) == expected
    for section in decode_ourygo(deck_url(deck)):
        assert list(section) == [789, 456, 123]
        assert section == Counter(original)
    assert deck.main == original


def test_old_bundle_regenerates_sorted_ydk_without_rewriting_originals(catalog, tmp_path):
    store = ArchiveStore(tmp_path / 'archive', set())
    mid, _ = store.begin('room', ['甲', '乙'])
    raw = replay(main_a=(123, 456, 789))
    store.save_replay(mid, raw)
    store.finalize(mid, True)
    directory = store.root / mid
    manifest_path = directory / 'manifest.json'
    before_manifest = manifest_path.read_bytes()
    manifest = json.loads(before_manifest)
    filename = manifest['replays'][0]['decks'][0]['file']
    old_ydk = b'#main\n123\n456\n789\n#extra\n!side\n'
    (directory / filename).write_bytes(old_ydk)
    with zipfile.ZipFile(store.bundle(mid)) as archive:
        assert '#main\n789\n456\n123\n#extra' in archive.read(filename).decode()
        assert archive.read(manifest['replays'][0]['file']) == raw
        assert archive.read('manifest.json') == before_manifest
    assert (directory / filename).read_bytes() == old_ydk
    assert manifest_path.read_bytes() == before_manifest


def test_side_is_sorted_before_splitting_multiple_links(catalog):
    side = tuple(range(1, 16)) + (789,)
    version = DeckVersion(Deck('甲', (789,), (), side), 0, 0, 1)
    links = deck_links(version.deck)
    first, second = [decode_ourygo(link)[2] for link in links]
    assert list(first) == [789, *range(1, 15)]
    assert list(second) == [15]
    assert first + second == Counter(side)
