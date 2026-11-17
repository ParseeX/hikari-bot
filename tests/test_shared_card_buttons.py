"""卡图/效果结果按钮供同群成员使用，价格选择和写操作保持原权限。"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.features.card_prices.models import CardVersion
from hikari_bot.qq_official.card_actions import CardActions, card_message, command_button, output_message
from hikari_bot.qq_official.callbacks import CallbackController
from hikari_bot.qq_official.commands import parse_command
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.qq_official.output_templates import present
from hikari_bot.qq_official.prices import PriceQueries
from test_qq_alias_choices import CONFIG, click, messages, source
from test_qq_art_markdown import gallery_transport, picture
from test_qq_preview import INFO, NOW


@pytest.mark.parametrize('kind', ['group', 'group-no-at'])
@pytest.mark.parametrize('initial,label', [('效果','查看卡图'), ('效果','查询卡价'),
                                          ('卡图','查看效果'), ('卡图','查询卡价')])
def test_each_group_member_can_query_and_owns_their_price_session(kind, initial, label):
    async def run():
        client = gallery_transport(); client.put_interaction = AsyncMock()
        backend = PriceQueries(None, None, None)
        backend.comparison = Mock(versions=AsyncMock(return_value=[CardVersion(1,'BOX','UR',INFO['jp_name'])]))
        action = CardActions(Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=[1])),
                             image_loader=AsyncMock(side_effect=picture), price_backend=backend)
        pager = ReplyPager(now=lambda: NOW)
        async def process(proxy, incoming):
            command, argument = parse_command(incoming.get_plaintext())
            await action.handle(command, argument, CONFIG, proxy, incoming, pager)
        processor = AsyncMock(side_effect=process)
        controller = CallbackController(CONFIG, processor)
        incoming = source(kind)
        await action.handle(initial, 'card', CONFIG, controller.wrap(NamedFileBot(client),incoming), incoming,pager)
        buttons = [b for row in messages(client)[0].json['keyboard']['content']['rows'] for b in row['buttons']]
        assert all(b['action']['permission'] == {'type':2} for b in buttons)
        button = next(b for b in buttons if b['render_data']['label'] == label)
        for user in ['other','only-user']:
            event = click(button,kind,id='click-'+user,group_member_openid=user)
            event.data.resolved.message_id = 'reply'
            await controller.handle(NamedFileBot(client),event)
            await asyncio.gather(*controller.tasks)
            result = messages(client)[-1].json
            assert '卡名识别有误？' not in result['markdown']['content']
            if label == '查询卡价':
                assert backend.active(('synthetic-app','group','synthetic-group',user))
                rarity = result['keyboard']['content']['rows'][0]['buttons'][0]
                assert rarity['action']['permission'] == {'type':0,'specify_user_ids':[user]}
            # 同一人的重复回调仍不重复查询，也不会消耗另一人的使用机会。
            await controller.handle(NamedFileBot(client),event.model_copy(update={'id':'repeat-'+user}))
        assert processor.await_count == 2 and len(messages(client)) == 3
        assert [call.args[1].get_user_id() for call in processor.await_args_list] == ['other','only-user']
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['other-group','private','message','expired','tampered'])
def test_shared_button_still_belongs_to_original_group_and_message(problem):
    async def run():
        client = gallery_transport(); client.put_interaction = AsyncMock()
        clock = [0]
        processor = AsyncMock()
        controller = CallbackController(CONFIG, processor,clock=lambda:clock[0])
        incoming = source('group')
        await controller.wrap(NamedFileBot(client),incoming).send(incoming,card_message(INFO,'only-user',images=True)[0])
        button = messages(client)[0].json['keyboard']['content']['rows'][0]['buttons'][0]
        event = click(button,'c2c' if problem == 'private' else 'group')
        event.data.resolved.message_id = 'reply'
        if problem == 'other-group': event.group_openid = 'other-group'
        if problem == 'message': event.data.resolved.message_id = 'wrong'
        if problem == 'expired': clock[0] = 601
        if problem == 'tampered': event.data.resolved.button_data = 'srbs:wrong'
        await controller.handle(NamedFileBot(client),event)
        if controller.tasks: await asyncio.gather(*controller.tasks)
        processor.assert_not_awaited()
    asyncio.run(run())


def test_private_query_and_write_buttons_never_become_shared():
    controller = CallbackController(CONFIG, AsyncMock())
    private,_ = controller.prepare(card_message(INFO,'only-user',images=True)[0], 'synthetic-app','only-user')
    assert private['keyboard'][0].data['keyboard'].model_dump()['content']['rows'][0]['buttons'][0]['action']['permission'] == {'type':0,'specify_role_ids':None,'specify_user_ids':['only-user']}
    # 即使误把删除按钮标为共享，服务端也只接受原有本人/管理员授权。
    undo = command_button('undo','删除该别名','撤销别名 '+'a'*32,'only-user',shared=True)
    _,handles = controller.prepare(output_message(present('effect.card'),[{'buttons':[undo]}]),'synthetic-app','only-user','synthetic-group')
    assert not controller.actions[handles[0]].shared
