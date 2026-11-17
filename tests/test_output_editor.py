"""文案后台的访问隔离、发布事务与真实业务输出回归，不发送 QQ 消息。"""
import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from hikari_bot.qq_official import output_templates as templates
from hikari_bot.qq_official.editor_web import create_app, COOKIE
from hikari_bot.qq_official.output_templates import Store, SCHEMA, default, present, Output
from hikari_bot.services.mycard_archive.admin_auth import AdminAuth, password_record
from hikari_bot.services.platform import Result
from hikari_bot.services.wording import using_wording, NoticeText

ORIGIN = 'https://bot.xyk.one'
PASSWORD = 'synthetic-editor-password'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'editor'
    root.mkdir()
    password = tmp_path / 'mycard-password.json'
    password.write_text(json.dumps(password_record(PASSWORD)))
    auth = AdminAuth(root)
    auth.password_path = password
    store = Store(tmp_path)
    monkeypatch.setattr(templates, 'HOME', tmp_path)
    client = TestClient(create_app(auth=auth, store=store), base_url=ORIGIN)
    return client, store, auth


def post(client, path, body):
    return client.post('/api/' + path, json=body, headers={'Origin': ORIGIN})


def login(client):
    response = post(client, 'login', {'password': PASSWORD})
    assert response.status_code == 200
    return response


def test_password_reuse_sessions_independent_and_rotation_revokes(setup, tmp_path):
    client, store, auth = setup
    response = login(client)
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ['Secure', 'HttpOnly', 'SameSite=strict', 'Path=/'])
    token = client.cookies.get(COOKIE)
    old_root = tmp_path / 'original-admin'
    old_root.mkdir()
    original = AdminAuth(old_root)
    original.password_path = auth.password_path
    assert not original.valid(token)
    assert client.get('/api/templates').status_code == 200
    auth.password_path.write_text(json.dumps(password_record('synthetic-changed-password')))
    assert client.get('/api/templates').status_code == 401
    assert not original.db_path.exists()


def test_auth_csrf_limits_and_no_secret_echo(setup):
    client, store, auth = setup
    assert client.get('/api/templates').status_code == 401
    assert client.post('/api/login', json={'password': PASSWORD}).status_code == 403
    assert client.post('/api/login', json={'password': PASSWORD}, headers={'Origin': 'https://ygo.xyk.one'}).status_code == 403
    response = post(client, 'login', {'password': PASSWORD, 'unexpected': PASSWORD})
    assert response.status_code == 422 and PASSWORD not in response.text
    for _ in range(5):
        assert post(client, 'login', {'password': 'wrong'}).status_code == 401
    assert post(client, 'login', {'password': PASSWORD}).status_code == 429
    assert not store.path.exists()


def test_request_size_and_public_assets(setup):
    client, _, _ = setup
    response = client.post('/api/login', content=b'x' * 70000, headers={'Origin': ORIGIN})
    assert response.status_code == 413
    response = client.get('/')
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/assets/editor.js').status_code == 200
    assert client.get('/assets/output-templates.json').status_code == 404
    assert client.get('/docs').status_code == 404


def test_draft_publish_rollback_and_stale_tab_are_separate(setup):
    client, store, _ = setup
    login(client)
    key = 'binding.business_758d5462'
    spec = default(key)
    spec['template'] = '已绑定。'
    response = post(client, 'change', dict(operation='save', feature='binding', key=key, spec=spec, revision=0))
    assert response.status_code == 200
    assert present(key, {'player': '示例玩家A'}) == '已成功绑定用户名 示例玩家A。'
    assert post(client, 'change', dict(operation='publish', feature='binding', revision=0)).status_code == 409
    assert post(client, 'change', dict(operation='publish', feature='binding', revision=1)).status_code == 200
    assert present(key) == '已绑定。'
    assert post(client, 'change', dict(operation='rollback', feature='binding', revision=2)).status_code == 200
    assert present(key, {'player': '示例玩家A'}) == '已成功绑定用户名 示例玩家A。'
    assert client.get('/api/templates').json()['session'] == {}
    post(client, 'logout', {})
    assert client.get('/api/templates').status_code == 401


def test_invalid_template_and_wrong_feature_cannot_change_storage(setup):
    client, store, _ = setup
    login(client)
    key = 'binding.business_758d5462'
    for value in ['{password}', '{content.__class__}', '{name!r}', '{']:
        spec = default(key)
        spec['template'] = value
        assert post(client, 'change', dict(operation='save', feature='binding', key=key, spec=spec, revision=0)).status_code == 400
    assert post(client, 'change', dict(operation='save', feature='history', key=key, spec=default(key), revision=0)).status_code == 400
    assert store.load()['revision'] == 0
    assert post(client, 'change', dict(operation='save', feature='binding', key=key, spec={'template':'x'}, revision=0)).status_code == 400


def test_competing_writers_only_one_can_publish_from_same_revision(setup):
    _, store, _ = setup
    key = 'binding.business_758d5462'
    def save(text):
        spec = default(key)
        spec['template'] = text
        try:
            store.mutate('save', 'binding', key, spec, 0)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(save, ['甲', '乙'])) == [False, True]
    assert store.load()['revision'] == 1


def test_web_preview_is_draft_only_and_never_queues_qq(setup):
    client, store, _ = setup
    login(client)
    key = 'binding.business_4c0fc3fa'
    spec = default(key)
    spec.update(template='# 已绑定\n**{player}**', mode='markdown')
    response = post(client, 'preview', {'key': key, 'spec': spec})
    assert response.status_code == 200 and response.json()['mode'] == 'markdown'
    assert response.json()['text'].startswith('# 已绑定')
    assert not store.path.exists() and not store.queue.exists()


def test_qq_preview_requires_explicit_action_auth_and_current_revision(setup):
    client, store, _ = setup
    key = 'binding.business_758d5462'
    assert post(client, 'qq-preview', {'key': key, 'revision': 0}).status_code == 401
    login(client)
    assert post(client, 'qq-preview', {'key': key, 'revision': 99}).status_code == 409
    response = post(client, 'qq-preview', {'key': key, 'revision': 0})
    assert response.status_code == 200
    value = response.json()
    assert value['status'] == 'awaiting_session'
    assert client.get('/api/qq-preview/' + value['id']).json() == value
    assert client.get('/api/qq-preview/session').status_code == 404
    assert not store.path.exists()


def test_editing_history_paragraph_reaches_official_only(setup, monkeypatch):
    from hikari_bot.services.mycard_reports import history_report
    from hikari_bot.services import mycard
    _, store, _ = setup
    monkeypatch.setattr(mycard, 'mycard_get_records', AsyncMock(return_value=[]))
    key = 'history.mycard_reports_e55ebb30'
    spec = default(key)
    spec['template'] = '玩家 {user_id}，本月 {value4} 场'
    store.mutate('save', 'history', key, spec, 0)
    store.mutate('publish', 'history', revision=1)
    async def run():
        original = await history_report('sample', 11, 2026)
        with using_wording(present):
            official = await history_report('sample', 11, 2026)
        after = await history_report('sample', 11, 2026)
        assert official.text == '玩家 sample，本月 0 场'
        assert '月场次：0' in original.text and original == after
    asyncio.run(run())


def test_markdown_queue_and_group_send_preserve_mode():
    from hikari_bot.services.official_bus import encode, decode
    from hikari_bot.qq_official.media import NamedFileBot, output_message
    result = decode(encode(Result(Output('# 测试', 'synthetic', 'markdown'))))
    assert result.text.mode == 'markdown'
    assert output_message(result.text)['markdown'][0].data['markdown'].content == '# 测试'
    bot = SimpleNamespace(send=AsyncMock())
    asyncio.run(NamedFileBot(bot).send(object(), result.text))
    assert bot.send.call_args.args[1]['markdown']


@pytest.mark.parametrize('mode', ['text', 'markdown'])
@pytest.mark.parametrize('kind', ['group_at', 'group', 'c2c', 'long_c2c', 'long_group_at', 'long_group'])
def test_published_effect_format_reaches_actual_sdk(setup, mode, kind):
    from copy import deepcopy
    from test_qq_preview import INFO, NOW, bot, event
    from nonebot.adapters.qq import Bot
    from nonebot.adapters.qq.event import GroupMessageCreateEvent
    from hikari_bot.qq_official.card_actions import CardActions, card_text
    from hikari_bot.qq_official.config import PilotConfig
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.media import NamedFileBot
    from unittest.mock import Mock
    _, store, _ = setup
    spec = default('effect.card')
    spec.update(template='# 自定义标题\n{body}', mode=mode)
    store.mutate('save', 'effect', 'effect.card', spec, 0)
    store.mutate('publish', 'effect', revision=1)
    info = deepcopy(INFO)
    if kind.startswith('long_'):
        info['text']['desc'] = '完整效果。' * 700
    client = bot()
    async def post(**kwargs):
        return await Bot.post_group_messages(client, **kwargs)
    async def group(**kwargs):
        return await Bot.send_to_group(client, **kwargs)
    client.post_group_messages, client.send_to_group = post, group
    source = event('c2c' if kind in {'c2c', 'long_c2c'} else 'group')
    if kind in {'group', 'long_group'}:
        source = GroupMessageCreateEvent.model_validate(source.model_dump())
    config = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'}), groups=frozenset({'synthetic-group'}))
    asyncio.run(CardActions(Mock(search=Mock(return_value=info))).handle(
        '效果', '卡名', config, NamedFileBot(client), source, ReplyPager(now=lambda: NOW)))
    payloads = [call.args[0].json for call in client._request.call_args_list]
    assert payloads and all(p['msg_type'] == (2 if mode == 'markdown' else 0) for p in payloads)
    content = ''.join(p['markdown']['content'] if mode == 'markdown' else p['content'] for p in payloads)
    assert content == card_text(info)
    assert all('keyboard' not in p for p in payloads)
    assert [key for key, value in store.state()['states'].items() if value['feature'] == 'effect'] == ['effect.card']


def test_plain_card_selection_does_not_send_rich_message_with_buttons(setup):
    from copy import deepcopy
    from test_qq_preview import INFO, NOW, bot, event
    from hikari_bot.qq_official.card_actions import CardActions
    from hikari_bot.qq_official.config import PilotConfig
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.media import NamedFileBot
    from unittest.mock import Mock
    _, store, _ = setup
    spec = default('effect.card')
    spec.update(template='{name}\n{pendulum}{effect}', mode='text')
    store.mutate('save', 'effect', 'effect.card', spec, 0)
    store.mutate('publish', 'effect', revision=1)
    client = bot()
    config = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'}))
    info = deepcopy(INFO)
    info['text']['pdesc'] = '原文 * 与 #'
    asyncio.run(CardActions(Mock(search=Mock(return_value=info)), image_loader=AsyncMock()).handle(
        '效果', '卡名', config, NamedFileBot(client), event(), ReplyPager(now=lambda: NOW)))
    payload = client._request.call_args.args[0].json
    assert payload['msg_type'] == 0 and 'keyboard' not in payload
    assert payload['content'] == INFO['cn_name'] + '\n原文 * 与 #\n---------------\n' + INFO['text']['desc'] + '\n\n' + str(present('common.card_name_hint'))


@pytest.mark.parametrize('kind', ['group', 'c2c'])
def test_effect_markdown_rejection_never_switches_template_or_format(setup, kind):
    from test_qq_preview import INFO, NOW, bot, event
    from nonebot.adapters.qq import Bot
    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.drivers import Response
    from hikari_bot.qq_official.card_actions import CardActions
    from hikari_bot.qq_official.config import PilotConfig
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.media import NamedFileBot
    from unittest.mock import Mock
    _, store, _ = setup
    spec = default('effect.card'); spec.update(template='# 同一份\n{effect}', mode='markdown')
    store.mutate('save', 'effect', 'effect.card', spec, 0)
    store.mutate('publish', 'effect', revision=1)
    async def run():
        client = bot()
        async def post(**kwargs): return await Bot.post_group_messages(client, **kwargs)
        async def group(**kwargs): return await Bot.send_to_group(client, **kwargs)
        client.post_group_messages, client.send_to_group = post, group
        client._request.side_effect = [ActionFailed(Response(403, content=json.dumps({'code': 304003}))),
            {'id': 'notice', 'timestamp': NOW.isoformat()}, {'id': 'retried', 'timestamp': NOW.isoformat()}]
        pager = ReplyPager(now=lambda: NOW)
        config = PilotConfig('synthetic-app', 'fake', users=frozenset({'only-user'}), groups=frozenset({'synthetic-group'}))
        wrapped = NamedFileBot(client)
        await CardActions(Mock(search=Mock(return_value=INFO)), image_loader=AsyncMock()).handle(
            '效果', '卡名', config, wrapped, event(kind), pager)
        payloads = [call.args[0].json for call in client._request.call_args_list]
        assert len(payloads) == 2 and payloads[0]['markdown']['content'].startswith('# 同一份')
        assert payloads[1]['msg_type'] == 0 and '消息发送失败' in payloads[1]['content']
        assert INFO['text']['desc'] not in payloads[1]['content']
        assert bool(payloads[0].get('keyboard')) == (kind == 'c2c')
        source = event(kind); source.id = 'manual-continue'
        await pager.send(wrapped, source)
        assert client._request.call_args.args[0].json['markdown']['content'] == payloads[0]['markdown']['content']
        assert not pager.pending
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['text', 'markdown'])
def test_art_page_uses_one_published_template_for_both_chats(setup, mode):
    from test_qq_preview import INFO
    from hikari_bot.qq_official.card_actions import image_navigation
    _, store, _ = setup
    spec = default('art.page')
    spec.update(template='{name}\n第 {index}/{total} 张\n{navigation}', mode=mode)
    store.mutate('save', 'art', 'art.page', spec, 0)
    store.mutate('publish', 'art', revision=1)
    for offset in (0, 1):
        private = image_navigation(INFO, 'only-user', offset, 2, buttons=True)
        group = image_navigation(INFO, 'only-user', offset, 2, buttons=False)
        assert private[0] == group[0]
        assert private[0].type == ('markdown' if mode == 'markdown' else 'text')
        assert bool(private['keyboard']) == (mode == 'markdown') and not group['keyboard']
        assert ('卡图 14558127 1' if offset == 0 else '已是最后一张') in str(group)


@pytest.mark.parametrize('delivery_type', ['pager', 'result'])
@pytest.mark.parametrize('status', [400, 403])
def test_markdown_rejection_reports_failure_and_keeps_original_format(delivery_type, status):
    from test_qq_preview import NOW, bot, event
    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.drivers import Response
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.results import ResultDelivery
    from hikari_bot.qq_official.media import NamedFileBot
    async def run():
        client = bot()
        client._request.side_effect = [ActionFailed(Response(status, content=json.dumps(
            {'code': 304003, 'message': 'secret-payload'}))), {'id': 'notice', 'timestamp': NOW.isoformat()},
            {'id': 'retried', 'timestamp': NOW.isoformat()}]
        wrapped = NamedFileBot(client)
        pager = ReplyPager(now=lambda: NOW)
        delivery = pager if delivery_type == 'pager' else ResultDelivery(pager)
        output = Output('# 原文\n**完整正文**', 'synthetic', 'markdown')
        await delivery.send(wrapped, event(), output if delivery_type == 'pager' else Result(output))
        payloads = [call.args[0].json for call in client._request.call_args_list]
        assert [p['msg_type'] for p in payloads] == [2, 0]
        assert '304003' not in payloads[1]['content'] and '消息发送失败' in payloads[1]['content']
        assert 'secret-payload' not in payloads[1]['content'] and '**完整正文**' not in payloads[1]['content']
        assert delivery.pending
        source = event(); source.id = 'new-event'
        await delivery.send(wrapped, source)
        assert client._request.call_args.args[0].json['markdown']['content'] == output
        assert not delivery.pending
    asyncio.run(run())


def test_markdown_result_is_separate_from_image_but_plain_keeps_same_bubble():
    from datetime import datetime, timezone
    from nonebot.adapters.qq.event import C2CMessageCreateEvent
    from hikari_bot.qq_official.delivery import ReplyPager
    from hikari_bot.qq_official.results import ResultDelivery
    event = C2CMessageCreateEvent.model_validate(dict(id='synthetic', content='历史', timestamp=datetime.now(timezone.utc).isoformat(), author={'id':'sample', 'user_openid':'sample'}))
    bot = SimpleNamespace(self_id='app', send=AsyncMock())
    delivery = ResultDelivery(ReplyPager())
    asyncio.run(delivery.send(bot, event, Result(Output('# 月统计', 'synthetic', 'markdown'), (b'image',))))
    assert bot.send.await_count == 2
    assert bot.send.call_args_list[0].args[1].mode == 'markdown'
    assert bot.send.call_args_list[1].args[1].type == 'file_image'
    bot.send.reset_mock()
    asyncio.run(ResultDelivery(ReplyPager()).send(bot, event, Result(Output('月统计', 'synthetic', 'text'), (b'image',))))
    assert bot.send.await_count == 1 and bot.send.call_args.args[1]['file_image']


def test_catalog_covers_wired_keys_and_every_feature_has_status():
    root = Path(__file__).resolve().parents[1] / 'hikari_bot'
    for info in SCHEMA['features'].values():
        assert all(info.get(name) for name in ['label','status','implementation','differences','qq','remaining','coverage'])
    for key, definition in SCHEMA['states'].items():
        assert definition['feature'] in SCHEMA['features']
        templates.validate(key, default(key))
    for path in root.rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'present','wording','NoticeText'} and node.args and isinstance(node.args[0], ast.Constant):
                key = node.args[0].value
                assert key in SCHEMA['states'], (path, key)
                if node.func.id != 'present':
                    definition = SCHEMA['states'][key]
                    assert ast.literal_eval(node.args[1]) in {definition['template'], definition.get('legacy_template'),
                                                            *definition.get('previous_default_templates', [])}, (path, key)


def test_notice_defaults_do_not_change_onebot_and_can_render_at_official_boundary(setup):
    _, store, _ = setup
    text = NoticeText('opponent.no_deck', '{player} vs {opponent}\n{opponent}：暂无卡组', {'player':'甲', 'opponent':'乙'})
    spec = default(text.key)
    spec['template'] = '{opponent} 尚未收录'
    store.mutate('save', 'opponent', text.key, spec, 0)
    store.mutate('publish', 'opponent', revision=1)
    assert str(text) == '甲 vs 乙\n乙：暂无卡组'
    assert present(text.key, text.values) == '乙 尚未收录'
