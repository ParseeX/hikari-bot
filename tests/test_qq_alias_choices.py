"""同名别名按钮、群/私聊回调、会话隔离与实际查询接线。"""
import asyncio
from datetime import datetime, timezone, timedelta
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import pytest
from nonebot.adapters.qq.event import GroupMessageCreateEvent, InteractionCreateEvent

from hikari_bot.qq_official.alias_choices import AliasChoices, PROMPT
from hikari_bot.qq_official.callbacks import CallbackController
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.media import NamedFileBot
from test_catalog_service import catalog
from test_history_markdown import transport
from test_qq_preview import event


CONFIG = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user', 'other'}),
                     groups=frozenset({'synthetic-group', 'other-group'}))


def source(kind):
    value = event('group' if kind == 'group-no-at' else kind)
    if kind == 'group-no-at':
        value = GroupMessageCreateEvent.model_validate(value.model_dump())
    return value


def messages(client):
    return [c.args[0] for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]


def click(button, kind='c2c', **updates):
    data = dict(id='interaction', event_id='outer-event', version=1, type=11,
                application_id='synthetic-app', timestamp=datetime.now(timezone.utc).isoformat(),
                chat_type=2 if kind == 'c2c' else 1, scene='c2c' if kind == 'c2c' else 'group',
                data={'resolved': {'button_id': button['id'], 'button_data': button['action']['data'],
                                   'message_id': 'test-reply'}})
    if kind == 'c2c':
        data['user_openid'] = 'only-user'
    else:
        data.update(group_openid='synthetic-group', group_member_openid='only-user')
    data.update(updates)
    return InteractionCreateEvent.model_validate(data)


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
@pytest.mark.parametrize('command,argument,expected', [
    ('效果查询', '小龙', '效果 00001234'), ('查卡图', '小龙', '卡图 00001234'),
    ('卡密', '小龙', '卡密 00001234'), ('共界计算', '小龙', '共界计算 00001234'),
    ('日版卡价查询', '小龙 UR LOB', '日版价格查询 00001234 UR LOB'),
])
def test_choice_uses_chinese_names_and_preserves_query_without_ack_text(catalog, kind, command, argument, expected):
    async def run():
        catalog.add_alias('1234', '小龙'); catalog.add_alias('2000', '小龙')
        client = transport(); client.put_interaction = AsyncMock()
        async def process(proxy, chosen):
            assert chosen.get_plaintext() == expected
            await proxy.send(chosen, '查询结果')
        processor = AsyncMock(side_effect=process)
        controller = CallbackController(CONFIG, processor)
        incoming = source(kind)
        chooser = AliasChoices(catalog)
        assert await chooser.handle(controller.wrap(NamedFileBot(client), incoming), incoming, command, argument)
        payload = messages(client)[0].json
        assert payload['msg_type'] == 2 and payload['markdown']['content'] == PROMPT
        buttons = payload['keyboard']['content']['rows'][0]['buttons']
        assert [b['render_data']['label'] for b in buttons] == ['青眼白龙', '相同种族属性']
        permission = {'type': 2} if kind == 'c2c' else {'type': 0, 'specify_user_ids': ['only-user']}
        assert all(b['action']['permission'] == permission for b in buttons)
        interaction = click(buttons[0], kind)
        await controller.handle(NamedFileBot(client), interaction)
        await controller.handle(NamedFileBot(client), interaction)
        await asyncio.gather(*controller.tasks)
        processor.assert_awaited_once()
        client.put_interaction.assert_awaited_once_with(interaction_id='interaction', code=0)
        sent = messages(client)
        assert len(sent) == 2
        assert sent[1].json['content'] == '查询结果'
        assert sent[1].json['event_id'] == 'outer-event' and not sent[1].json.get('msg_id')
        assert sent[1].json['msg_seq'] == 1
        assert ('/users/' if kind == 'c2c' else '/groups/') in sent[1].url.path
        assert not sent[1].json.get('message_reference')
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['user', 'group', 'private', 'app', 'expired', 'tampered'])
def test_foreign_or_expired_group_button_cannot_run_query(catalog, problem):
    async def run():
        catalog.add_alias('1234', '小龙'); catalog.add_alias('2000', '小龙')
        client = transport(); client.put_interaction = AsyncMock()
        processor = AsyncMock()
        controller = CallbackController(CONFIG, processor)
        incoming = source('group')
        await AliasChoices(catalog).handle(controller.wrap(NamedFileBot(client), incoming), incoming, '效果', '小龙')
        button = messages(client)[0].json['keyboard']['content']['rows'][0]['buttons'][0]
        changes = {}
        if problem == 'user': changes['group_member_openid'] = 'other'
        if problem == 'group': changes['group_openid'] = 'other-group'
        if problem == 'app': changes['application_id'] = 'foreign'
        if problem == 'expired': changes['timestamp'] = (datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()
        if problem == 'tampered': button['action']['data'] = 'srbs:fake'
        await controller.handle(NamedFileBot(client), click(button, 'c2c' if problem == 'private' else 'group', **changes))
        if controller.tasks: await asyncio.gather(*controller.tasks)
        processor.assert_not_awaited()
    asyncio.run(run())


def test_all_candidates_can_be_reached_by_buttons_without_truncating():
    async def run():
        cards = [{'id': i, 'cn_name': f'测试卡{i}'} for i in range(1, 31)]
        chooser = AliasChoices(Mock(alias_targets=Mock(return_value=cards)))
        client = transport(); client.put_interaction = AsyncMock()
        async def process(proxy, selected):
            command, argument = selected.get_plaintext().split(' ', 1)
            assert await chooser.handle(proxy, selected, command, argument)
        controller = CallbackController(CONFIG, process)
        incoming = source('group')
        await chooser.handle(controller.wrap(NamedFileBot(client), incoming), incoming, '卡图', '别名')
        found = []
        for page in range(4):
            keyboard = messages(client)[-1].json['keyboard']['content']['rows']
            assert len(keyboard) <= 5 and all(len(row['buttons']) <= 5 for row in keyboard)
            buttons = [b for row in keyboard for b in row['buttons']]
            found.extend(b['render_data']['label'] for b in buttons if not b['id'].startswith('alias_page_'))
            if page < 3:
                next_button = next(b for b in buttons if b['render_data']['label'] == '下一页')
                await controller.handle(NamedFileBot(client), click(next_button, 'group', id=f'next-{page}'))
                await asyncio.gather(*controller.tasks)
        assert found == [f'测试卡{i}' for i in range(1, 31)]
    asyncio.run(run())


def test_names_are_not_silently_shortened_and_failed_buttons_are_unusable():
    async def run():
        name = '这是一张中文名字超过十个字的卡片'
        cards = [{'id': 1, 'cn_name': name}, {'id': 2, 'cn_name': '另一张卡'}]
        chooser = AliasChoices(Mock(alias_targets=Mock(return_value=cards)))
        controller = CallbackController(CONFIG, AsyncMock())
        client = transport()
        original = client._request.side_effect
        async def reject_keyboard(request):
            if request.json and request.json.get('keyboard'):
                assert request.json['keyboard']['content']['rows'][0]['buttons'][0]['render_data']['label'] == name
                raise RuntimeError('synthetic rejection')
            return await original(request)
        client._request.side_effect = reject_keyboard
        incoming = source('group')
        assert await chooser.handle(controller.wrap(NamedFileBot(client), incoming), incoming, '效果', '别名')
        assert not controller.actions
        assert messages(client)[-1].json['content'] == '选择按钮发送未成功，请重新查询。'
    asyncio.run(run())


def test_fuzzy_alias_collisions_open_choices_but_formal_names_and_writes_do_not(catalog):
    async def run():
        catalog.add_alias('1234', '小龙'); catalog.add_alias('2000', '小龙')
        chooser = AliasChoices(catalog)
        bot = AsyncMock()
        for command, argument in [('效果', '青眼白龙'), ('添加别名', '小龙 新别名')]:
            assert not await chooser.handle(bot, source('c2c'), command, argument)
        bot.send.assert_not_awaited()
        for query in ['小', '小隆']:
            assert await chooser.handle(bot, source('c2c'), '效果', query)
        assert bot.send.await_count == 2
    asyncio.run(run())


def test_plugin_wires_choices_to_actual_effect_image_and_passcode_queries(catalog):
    catalog.add_alias('1234', '小龙'); catalog.add_alias('2000', '小龙')
    script = r'''
import asyncio,sys
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.qq_official.config import PilotConfig
from test_history_markdown import picture,transport
from test_qq_alias_choices import source,click,messages
controllers=[]; factory=plugin.CallbackController
def controller(*args,**kwargs):
    value=factory(*args,**kwargs); controllers.append(value); return value
plugin.CallbackController=controller
catalog=CardCatalog(Path(sys.argv[1]))
config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user'}),
                  accept_joined_groups=True,catalog_path=catalog.path)
async def run():
    handler=plugin.register(config,queries=Queries(catalog),image_loader=AsyncMock(return_value=picture())).handlers[0].call
    controller=controllers[0]
    for kind in ['c2c','group','group-no-at']:
        for command in ['效果','卡图','卡密','共界计算']:
            client=transport();client.put_interaction=AsyncMock()
            query={'c2c':'小龙','group':'小','group-no-at':'小隆'}[kind]
            incoming=source(kind);incoming.id=f'{kind}-{command}';incoming.content=command+' '+query
            await handler(client,incoming)
            first=messages(client)[0].json
            button=first['keyboard']['content']['rows'][0]['buttons'][0]
            from hikari_bot.qq_official.media import NamedFileBot
            await controller.handle(NamedFileBot(client),click(button,kind,id=f'click-{kind}-{len(controller.seen)}'))
            await asyncio.gather(*controller.tasks)
            sent=messages(client)
            assert len(sent)==2,(kind,command,[r.json for r in sent])
            result=sent[1].json
            if command=='效果': assert '青眼白龙' in result['markdown']['content'] and '原文效果' in result['markdown']['content']
            elif command=='卡图': assert result['markdown']['content'].count('qqbot-media://')==2
            elif command=='卡密': assert '00001234' in result['content']
            else: assert '2000' in result['content']
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script, str(catalog.path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2500:]
