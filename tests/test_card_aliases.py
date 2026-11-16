"""手动别名持久化、完整匹配、同名多卡和卡库更新回归。"""
import asyncio
import sqlite3

import pytest

from hikari_bot.services.card_aliases import add_alias_reply
from hikari_bot.services.card_catalog import AmbiguousCardName, CardCatalog
from scripts.card_catalog import catalog as importer
from test_catalog_service import catalog, record


def test_alias_is_persistent_idempotent_and_resolves_artwork(catalog):
    info, added = catalog.add_alias('1235', '  小白龙  ')
    assert info['id'] == 1234 and added
    assert catalog.add_alias('00001234', '小白龙')[1] is False
    reopened = CardCatalog(catalog.path)
    assert reopened.search('小白龙')['id'] == 1234
    assert reopened.image_ids('小白龙') == [1234, 1235]
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM card_names WHERE source='manual:alias'").fetchone()[0] == 1


def test_manual_alias_never_participates_in_partial_or_fuzzy_search(catalog):
    queries = ['灰', '灰莉', '灰流莉', '流灰丽', '灰·流丽']
    before = {q: catalog.search(q, fuzzy=True)['id'] for q in queries}
    # 正式卡名都不含“灰流丽”，即使这个别名很接近，也不能影响模糊结果。
    catalog.add_alias('2000', '灰流丽')
    for query in queries:
        assert catalog.search(query) is None
        assert catalog.search_many(query) == []
        assert catalog.search(query, fuzzy=True)['id'] == before[query]
    assert catalog.search('灰流丽', fuzzy=True)['id'] == 2000
    assert [c['id'] for c in catalog.search_many('灰流丽')] == [2000]


def test_alias_collision_lists_every_card_without_truncation(catalog):
    records = [record(cid, cid * 1000, f'别名目标卡{cid}') for cid in range(7, 14)]
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {str(r['cid']): r for r in records}, {})
    for row in records:
        catalog.add_alias(row['id'], '共同昵称')
    for fuzzy in [False, True]:
        with pytest.raises(AmbiguousCardName) as error:
            catalog.search('共同昵称', fuzzy=fuzzy)
        assert len(error.value.cards) == 7
        for row in records:
            assert row['cn_name'] in str(error.value)
            assert f"{row['id']:08d}" in str(error.value)
    assert len(catalog.search_many('共同昵称')) == 7


def test_alias_normalization_and_priority(catalog):
    catalog.add_alias('2000', 'ＢＥＷＤ')
    assert catalog.add_alias('2000', 'bewd')[1] is False
    assert catalog.search('be wd')['id'] == 2000
    catalog.add_alias('2000', '青眼白龙')
    assert catalog.search('青眼白龙')['id'] == 2000


def test_catalog_sync_preserves_alias_and_current_identity(catalog):
    catalog.add_alias('100000002', '我的龙')
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {'6': record(6, 6000, '正式发行卡')}, {})
    assert catalog.search('我的龙')['id'] == 6000
    assert catalog.search('我的龙')['cn_name'] == '正式发行卡'


@pytest.mark.parametrize('code,alias', [
    ('青眼白龙', '小龙'), ('99999999', '小龙'), ('9' * 40, '小龙'),
    ('9223372036854775808', '小龙'), ('1234', ''), ('1234', ' % '),
    ('1234', '１２３４'), ('1234', '12-34'), ('1234', '龙' * 129), ('1234', '小\n龙'),
])
def test_invalid_alias_or_unknown_passcode_does_not_write(catalog, code, alias):
    with pytest.raises(ValueError):
        catalog.add_alias(code, alias)
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM card_names WHERE source='manual:alias'").fetchone()[0] == 0


def test_alias_write_does_not_create_missing_database(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        CardCatalog(path).add_alias('1234', '小龙')
    assert not path.exists()


def test_alias_command_feedback(catalog):
    assert '用法' in asyncio.run(add_alias_reply(catalog, '1234'))
    assert '准确的卡密' in asyncio.run(add_alias_reply(catalog, '99999999 小龙'))
    assert '已添加别名' in asyncio.run(add_alias_reply(catalog, '1234 小龙'))
    assert '已关联' in asyncio.run(add_alias_reply(catalog, '1234 小龙'))


def test_no_minimum_similarity_or_length_for_text_queries(catalog):
    for query in ['陌', 'xyzxyz', '毫无关系的名字']:
        assert catalog.search(query, fuzzy=True) is not None
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {'7': record(7, 7000, '灰流丽')}, {})
    assert catalog.search('灰莉', fuzzy=True)['id'] == 7000
