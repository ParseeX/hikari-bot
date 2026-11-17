"""整组卡图单条 Markdown：真实 SDK/回调包装，模拟素材传输。"""
import asyncio
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image
from nonebot.adapters.qq.exception import ActionFailed
from nonebot.drivers import Response
from test_history_markdown import transport
from test_qq_preview import INFO, NOW, event
from hikari_bot.qq_official.card_actions import CardActions
from hikari_bot.qq_official.callbacks import CallbackController
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.qq_official.output_templates import present, notice_text, OVERRIDE, default, validate


CONFIG = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'}), accept_joined_groups=True)


def picture(index):
    stream = BytesIO()
    Image.new('RGB', (10 + index, 5), (index, 0, 0)).save(stream, format='PNG')
    return stream.getvalue()


def gallery_transport(*, reject_upload=False, reject_message=False):
    client = transport()
    names, digests = {}, {}
    async def api(request):
        path = request.url.path
        if path.endswith('/prepare'):
            number = str(len(names) + 1)
            names[number] = request.json['file_name']
            digests[number] = request.json['md5']
            return {'upload_id':number, 'presigned_url':f'https://test.cos.ap-guangzhou.myqcloud.com/{number}'}
        if path.endswith('/complete'):
            number = request.json['upload_id']
            if reject_upload and number == '2':
                raise ActionFailed(Response(400, content='{"code":40035007}'))
            assert request.json['md5'] == digests[number]
            return {'image_id':number, 'url':f'qqbot-media://image/v1/image-{number}'}
        if path.endswith('/media-images'):
            return {'images':[{'image_id':i,'file_name':n} for i,n in names.items()]}
        if path.endswith('/delete'):
            return {}
        if path.endswith('/messages'):
            if reject_message and request.json['msg_type'] == 2:
                raise ActionFailed(Response(403, content='{"code":40034141,"message":"secret"}'))
            return {'id':'reply','timestamp':NOW.isoformat()}
        raise AssertionError(path)
    async def put(request):
        assert dict(request.headers) == {'Content-Type':'image/png'}
        # 故意打乱上传完成顺序，消息仍须使用原卡图顺序。
        await asyncio.sleep((8 - int(request.url.path.strip('/'))) * .001)
        return Response(200,headers={'ETag':'"'+hashlib.md5(request.content).hexdigest()+'"'})
    client._request = AsyncMock(side_effect=api)
    client.adapter.request = AsyncMock(side_effect=put)
    return client


async def query(client, kind='c2c', *, missing=False):
    source = event('group' if kind == 'group-no-at' else kind)
    if kind == 'group-no-at':
        from nonebot.adapters.qq.event import GroupMessageCreateEvent
        source = GroupMessageCreateEvent.model_validate(source.model_dump())
    wrapper = NamedFileBot(client)
    if kind == 'c2c':
        wrapper = CallbackController(CONFIG, AsyncMock()).wrap(wrapper, source)
    action = CardActions(Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=list(range(7)))),
                         image_loader=AsyncMock(side_effect=lambda i: None if missing and i == 1 else picture(i)))
    await action.handle('卡图', '卡名', CONFIG, wrapper, source, ReplyPager(now=lambda: NOW))
    return action, source


@pytest.mark.parametrize('kind',['c2c','group','group-no-at'])
def test_all_seven_images_in_one_message_in_order(kind):
    async def run():
        client = gallery_transport()
        action, source = await query(client, kind)
        messages = [c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]
        assert len(messages) == 1
        payload = messages[0]
        assert payload['msg_type'] == 2 and payload['msg_id'] == source.id and payload['msg_seq'] == 1
        markdown = payload['markdown']
        assert markdown['force_verify_image_resource'] is True
        for i in range(7):
            assert f'![第 {i+1} 张卡图 #{10+i}px #5px](qqbot-media://image/v1/image-{i+1})' in markdown['content']
        assert markdown['content'].count('qqbot-media://') == 7 and '继续' not in markdown['content']
        assert action.result_delivery is None
        assert sum(c.args[0].method == 'DELETE' for c in client._request.call_args_list) == 7
    asyncio.run(run())


def test_missing_source_is_shown_in_place_without_hiding_other_images():
    async def run():
        client = gallery_transport()
        await query(client, missing=True)
        payloads = [c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]
        assert len(payloads) == 1
        content = payloads[0]['markdown']['content']
        assert content.count('qqbot-media://') == 6
        assert content.index('第 1 张卡图') < content.index('第 2 张卡图暂不可用') < content.index('第 3 张卡图')
    asyncio.run(run())


@pytest.mark.parametrize('failure',['upload','message'])
def test_platform_failure_never_splits_or_sends_partial_gallery(failure):
    async def run():
        client = gallery_transport(reject_upload=failure=='upload', reject_message=failure=='message')
        await query(client)
        payloads = [c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]
        assert [p['msg_type'] for p in payloads] == ([0] if failure=='upload' else [2,0])
        assert '图文未能完成发送' in payloads[-1]['content']
        assert 'secret' not in payloads[-1]['content']
    asyncio.run(run())


def test_gallery_copy_can_move_images_but_cannot_hide_them():
    async def run():
        spec = default('art.gallery')
        spec['template'] = '# {name}\n共 {total} 张\n{images}\n结束'
        token = OVERRIDE.set({'art.gallery':spec})
        try:
            client = gallery_transport()
            await query(client)
            payload = next(c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages'))
            assert payload['markdown']['content'].startswith('# 灰流丽\n共 7 张\n')
            assert payload['markdown']['content'].endswith('\n结束\n\n' + notice_text('common.card_name_hint', markdown=True))
            assert payload['markdown']['content'].count('卡名识别有误？') == 1
        finally:
            OVERRIDE.reset(token)
    asyncio.run(run())
    for body in ('没有图片','{images}{images}'):
        spec = default('art.gallery'); spec['template'] = body
        with pytest.raises(ValueError): validate('art.gallery',spec)
    spec = default('art.gallery'); spec['visible'].remove('images')
    with pytest.raises(ValueError): validate('art.gallery',spec)


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
def test_gallery_buttons_query_effect_and_price_without_name_hint(kind):
    from hikari_bot.features.card_prices.models import CardVersion
    from hikari_bot.qq_official.commands import parse_command
    from hikari_bot.qq_official.prices import PriceQueries
    from test_qq_alias_choices import source, click, messages
    async def run():
        client = gallery_transport(); client.put_interaction = AsyncMock()
        pager = ReplyPager(now=lambda: NOW)
        backend = PriceQueries(None, None, None)
        backend.comparison = Mock(versions=AsyncMock(return_value=[CardVersion(1, 'BOX-001', 'UR', INFO['jp_name'])]))
        actions = CardActions(Mock(search=Mock(return_value=INFO), image_ids=Mock(return_value=[1,2])),
                              image_loader=AsyncMock(side_effect=picture), price_backend=backend)
        async def process(proxy, event):
            command, argument = parse_command(event.get_plaintext())
            await actions.handle(command, argument, CONFIG, proxy, event, pager)
        controller = CallbackController(CONFIG, process)
        incoming = source(kind)
        await actions.handle('卡图', '卡名', CONFIG, controller.wrap(NamedFileBot(client), incoming), incoming, pager)
        first = messages(client)[-1].json
        assert first['markdown']['force_verify_image_resource'] is True
        assert first['markdown']['content'].count('qqbot-media://') == 2
        assert '卡名识别有误？' in first['markdown']['content']
        buttons = first['keyboard']['content']['rows'][0]['buttons']
        assert [b['render_data']['label'] for b in buttons] == ['查看效果', '查询卡价']
        for index, button in enumerate(buttons):
            event = click(button, kind, id=f'gallery-click-{index}')
            event.data.resolved.message_id = 'reply'
            await controller.handle(NamedFileBot(client), event)
            await asyncio.gather(*controller.tasks)
            result = messages(client)[-1].json
            assert result['msg_type'] == 2
            assert '卡名识别有误？' not in result['markdown']['content']
            assert '添加别名' not in result['markdown']['content']
            if index == 0:
                assert INFO['cn_name'] in result['markdown']['content']
                # 从效果按钮再次进入卡图，同一条图集也不带识别提示。
                art_button = result['keyboard']['content']['rows'][0]['buttons'][0]
                art_event = click(art_button, kind, id='back-to-art')
                art_event.data.resolved.message_id = 'reply'
                await controller.handle(NamedFileBot(client), art_event)
                await asyncio.gather(*controller.tasks)
                gallery = messages(client)[-1].json
                assert 'qqbot-media://' in gallery['markdown']['content']
                assert '卡名识别有误？' not in gallery['markdown']['content']
                assert gallery['keyboard']['content']['rows'][0]['buttons']
            else:
                assert result['keyboard']['content']['rows'][0]['buttons'][0]['render_data']['label'] == 'UR'
                assert '也可回复编号' not in result['markdown']['content']
        # 按钮执行上下文不能泄漏到后续手动查询。
        manual = source(kind); manual.id = 'manual-again'
        await actions.handle('效果', '卡名', CONFIG, controller.wrap(NamedFileBot(client), manual), manual, pager)
        assert '卡名识别有误？' in messages(client)[-1].json['markdown']['content']
    asyncio.run(run())
