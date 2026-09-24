"""主库切换的身份兼容、资料读取及旧命令入口回归。"""
import asyncio
import random
import sqlite3

import pytest

from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.services import ygocard
from scripts.card_catalog import catalog as importer


def record(cid, code, name, *, type_code=17, atk=3000, race=8192, attribute=16):
    return {'cid': cid, 'id': code, 'cn_name': name, 'sc_name': name,
            'cnocg_n': '蓝眼白龙' if cid == 1 else name,
            'jp_name': '青眼の白龍' if cid == 1 else name,
            'en_name': 'Blue-Eyes White Dragon' if cid == 1 else name,
            'jp_ruby': 'ブルーアイズ',
            'text': {'types': '[怪兽|通常]', 'desc': '原文效果', 'pdesc': ''},
            'data': {'type': type_code, 'ot': 11, 'atk': atk, 'def': 2500,
                     'race': race, 'attribute': attribute, 'level': 8}}


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    records = [record(1, 1234, '青眼白龙'),
               record(2, 2000, '相同种族属性', atk=2000),
               record(3, 3000, '只有属性相同', atk=2000, race=1),
               record(4, 4000, '不能入库的衍生物', type_code=0x4001),
               record(5, 5000, '魔法卡', type_code=2, race=0, attribute=0),
               record(6, 100000002, '待发行卡')]
    with importer.connect(path) as db:
        importer.import_cards(db, {str(r['cid']): r for r in records}, {'100000001': 1234})
        db.execute("INSERT INTO card_artwork_ids VALUES (1235,1,'mc:alias')")
    return CardCatalog(path)


def test_all_names_and_partial_search(catalog):
    for name in ['青眼白龙', '蓝眼白龙', '青眼の白龍', 'Ｂｌｕｅ－Ｅｙｅｓ White Dragon', '青眼']:
        assert catalog.search(name)['id'] == 1234
    assert catalog.search('%') is None
    assert catalog.search('_') is None
    assert catalog.search(' ') is None
    assert catalog.search('不存在的卡') is None
    assert catalog.search('不能入库的衍生物') is None


def test_identifiers_are_exact_and_not_internal_ids(catalog):
    for value in ['00001234', '1234', '100000001', '1235']:
        info = catalog.by_id(value)
        assert info['id'] == 1234
        assert info['passcode'] == '00001234'
        assert info['cid'] == 1
    assert catalog.by_id(1236) is None
    assert catalog.by_id(1) is None
    assert catalog.by_id(0) is None
    assert catalog.by_id('word') is None
    assert catalog.by_id(100000002)['id'] == 100000002


def test_effect_and_stats_contract(catalog):
    info = catalog.search('蓝眼白龙')
    assert info['text'] == {'types': '[怪兽|通常]', 'desc': '原文效果', 'pdesc': ''}
    assert info['data']['atk'] == 3000
    assert info['data']['type'] == 17
    assert info['jp_ruby'] == 'ブルーアイズ'
    assert info['sc_name'] == '青眼白龙'


def test_images_require_known_artwork_mapping(catalog):
    assert catalog.image_id('青眼白龙', 1) == 1235
    assert catalog.image_id('青眼白龙', 2) is None
    assert catalog.image_id('青眼白龙', 0) == 1234
    assert catalog.image_id('1235') == 1235
    assert catalog.image_id('100000001') == 1234
    assert catalog.image_id('100000002') == 100000002
    assert catalog.image_id('4000') is None


def test_random_and_calculator(catalog):
    state = random.getstate()
    assert catalog.random_id(42) == catalog.random_id(42)
    assert random.getstate() == state
    assert all(catalog.random_id(i) != 4000 for i in range(50))
    assert catalog.metaltronus(1234) == [2000, 100000002]
    assert catalog.metaltronus(1235) == catalog.metaltronus(1234)
    assert catalog.metaltronus(5000) == []


def test_queries_are_readonly_and_missing_path_not_created(catalog, tmp_path):
    catalog.validate()
    with catalog.connect() as db:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM cards')
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        CardCatalog(missing).search('青眼')
    assert not missing.exists()


def test_artwork_import_checks_alias_names_and_existing_passcodes(catalog, tmp_path):
    source = tmp_path / 'mc.cdb'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE datas(id,alias,type); CREATE TABLE texts(id,name);')
        db.executemany('INSERT INTO datas VALUES (?,?,?)', [(1234, 0, 17), (1236, 1234, 17),
                       (1237, 1234, 17), (1238, 1234, 0x4001), (2000, 1234, 17)])
        db.executemany('INSERT INTO texts VALUES (?,?)', [(1234, '原卡'), (1236, '原卡'),
                       (1237, '规则同名的另一张卡'), (1238, '原卡'), (2000, '原卡')])
    with importer.connect(catalog.path) as db:
        assert importer.import_artwork_ids(db, source)['inserted'] == 1
        assert importer.import_artwork_ids(db, source)['unchanged'] == 1
    assert catalog.by_id(1236)['id'] == 1234
    assert catalog.by_id(1237) is None
    assert catalog.by_id(1238) is None
    assert catalog.by_id(2000)['id'] == 2000


def test_bot_facade_uses_catalog_without_http(catalog, monkeypatch):
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    def no_http(*args, **kwargs):
        raise AssertionError('卡片资料不应再发起 HTTP 查询')
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', no_http)
    assert asyncio.run(ygocard.get_card_info('蓝眼白龙'))['id'] == 1234
    assert asyncio.run(ygocard.get_card_info_by_id('1235'))['id'] == 1234
    assert asyncio.run(ygocard.resolve_card_image('青眼白龙', 1)) == 1235
    assert asyncio.run(ygocard.get_card_info('未知')) is None
    assert ygocard.metaltronus_calc(1234) == [2000, 100000002]


@pytest.mark.parametrize('returncode', [0, 1])
def test_update_command_targets_catalog_and_reports_failure(catalog, monkeypatch, returncode):
    monkeypatch.setattr(ygocard, 'catalog', catalog)
    calls = []
    class Process:
        async def communicate(self):
            return b'{}', b'upstream error'
    process = Process()
    process.returncode = returncode
    async def create(*args, **kwargs):
        calls.append(args)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    if returncode:
        with pytest.raises(RuntimeError, match='主库同步失败'):
            asyncio.run(ygocard.update_cdb())
    else:
        asyncio.run(ygocard.update_cdb())
    assert calls[0][-3:] == ('--db', str(catalog.path), 'sync-base')
