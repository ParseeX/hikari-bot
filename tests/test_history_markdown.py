"""历史 Markdown 图文整条发送，真实 SDK 序列化、模拟官方传输。"""
import asyncio
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from nonebot.adapters.qq import Bot
from nonebot.drivers import Response

from test_qq_preview import bot, event, NOW
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.qq_official.output_templates import Output
from hikari_bot.qq_official.results import ResultDelivery
from hikari_bot.services.platform import Result


def picture():
    data = BytesIO()
    Image.new('RGB', (8, 4), 'white').save(data, format='PNG')
    return data.getvalue()


def transport(*, fail=None, foreign=False):
    client = bot()
    client._extract_qq_media = Bot._extract_qq_media
    client.send_to_group = lambda **kwargs: Bot.send_to_group(client, **kwargs)
    client.post_group_messages = lambda **kwargs: Bot.post_group_messages(client, **kwargs)
    client.adapter.request = AsyncMock(return_value=Response(200,
        headers={'ETag': '"' + hashlib.md5(picture()).hexdigest() + '"'}))
    state = {}

    async def request(req):
        path = req.url.path
        if path.endswith('/prepare'):
            state['filename'] = req.json['file_name']
            return {'upload_id': 'test-upload', 'presigned_url': 'https://test.cos.ap-guangzhou.myqcloud.com/test'}
        if path.endswith('/complete'):
            return {'image_id': 'test-image', 'url': 'qqbot-media://image/v1/test-token'}
        if path.endswith('/messages'):
            if fail:
                raise fail
            return {'id': 'test-reply', 'timestamp': NOW.isoformat()}
        if path.endswith('/media-images'):
            return {'images': [{'image_id': 'test-image', 'file_name': 'existing.png' if foreign else state['filename']}], 'next_cursor': ''}
        if path.endswith('/delete'):
            return {}
        raise AssertionError(path)

    client._request = AsyncMock(side_effect=request)
    return client


@pytest.mark.parametrize('kind', ['c2c', 'group', 'group-no-at'])
def test_history_markdown_and_chart_are_one_verified_message(kind):
    async def run():
        client = transport()
        source = event('group' if kind == 'group-no-at' else kind)
        if kind == 'group-no-at':
            from nonebot.adapters.qq.event import GroupMessageCreateEvent
            source = GroupMessageCreateEvent.model_validate(source.model_dump())
        text = '# 我的历史\n**胜率：75%**\n自定义顺序不改'
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(NamedFileBot(client), source, Result(
            Output(text, 'history.mycard_reports_body_2fe0148e', 'markdown'), (picture(),)))
        requests = [c.args[0] for c in client._request.call_args_list]
        messages = [r for r in requests if r.url.path.endswith('/messages')]
        assert len(messages) == 1
        payload = messages[0].json
        assert payload['msg_type'] == 2 and payload['msg_id'] == source.id and payload['msg_seq'] == 1
        assert ('/groups/' if kind.startswith('group') else '/users/') in messages[0].url.path
        assert payload['markdown'] == {'content': text + '\n\n![历史曲线 #8px #4px](qqbot-media://image/v1/test-token)',
                                       'force_verify_image_resource': True}
        assert not payload.get('media') and not payload.get('content')
        upload = client.adapter.request.call_args.args[0]
        assert dict(upload.headers) == {'Content-Type': 'image/png'}
        assert upload.content == picture()
        assert requests[-1].method == 'DELETE'
        assert not delivery.pending
    asyncio.run(run())


def test_rejected_combined_message_stays_pending_without_split_or_plain_downgrade():
    from nonebot.adapters.qq.exception import ActionFailed
    import json
    async def run():
        error = ActionFailed(Response(403, content=json.dumps({'code': 40034141, 'message': 'sensitive-error'})))
        client = transport()
        original = client._request.side_effect
        failed = False
        async def request(req):
            nonlocal failed
            if req.url.path.endswith('/messages') and req.json['msg_type'] == 2 and not failed:
                failed = True
                raise error
            return await original(req)
        client._request.side_effect = request
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(NamedFileBot(client), event(), Result(
            Output('# 历史', 'history.mycard_reports_body_2fe0148e', 'markdown'), (picture(),)))
        messages = [c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]
        assert [m['msg_type'] for m in messages] == [2, 0]
        assert '图文未能完成发送' in messages[1]['content']
        assert '40034141' not in messages[1]['content'] and 'sensitive-error' not in messages[1]['content']
        assert '平台拒绝' in messages[1]['content']
        assert delivery.pending
        source = event(); source.id = 'continue'
        await delivery.send(NamedFileBot(client), source)
        assert not delivery.pending
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['bad-etag', 'unsupported-url', 'quota'])
def test_upload_failure_does_not_send_partial_history(problem):
    async def run():
        client = transport()
        original = client._request.side_effect
        async def request(req):
            result = await original(req)
            if req.url.path.endswith('/prepare'):
                if problem == 'unsupported-url':
                    result['presigned_url'] = 'https://localhost/private'
                elif problem == 'quota':
                    from nonebot.adapters.qq.exception import ActionFailed
                    raise ActionFailed(Response(400, content='{"code":40035007,"message":"secret"}'))
            return result
        client._request.side_effect = request
        if problem == 'bad-etag':
            client.adapter.request.return_value = Response(200, headers={'ETag': 'wrong'})
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(NamedFileBot(client), event(), Result(
            Output('# 历史', 'history.mycard_reports_body_2fe0148e', 'markdown'), (picture(),)))
        messages = [c.args[0].json for c in client._request.call_args_list if c.args[0].url.path.endswith('/messages')]
        assert len(messages) == 1 and messages[0]['msg_type'] == 0
        assert '图文未能完成发送' in messages[0]['content'] and delivery.pending
        if problem == 'unsupported-url':
            client.adapter.request.assert_not_called()
    asyncio.run(run())


def test_cleanup_never_deletes_preexisting_material():
    async def run():
        client = transport(foreign=True)
        await ResultDelivery(ReplyPager()).send(NamedFileBot(client), event(), Result(
            Output('# 历史', 'history.mycard_reports_body_2fe0148e', 'markdown'), (picture(),)))
        assert not any(c.args[0].method == 'DELETE' for c in client._request.call_args_list)
    asyncio.run(run())


def test_ambiguous_send_retains_material_and_does_not_retry_automatically():
    async def run():
        client = transport()
        original = client._request.side_effect
        async def request(req):
            if req.url.path.endswith('/messages') and req.json['msg_type'] == 2:
                raise TimeoutError('sensitive-url')
            return await original(req)
        client._request.side_effect = request
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(NamedFileBot(client), event(), Result(
            Output('# 历史', 'history.mycard_reports_body_2fe0148e', 'markdown'), (picture(),)))
        calls = [c.args[0] for c in client._request.call_args_list]
        assert sum(r.json.get('msg_type') == 2 for r in calls if isinstance(r.json, dict)) == 1
        assert not any(r.method == 'DELETE' for r in calls)
        assert '发送结果未确认' in calls[-1].json['content']
        assert 'sensitive-url' not in calls[-1].json['content'] and delivery.pending
    asyncio.run(run())
