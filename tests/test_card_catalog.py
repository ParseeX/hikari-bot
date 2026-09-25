"""独立主库的数据身份、过滤、版本保真与失败回滚验证。"""
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts.card_catalog import catalog as cat


def card(cid=4007, passcode=89631139, name='青眼白龙', **data):
    return {'cid': cid, 'id': passcode, 'cn_name': name, 'sc_name': name,
            'jp_name': '青眼の白龍', 'jp_ruby': 'ブルーアイズ・ホワイト・ドラゴン',
            'en_name': 'Blue-Eyes White Dragon',
            'text': {'types': '[怪兽|通常]', 'desc': '效果', 'pdesc': ''},
            'data': {'type': 17, 'ot': 11, 'atk': 3000, 'def': 2500,
                     'level': 8, 'race': 8192, 'attribute': 16, **data}}


@pytest.fixture
def db(tmp_path):
    connection = cat.connect(tmp_path / 'catalog.sqlite3')
    yield connection
    connection.close()


def load(db, records, changes=None):
    with db:
        return cat.import_cards(db, {str(r['cid']): r for r in records}, changes or {})


def version(vid=1, jhs_id=139, number='TEST-JP001', rarity='S1R（代标）', name='青眼白龙'):
    return {'id': vid, 'card_id': jhs_id, 'number': number, 'rarity': rarity,
            'name_cn': name, 'name_jp': '', 'aliases': [name]}


def test_import_and_repeat_preserve_names_and_identity(db):
    assert load(db, [card()])['inserted'] == 1
    original = cat.find(db, '89631139')[0]
    assert original['japanese_reading'].startswith('ブルーアイズ')
    assert cat.find(db, '青眼の白龍')[0]['id'] == original['id']
    assert load(db, [card()])['unchanged'] == 1
    assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 1
    changed = card()
    changed['text']['desc'] = '修订后的效果'
    load(db, [changed])
    assert cat.find(db, '青眼白龙')[0]['texts'][0]['effect'] == '修订后的效果'


def test_temporary_to_official_keeps_internal_id_and_both_lookups(db):
    load(db, [card(passcode=100000001)])
    before = cat.find(db, '100000001')[0]['id']
    load(db, [card(passcode=1234)], {'100000001': 1234})
    row = cat.find(db, '00001234')[0]
    assert row['id'] == before
    assert row['passcode'] == '00001234'
    assert row['temporary_id'] == '100000001'
    assert cat.find(db, '100000001')[0]['id'] == before


def test_changelog_conflict_does_not_merge_cards(db):
    load(db, [card(cid=1, passcode=100000001), card(cid=2, passcode=1234)])
    with db:
        result = cat.import_id_changes(db, {'100000001': 1234})
    assert result['id_conflicts'] == 1
    assert cat.find(db, '100000001')[0]['id'] != cat.find(db, '1234')[0]['id']


def test_token_bitmask_and_unknown_data_are_excluded(db):
    invalid = {'cid': 9, 'id': 0, 'jp_name': '資料不足'}
    result = load(db, [card(cid=1, type=0x4000 | 1 | 0x1000 | 0x20), invalid, card()])
    assert result['excluded_token'] == 1
    assert result['pending'] == 1
    assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 1
    assert cat.stats(db)['foreign_key_errors'] == []


def test_link_and_pendulum_stats(db):
    load(db, [card(cid=1, passcode=1, type=cat.TYPE_LINK | 1, level=3, **{'def': 0x145}),
              card(cid=2, passcode=2, type=cat.TYPE_PENDULUM | 1, level=(4 << 24) | (5 << 16) | 7)])
    link, pendulum = cat.find(db, '1')[0], cat.find(db, '2')[0]
    assert link['def_text'] is None and link['link_markers'] == 0x145 and link['link_rating'] == 3
    assert link['level'] is None
    assert (pendulum['level'], pendulum['pendulum_left'], pendulum['pendulum_right']) == (7, 4, 5)


def test_versions_keep_raw_parentheses_and_repeat_is_idempotent(db):
    load(db, [card()])
    rows = [version(), version(vid=2, rarity='S1R'),
            version(vid=3, number='TEST-JP001（异画）', rarity='S1R'),
            version(vid=4, number='OTHER-JP001')]
    result = cat.import_pack(db, 'TEST', rows, expected_cards=1, expected_versions=3)
    assert result['matched_cards'] == 1 and result['inserted'] == 3
    assert cat.import_pack(db, 'TEST', rows)['unchanged'] == 3
    found = cat.find(db, '89631139')[0]['versions']
    assert {v['rarity_raw'] for v in found} == {'S1R（代标）', 'S1R'}
    assert 'TEST-JP001（异画）' in {v['number_raw'] for v in found}


def test_incomplete_pack_does_not_replace_existing(db):
    load(db, [card()])
    cat.import_pack(db, 'TEST', [version(), version(vid=2)])
    with pytest.raises(ValueError, match='版本数不符'):
        cat.import_pack(db, 'TEST', [version()], expected_versions=2)
    assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 2


def test_pack_conflict_rolls_back_entire_batch(db):
    load(db, [card()])
    cat.import_pack(db, 'TEST', [version()])
    with pytest.raises(ValueError, match='归属发生冲突'):
        cat.import_pack(db, 'TEST', [version(vid=2), version(jhs_id=888)])
    assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 1
    assert not db.execute('SELECT * FROM jhs_cards WHERE jhs_card_id=888').fetchall()


def test_ambiguous_alias_keeps_version_pending(db):
    load(db, [card(cid=1, passcode=1), card(cid=2, passcode=2)])
    result = cat.import_pack(db, 'TEST', [version()])
    assert result['unmatched_cards'] == 1
    assert db.execute('SELECT card_id FROM jhs_cards').fetchone()[0] is None
    assert db.execute('SELECT COUNT(*) FROM jhs_versions').fetchone()[0] == 1


def test_refuse_unrelated_database(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as existing:
        existing.execute('CREATE TABLE texts(id INTEGER)')
    with pytest.raises(ValueError, match='不是卡片主库'):
        cat.connect(path)
    with sqlite3.connect(path) as existing:
        assert existing.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('texts',)]


def test_sync_uses_json_md5_and_skips_unchanged_download(db, tmp_path, monkeypatch):
    records = {str(i): card(cid=i, passcode=i) for i in range(1, 10001)}
    raw = json.dumps(records).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('cards.json', raw)
    calls = []
    def fetch(url):
        path = url.removeprefix(cat.BASE_URL)
        calls.append(path)
        return {'cards.zip.md5': json.dumps(hashlib.md5(raw).hexdigest()).encode(),
                'cards.zip': output.getvalue(), 'idChangelog.jsonp': b'{}',
                'releaseDates.json': b'[]'}[path]
    monkeypatch.setattr(cat, 'fetch', fetch)
    assert cat.sync_ygocdb(db, tmp_path / 'sources')['inserted'] == 10000
    assert cat.sync_ygocdb(db, tmp_path / 'sources')['downloaded_cards'] is False
    assert calls.count('cards.zip') == 1


def test_bad_source_checksum_preserves_data_and_sync_state(db, tmp_path, monkeypatch):
    load(db, [card()])
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('cards.json', b'{}')
    values = {'cards.zip.md5': json.dumps('0' * 32).encode(), 'idChangelog.jsonp': b'{}',
              'releaseDates.json': b'[]', 'cards.zip': output.getvalue()}
    monkeypatch.setattr(cat, 'fetch', lambda url: values[url.removeprefix(cat.BASE_URL)])
    with pytest.raises(ValueError, match='不完整'):
        cat.sync_ygocdb(db, tmp_path / 'sources')
    assert cat.find(db, '89631139')
    assert db.execute('SELECT COUNT(*) FROM sync_state').fetchone()[0] == 0


def test_invalid_pack_prefix_rejected_before_credentials_or_network(db, tmp_path):
    with pytest.raises(ValueError, match='前缀格式错误'):
        cat.sync_pack(db, SimpleNamespace(prefix='../escape'), tmp_path)


def test_base_import_unexpected_failure_rolls_back_all_cards(db):
    with pytest.raises(ValueError):
        with db:
            cat.import_cards(db, {'good': card(), 'bad': {'cid': 'invalid'}}, {})
    assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 0


def test_v1_migration_preserves_versions_and_official_name(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    with sqlite3.connect(path) as old:
        old.row_factory = sqlite3.Row
        old.executescript(Path('tests/fixtures/catalog_v1.sql').read_text(encoding='utf-8'))
        old.execute("INSERT INTO catalog_meta VALUES ('schema_version','1')")
        cat.import_cards(old, {'4007': card()}, {})
        old.execute('INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?)',
                    ('TEST', '官网商品名', '2024-01-01', 'https://example.com/?pid=123', None, None, 1, 1, cat.now()))
        old.execute("INSERT INTO jhs_cards VALUES (139,1,'existing_mapping',?)", (cat.now(),))
        old.execute('INSERT INTO jhs_versions VALUES (1,139,?,?,?,?,?,?,?,?,?)',
                    ('TEST', 'TEST-JP001（异画）', 'S1R', '青眼白龙', '', '{}', cat.now(), cat.now(), cat.now()))
    with cat.connect(path) as db:
        assert db.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()[0] == '2'
        assert db.execute('SELECT number_raw FROM jhs_versions').fetchone()[0] == 'TEST-JP001（异画）'
        assert db.execute('SELECT name,konami_pid FROM product_official_links').fetchone()[:] == ('官网商品名', '123')
        assert cat.stats(db)['foreign_key_errors'] == []
        assert db.execute('SELECT COUNT(*) FROM card_catalog').fetchone()[0] == 1
    assert len(list((tmp_path / 'backups').glob('*.sqlite3'))) == 1
    with cat.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 1


def test_unnumbered_products_with_same_name_stay_separate(db):
    load(db, [card()])
    for vid, pid in [(1, 4404), (2, 4405)]:
        product = {'id': pid, 'name': 'EX 复刻版', 'name_origin': 'EX 復刻版'}
        row = {**version(vid=vid, number='无编号（原画）'), 'pack': product}
        cat.import_pack(db, None, [row], product=product)
        assert cat.import_pack(db, None, [row], product=product)['unchanged'] == 1
    assert db.execute('SELECT COUNT(DISTINCT product_id) FROM jhs_versions').fetchone()[0] == 2
    assert db.execute('SELECT COUNT(*) FROM packs').fetchone()[0] == 0
    assert cat.stats(db)['foreign_key_errors'] == []


def test_product_import_mismatch_does_not_write_and_prefix_refresh_keeps_product(db):
    load(db, [card()])
    product = {'id': 99, 'name': '同盒号商品'}
    row = {**version(), 'pack': product}
    cat.import_pack(db, 'TEST', [version()])
    cat.import_pack(db, 'TEST', [row], product=product)
    cat.import_pack(db, 'TEST', [version()])
    assert db.execute('SELECT p.jhs_pack_id FROM jhs_versions v JOIN products p ON p.id=v.product_id').fetchone()[0] == 99
    with pytest.raises(ValueError, match='商品 ID 不一致'):
        cat.import_pack(db, None, [row], product={'id': 100, 'name': '其他商品'})
    assert db.execute('SELECT COUNT(*) FROM products WHERE jhs_pack_id=100').fetchone()[0] == 0
