"""删除仅影响精确卡片的手动别名，普通用户入口与重复事件回归。"""
import asyncio
import sqlite3
import subprocess
import sys

import pytest

from hikari_bot.services.card_aliases import delete_alias_reply
from hikari_bot.services.card_catalog import CardCatalog
from scripts.card_catalog import catalog as importer
from test_catalog_service import catalog, record


@pytest.mark.parametrize('target', ['青眼白龙', '蓝眼白龙', 'Blue-Eyes White Dragon',
                                    'Ｂｌｕｅ－Ｅｙｅｓ　White　Dragon', '１２３４', '1235'])
def test_delete_exact_pair_preserves_other_aliases_and_official_names(catalog, target):
    catalog.add_alias('1234', '小龙')
    catalog.add_alias('2000', '小龙')
    catalog.add_alias('1234', '另一别名')
    with catalog.connect() as db:
        expected = [tuple(r) for r in db.execute('SELECT * FROM card_names ORDER BY card_id,source,name')
                    if not (r['card_id'] == 1 and r['source'] == 'manual:alias' and r['normalized'] == '小龙')]
    assert asyncio.run(delete_alias_reply(catalog, f'{target} 小龙')) == '已删除别名：小龙 → 青眼白龙'
    reopened = CardCatalog(catalog.path)
    assert reopened.search('小龙')['id'] == 2000
    assert reopened.search('另一别名')['id'] == 1234
    with reopened.connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM card_names ORDER BY card_id,source,name')] == expected
    assert '未关联' in asyncio.run(delete_alias_reply(reopened, f'{target} 小龙'))
    catalog.add_alias('1234', '小龙')
    assert len(catalog.alias_targets('小龙')) == 2


def test_alias_normalization_and_formal_name_shadowing(catalog):
    catalog.add_alias('1234', 'ＢＥＷＤ')
    # 旧数据中已存在的正式名遮蔽仍可精确删除；新添加路径不再允许这种写入。
    with sqlite3.connect(catalog.path) as db:
        db.execute("INSERT INTO card_names VALUES (2,'und','manual:alias','青眼白龙','青眼白龙')")
    assert catalog.delete_alias('青眼白龙', 'bewd')[1]
    assert catalog.search('青眼白龙')['id'] == 2000
    assert not catalog.delete_alias('1234', '青眼白龙')[1]
    assert catalog.by_id('1234')['cn_name'] == '青眼白龙'


@pytest.mark.parametrize('name', ['青眼白龙', '蓝眼白龙', '青眼の白龍', 'Blue-Eyes White Dragon',
                                  'Ｂｌｕｅ－Ｅｙｅｓ　White　Dragon'])
def test_add_rejects_every_full_formal_name_without_modifying_data(catalog, name):
    with catalog.connect() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM card_names ORDER BY card_id,source,name')]
    for target in ['1234', '2000']:
        with pytest.raises(ValueError, match='不能将其他卡片的完整名称设置为别名。'):
            catalog.add_alias(target, name)
    with catalog.connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM card_names ORDER BY card_id,source,name')] == before


@pytest.mark.parametrize('argument', ['小龙 青眼白龙', '1234 青眼白龙', '不存在 蓝眼白龙', '2000 青眼の白龍'])
def test_reversed_arguments_get_exact_requested_error(catalog, argument):
    from hikari_bot.services.card_aliases import add_alias_reply
    assert asyncio.run(add_alias_reply(catalog, argument)) == '不能将其他卡片的完整名称设置为别名。'
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM card_names WHERE source='manual:alias'").fetchone()[0] == 0


def test_guard_allows_name_fragments_and_shared_manual_aliases(catalog):
    for name in ['青眼', '青眼白隆', '小龙']:
        assert catalog.add_alias('1234', name)[1]
        assert catalog.add_alias('2000', name)[1]
        assert len(catalog.alias_targets(name)) == 2


@pytest.mark.parametrize('target', ['青眼', '青眼白隆', '白龙青眼', '不存在的卡', '99999999', "' OR 1=1 --"])
def test_never_guesses_a_card_for_deletion(catalog, target):
    catalog.add_alias('1234', '小龙')
    assert '未找到' in asyncio.run(delete_alias_reply(catalog, f'{target} 小龙'))
    assert catalog.search('小龙')['id'] == 1234


def test_ambiguous_formal_name_refuses_deletion(catalog):
    with importer.connect(catalog.path) as db:
        importer.import_cards(db, {'7': record(7, 7000, '青眼白龙')}, {})
    catalog.add_alias('1234', '小龙'); catalog.add_alias('7000', '小龙')
    reply = asyncio.run(delete_alias_reply(catalog, '青眼白龙 小龙'))
    assert '请使用卡密删除' in reply and '00001234' in reply and '00007000' in reply
    assert len(catalog.alias_targets('小龙')) == 2


def test_delete_failure_and_missing_input(tmp_path, catalog):
    assert '用法' in asyncio.run(delete_alias_reply(catalog, '小龙'))
    assert '暂不可用' in asyncio.run(delete_alias_reply(None, '1234 小龙'))
    missing = tmp_path/'missing.sqlite3'
    assert '删除失败' in asyncio.run(delete_alias_reply(CardCatalog(missing), '1234 小龙'))
    assert not missing.exists()
    catalog.add_alias('1234', '小龙')
    with sqlite3.connect(catalog.path) as db:
        db.execute("CREATE TRIGGER no_delete BEFORE DELETE ON card_names BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    assert '删除失败' in asyncio.run(delete_alias_reply(catalog, '1234 小龙'))
    assert catalog.search('小龙')['id'] == 1234


def test_actual_official_matcher_allows_connected_users_and_rejects_foreign_app(catalog):
    script = r'''
import asyncio,sys
from pathlib import Path
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.card_catalog import CardCatalog
from test_history_markdown import transport
from test_qq_alias_choices import source,messages
catalog=CardCatalog(Path(sys.argv[1]))
config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user'}),
                  accept_joined_groups=True,catalog_path=catalog.path)
async def run():
    handler=register(config,queries=Queries(catalog)).handlers[0].call
    for kind in ['c2c','group','group-no-at']:
        catalog.add_alias('1234','小龙'); catalog.add_alias('2000','小龙')
        client=transport()
        incoming=source(kind); incoming.id='delete-'+kind; incoming.content='删除别名 青眼白龙 小龙'
        await handler(client,incoming)
        await handler(client,incoming)
        assert len(messages(client))==1
        assert messages(client)[0].json['content']=='已删除别名：小龙 → 青眼白龙'
        assert catalog.search('小龙')['id']==2000
        reversed_input=source(kind); reversed_input.id='reversed-'+kind
        reversed_input.content='添加别名 小龙 青眼白龙'
        await handler(client,reversed_input)
        assert messages(client)[-1].json['content']=='不能将其他卡片的完整名称设置为别名。'
        assert catalog.search('青眼白龙')['id']==1234
    catalog.add_alias('1234','小龙')
    client=transport(); client.self_id='foreign-app'
    incoming=source('group'); incoming.id='foreign'; incoming.content='删除别名 青眼白龙 小龙'
    await handler(client,incoming)
    assert not messages(client)
    assert len(catalog.alias_targets('小龙'))==2
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script, str(catalog.path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-1500:]
