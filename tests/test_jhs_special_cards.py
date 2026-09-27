"""无卡密的实体卡保留官方 cid；商品按集换社录入，衍生物继续排除。"""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.card_catalog import catalog as cat
from hikari_bot.services.card_catalog import CardCatalog


def source():
    return {'cid': 10321, 'id': 0, 'jp_name': '伝説の白き龍',
            'en_name': 'Legendary Dragon of White', 'jp_ruby': 'レジェンダリー・ドラゴン・オブ・ホワイト'}


def rows():
    product = {'id': 10, 'name': '传说的白龙决斗套装'}
    return product, [{'id': 42, 'card_id': 5581, 'object_type': 'card', 'pack': product,
        'name_cn': '传说的白之龙', 'name_jp': '', 'aliases': ['传说的白龙'],
        'number': '2023-JPD04', 'rarity': 'SER',
        'identity': {'id': 5581, 'name_cn': '传说的白之龙', 'name_jp': '伝説の白き龍',
                     'type': 'XYZ怪兽', 'desc': '中文效果原文'}}]


def test_special_card_keeps_known_cid_and_null_passcode(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    db = cat.connect(path)
    with db:
        cat.import_cards(db, {'10321': source()}, {})
    product, entries = rows()
    cat.import_pack(db, None, entries, product=product)
    original = cat.find(db, '伝説の白き龍')[0]
    assert original['konami_cid'] == 10321
    assert original['passcode'] is None and original['temporary_id'] is None
    assert not db.execute('SELECT * FROM card_identifiers').fetchall()
    assert original['versions'][0]['products'][0]['jhs_name'] == product['name']
    assert db.execute('SELECT name_cn FROM card_catalog').fetchone()[0] == '传说的白之龙'
    with db:
        result = cat.import_cards(db, {'10321': source()}, {})
    assert result['retained_jhs_metadata'] == 1
    assert not db.execute("SELECT * FROM import_issues WHERE source='ygocdb'").fetchall()
    cat.import_pack(db, None, entries, product=product)
    assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 1
    reader = CardCatalog(path)
    reader.validate()
    for name in ['传说的白之龙', '传说的白龙', '伝説の白き龍']:
        info = reader.search(name)
        assert info['cid'] == 10321 and info['id'] == 0
        assert info['jp_name'] == '伝説の白き龍'
        assert info['text']['desc'] == '中文效果原文'
    assert reader.by_id('10321') is None  # cid 不是卡密


def test_jhs_only_card_gets_no_invented_number_and_later_keeps_identity(tmp_path):
    db = cat.connect(tmp_path / 'catalog.sqlite3')
    product, entries = rows()
    cat.import_pack(db, None, entries, product=product)
    old = cat.find(db, '伝説の白き龍')[0]
    assert old['konami_cid'] is None and old['passcode'] is None
    record = {**source(), 'id': 12345678, 'data': {'type': 0x800021, 'ot': 1, 'level': 8}}
    with db:
        cat.import_cards(db, {'10321': record}, {})
    new = cat.find(db, '12345678')[0]
    assert new['id'] == old['id'] and new['konami_cid'] == 10321
    assert new['versions'][0]['jhs_version_id'] == 42


def test_token_cannot_create_a_base_card(tmp_path):
    db = cat.connect(tmp_path / 'catalog.sqlite3')
    product, entries = rows()
    entries[0]['identity']['type'] = 'token'
    cat.import_pack(db, None, entries, product=product)
    assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 0


def test_v3_migration_keeps_ids_names_versions_and_product_links(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    schema = Path('scripts/card_catalog/schema.sql').read_text(encoding='utf-8').replace(
        'konami_cid INTEGER UNIQUE CHECK(konami_cid IS NULL OR konami_cid > 0)',
        'konami_cid INTEGER NOT NULL UNIQUE CHECK(konami_cid > 0)')
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(schema)
    db.execute("INSERT INTO catalog_meta VALUES ('schema_version','3')")
    with db:
        cat.import_cards(db, {'10321': {**source(), 'id': 12345678, 'data': {'type': 17, 'ot': 1}}}, {})
    product, entries = rows()
    cat.import_pack(db, None, entries, product=product)
    tables = ['cards', 'card_names', 'jhs_cards', 'jhs_versions', 'jhs_version_products']
    before = {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
    db.close()
    db = cat.connect(path)
    assert db.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()[0] == '4'
    assert before == {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert list((tmp_path / 'backups').glob('*.sqlite3'))
