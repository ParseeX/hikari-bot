"""本人撤销按钮、旧按钮隔离及真实官方处理器接线。"""
import asyncio
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from hikari_bot.services.card_aliases import add_alias_reply, AliasAdded
from hikari_bot.qq_official.alias_undo import send_added, undo_handler
from hikari_bot.qq_official.callbacks import CallbackController
from hikari_bot.qq_official.media import NamedFileBot
from test_catalog_service import catalog
from test_history_markdown import transport
from test_qq_alias_choices import CONFIG, source, click, messages


def test_receipt_only_deletes_original_generation(catalog):
    first, _ = catalog.add_alias('1234', '小龙')
    catalog.add_alias('2000', '小龙')
    # 即使另一入口直接执行原 SQL，触发器也会撤销旧代次。
    with sqlite3.connect(catalog.path) as db:
        db.execute("DELETE FROM card_names WHERE card_id=1 AND source='manual:alias'")
    second, _ = catalog.add_alias('1234', '小龙')
    assert catalog.undo_alias(first['alias_receipt']) is None
    assert len(catalog.alias_targets('小龙')) == 2
    assert catalog.undo_alias(second['alias_receipt'])[0]['id'] == 1234
    assert catalog.undo_alias(second['alias_receipt']) is None
    assert catalog.search('小龙')['id'] == 2000


def test_duplicate_add_never_grants_undo(catalog):
    assert isinstance(asyncio.run(add_alias_reply(catalog, '1234 小龙', with_receipt=True)), AliasAdded)
    repeated = asyncio.run(add_alias_reply(catalog, '1234 小龙', with_receipt=True))
    assert type(repeated) is str and '已关联' in repeated
    assert type(asyncio.run(add_alias_reply(catalog, '1234 旧入口'))) is str


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
@pytest.mark.parametrize('attempt', ['owner', 'other', 'other-group', 'message', 'expired', 'recreated'])
def test_button_owner_scope_lifetime_and_single_use(catalog, kind, attempt):
    async def run():
        added = await add_alias_reply(catalog, '1234 小龙', with_receipt=True)
        catalog.add_alias('2000', '小龙')
        client = transport(); client.put_interaction = AsyncMock()
        clock = [0]
        controller = CallbackController(CONFIG, AsyncMock(), undo_handler=undo_handler(catalog), clock=lambda: clock[0])
        incoming = source(kind)
        await send_added(controller.wrap(NamedFileBot(client), incoming), incoming, added)
        payload = messages(client)[0].json
        assert payload['markdown']['content'] == '已添加别名：小龙 → 青眼白龙'
        button = payload['keyboard']['content']['rows'][0]['buttons'][0]
        assert button['render_data']['label'] == '删除该别名'
        assert button['action']['permission'] == {'type': 0, 'specify_user_ids': ['only-user']}
        event = click(button, kind)
        if attempt == 'other':
            if kind == 'c2c': event.user_openid = 'other'
            else: event.group_member_openid = 'other'
        elif attempt == 'other-group':
            event = click(button, 'group', group_openid='other-group')
        elif attempt == 'message': event.data.resolved.message_id = 'foreign-message'
        elif attempt == 'expired': clock[0] = 601
        elif attempt == 'recreated':
            catalog.delete_alias('1234', '小龙'); catalog.add_alias('1234', '小龙')
        await controller.handle(NamedFileBot(client), event)
        await asyncio.gather(*controller.tasks)
        if attempt == 'owner':
            assert catalog.search('小龙')['id'] == 2000
            assert messages(client)[-1].json['content'] == '已删除别名：小龙 → 青眼白龙'
            assert len(messages(client)) == 2
            # 重建后再点同一个已使用按钮，也不得再次删除。
            catalog.add_alias('1234', '小龙')
            await controller.handle(NamedFileBot(client), click(button, kind, id='second-click'))
            await asyncio.gather(*controller.tasks)
        assert len(catalog.alias_targets('小龙')) == 2
        controller.processor.assert_not_awaited()
    asyncio.run(run())


def test_button_rejection_preserves_success_and_offers_manual_delete(catalog):
    async def run():
        added = await add_alias_reply(catalog, '1234 小龙', with_receipt=True)
        client = transport(); original = client._request.side_effect
        async def reject(request):
            if request.json and request.json.get('keyboard'): raise RuntimeError('synthetic rejection')
            return await original(request)
        client._request.side_effect = reject
        controller = CallbackController(CONFIG, AsyncMock(), undo_handler=undo_handler(catalog))
        incoming = source('group')
        await send_added(controller.wrap(NamedFileBot(client), incoming), incoming, added)
        assert catalog.search('小龙')['id'] == 1234
        assert not controller.actions
        assert messages(client)[-1].json['content'] == '已添加别名：小龙 → 青眼白龙\n删除按钮发送失败，可用：删除别名 00001234 小龙'
    asyncio.run(run())


def test_actual_matcher_uses_undo_callback_but_never_user_typed_internal_command(catalog):
    script = r'''
import asyncio,sys
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.card_catalog import CardCatalog
from test_history_markdown import transport
from test_qq_alias_choices import source,click,messages
controllers=[]; factory=plugin.CallbackController
def create(*a,**kw):
    result=factory(*a,**kw);controllers.append(result);return result
plugin.CallbackController=create
catalog=CardCatalog(Path(sys.argv[1]))
config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user'}),accept_joined_groups=True,catalog_path=catalog.path)
async def run():
    handler=plugin.register(config,queries=Queries(catalog)).handlers[0].call
    controller=controllers[0]
    for kind in ['c2c','group','group-no-at']:
        client=transport();client.put_interaction=AsyncMock()
        incoming=source(kind);incoming.id='add-'+kind;incoming.content='添加别名 青眼白龙 小龙'
        await handler(client,incoming)
        button=messages(client)[0].json['keyboard']['content']['rows'][0]['buttons'][0]
        forged=source(kind); forged.id='forged-'+kind
        forged.content=controller.actions[button['action']['data']].command
        await handler(client,forged)
        assert catalog.search('小龙')['id']==1234
        await controller.handle(client,click(button,kind,id='undo-'+kind))
        await asyncio.gather(*controller.tasks)
        assert not catalog.alias_targets('小龙')
        assert messages(client)[-1].json['content']=='已删除别名：小龙 → 青眼白龙'
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script, str(catalog.path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-1500:]
