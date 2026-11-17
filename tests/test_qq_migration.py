"""官方迁移回归：合成事件与临时数据，不连接真实 QQ。"""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nonebot.adapters.qq import Bot, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent, GroupAtMessageCreateEvent, InteractionCreateEvent

from hikari_bot.services.platform import Identity, Request, Result, Media, legacy_delivery_status
from hikari_bot.services.platform_state import PlatformState
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.events import request_from_event, EventGate
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.results import ResultDelivery
from hikari_bot.qq_official.business import Queries

ROOT = Path(__file__).resolve().parents[1]


def event(kind='c2c', user='u', group='g', id='event', text='ping'):
    data = dict(id=id, content=text, timestamp=datetime.now(timezone.utc).isoformat(),
                author=dict(id=user, user_openid=user))
    if kind != 'c2c':
        data.update(group_id=group, group_openid=group,
                    author=dict(id=user, member_openid=user, bot=False, member_role='member'))
    cls = {'c2c': C2CMessageCreateEvent, 'group': GroupMessageCreateEvent,
           'group_at': GroupAtMessageCreateEvent}[kind]
    return cls.model_validate(data)


def bot():
    client = SimpleNamespace(self_id='app', send_to_c2c=AsyncMock(), send_to_group=AsyncMock())
    client.send = MethodType(Bot.send, client)
    return client


def request(text='', kind='c2c', user='u', group='g', id='event'):
    return request_from_event(bot(), event(kind, user, group, id, text))


class IdentityTests(unittest.TestCase):
    def test_server_profile_v1_does_not_inherit_admins_and_v2_checks_membership(self):
        from run_srbs import read_profile, configuration
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / '.config/srbs/profile.json'
            path.parent.mkdir(parents=True)
            credentials = home / 'credentials'
            credentials.mkdir()
            (credentials / 'srbs-app-id').write_text('app')
            (credentials / 'srbs-app-secret').write_text('synthetic')
            data = dict(schema_version=1, allowed_users=['u'], allowed_groups=[], card_catalog='')
            path.write_text(json.dumps(data))
            self.assertEqual(configuration(home, credentials)['HIKARI_QQ_ADMIN_USERS'], '[]')
            data.update(schema_version=2, verified_admin_users=['u'], receive_non_at=True, allowed_groups=['g'])
            path.write_text(json.dumps(data))
            config = PilotConfig.from_env(configuration(home, credentials))
            self.assertEqual(config.admins, frozenset({'u'}))
            self.assertTrue(config.allows('group', 'member', 'g'))
            data['verified_admin_users'] = ['other']
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read_profile(path)

    def test_namespace_app_and_group_member_scope_never_alias(self):
        identities = [Identity('onebot11', 'app', 'user', '123'),
                      Identity('qq_official', 'app', 'user', '123'),
                      Identity('qq_official', 'other-app', 'user', '123'),
                      Identity('qq_official', 'app', 'member', '123', 'g'),
                      Identity('qq_official', 'app', 'member', '123', 'other-g')]
        self.assertEqual(len({i.key for i in identities}), 5)
        self.assertNotIn('123', repr(identities[1]))
        self.assertNotEqual(request(kind='group').session, request(kind='c2c').session)
        self.assertNotEqual(request(kind='group').session, request(kind='group', group='g2').session)

    def test_event_dedup_expiry_and_capacity_fail_closed(self):
        now = [0]
        gate = EventGate(clock=lambda: now[0], limit=2)
        self.assertTrue(gate.claim(request()))
        self.assertFalse(gate.claim(request()))
        self.assertTrue(gate.claim(request(user='u2')))
        self.assertFalse(gate.claim(request(id='new')))
        now[0] = 3601
        self.assertTrue(gate.claim(request()))
        self.assertFalse(gate.claim(request(id='')))

    def test_scope_non_at_defaults_on_and_admin_is_not_inherited(self):
        config = PilotConfig('app', 'fake', users=frozenset({'u'}), groups=frozenset({'g'}))
        self.assertTrue(config.allows('group_at', 'member', 'g'))
        self.assertTrue(config.allows('group', 'member', 'g'))
        self.assertFalse(config.allows('group', 'member', 'other'))
        self.assertFalse(config.allows('c2c', 'member'))
        self.assertFalse(config.allows('superuser', 'u'))
        self.assertFalse(PilotConfig('app', 'fake', groups=frozenset({'g'}), receive_non_at=False).allows('group', 'member', 'g'))

    def test_new_bindings_and_paused_subscriptions_leave_legacy_bytes_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'hikari.db'
            old.write_bytes(b'opaque historical data')
            digest = hashlib.sha256(old.read_bytes()).digest()
            state = PlatformState(Path(tmp) / 'new')
            actor = request().actor
            state.bind(actor, 'player')
            self.assertEqual(state.binding(actor), 'player')
            self.assertEqual(state.binding(request(kind='group').actor), 'player')
            state.subscribe(actor, 'player')
            self.assertEqual(state.subscriptions(actor), [('player', 'paused_platform_unverified')])
            state.unsubscribe(request(user='other').actor, 'player')
            self.assertEqual(len(state.subscriptions(actor)), 1)
            self.assertEqual(hashlib.sha256(old.read_bytes()).digest(), digest)
            self.assertTrue(state.claim_event(request(), now=1))
            self.assertFalse(PlatformState(Path(tmp) / 'new').claim_event(request(), now=10))
            self.assertTrue(state.claim_event(request(id='next'), now=10))
            self.assertTrue(state.claim_event(request(), now=3602))
            with self.assertRaises(ValueError):
                state.bind(Identity('onebot11', 'old', 'user', '123'), 'player')
        self.assertEqual(legacy_delivery_status('123'), 'paused_identity_unverified')

    def test_pure_services_do_not_load_old_settings_or_onebot_plugins(self):
        code = '''import sys
from hikari_bot.qq_official import plugin
from hikari_bot.services import price_reports, ygodeck, mycard_reports, translation
assert 'hikari_bot.core.config' not in sys.modules
assert not any(x.startswith('hikari_bot.plugins.') for x in sys.modules)
assert 'nonebot_plugin_parser' not in sys.modules
assert 'nonebot_plugin_easy_translate' not in sys.modules
'''
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True, capture_output=True)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_text_and_single_image_share_one_passive_message(self):
        client, source = bot(), event()
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(client, source, Result('玩家：synthetic\n胜率：50%', (b'image',)))
        client.send_to_c2c.assert_awaited_once()
        sent = client.send_to_c2c.call_args.kwargs
        self.assertEqual(sent['msg_id'], source.id)
        self.assertEqual(sent['message'].extract_plain_text(), '玩家：synthetic\n胜率：50%')
        self.assertEqual(sent['message']['file_image'][0].data['content'], b'image')
        self.assertFalse(delivery.has_pending(client, source))
    async def test_rate_limit_response_does_not_repeat_a_message(self):
        from nonebot.adapters.qq.exception import ActionFailed
        from nonebot.drivers import Response
        client = bot()
        client.send_to_c2c.side_effect = ActionFailed(Response(429, content='{"code":429}'))
        source = event()
        delivery = ResultDelivery(ReplyPager())
        with self.assertRaises(ActionFailed):
            await delivery.send(client, source, Result('hello'))
        self.assertEqual(client.send_to_c2c.call_count, 1)
        self.assertTrue(delivery.has_pending(client, source))

    async def test_small_pdf_uses_named_upload_without_unsolicited_send(self):
        from hikari_bot.qq_official.media import NamedFileBot
        for kind in ('c2c', 'group'):
            client = bot()
            client._prepare_message = Bot._prepare_message
            client._extract_send_message = Bot._extract_send_message
            client.post_c2c_upload = client.post_group_upload = AsyncMock(return_value=SimpleNamespace(file_info='synthetic-file-info'))
            client.post_c2c_messages = client.post_group_messages = AsyncMock()
            source = event(kind)
            await NamedFileBot(client).send(source, MessageSegment.file_file(b'%PDF', file_name='deck.pdf'))
            upload = client.post_c2c_upload.call_args.kwargs
            self.assertEqual(upload['file_name'], 'deck.pdf')
            self.assertFalse(upload['srv_send_msg'])
            self.assertEqual(upload['file_type'], 4)
            posted = client.post_c2c_messages.call_args.kwargs
            self.assertEqual(posted['msg_id'], source.id)
            self.assertEqual(posted['msg_seq'], 1)
            self.assertEqual(posted['media'].file_info, 'synthetic-file-info')

    async def test_mixed_media_keeps_order_and_pages_using_new_event(self):
        client, source = bot(), event()
        delivery = ResultDelivery(ReplyPager())
        value = Result(media=('text1', Media('image', b'img'), Media('video', b'video', 'v.mp4'),
                              Media('file', b'pdf', 'deck.pdf'), 'last'))
        await delivery.send(client, source, value)
        calls = client.send_to_c2c.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertIn('继续', str(calls[-1].kwargs['message']))
        self.assertTrue(all(c.kwargs['msg_id'] == source.id for c in calls))
        self.assertTrue(delivery.has_pending(client, source))
        wrong_group = event('group', id='second')
        await delivery.send(client, wrong_group)
        client.send_to_group.assert_not_awaited()
        await delivery.send(client, event(id='second'))
        self.assertEqual(client.send_to_c2c.call_count, 6)
        self.assertEqual(str(client.send_to_c2c.call_args.kwargs['message']), 'last')
        self.assertFalse(delivery.has_pending(client, source))

    async def test_failure_does_not_retry_or_fall_back_to_active(self):
        client, source = bot(), event()
        client.send_to_c2c.side_effect = TimeoutError('private payload')
        delivery = ResultDelivery(ReplyPager())
        with self.assertRaises(TimeoutError):
            await delivery.send(client, source, Result(file=b'PDF', filename='deck.pdf'))
        self.assertEqual(client.send_to_c2c.call_count, 1)
        self.assertTrue(delivery.has_pending(client, source))
        self.assertEqual(client.send_to_c2c.call_args.kwargs['msg_id'], source.id)

    async def test_expired_event_and_session_do_not_send(self):
        now = [0]
        client, source = bot(), event()
        source.timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        delivery = ResultDelivery(ReplyPager(), clock=lambda: now[0])
        await delivery.send(client, source, Result('hello'))
        client.send_to_c2c.assert_not_awaited()
        now[0] = 601
        self.assertFalse(delivery.has_pending(client, event()))
        await delivery.send(client, event(id='next'))
        client.send_to_c2c.assert_not_awaited()

    async def test_group_non_at_passive_target_and_five_reply_limit(self):
        client, source = bot(), event('group')
        delivery = ResultDelivery(ReplyPager())
        await delivery.send(client, source, Result(media=tuple(str(i) for i in range(6))))
        self.assertEqual(client.send_to_group.call_count, 5)
        self.assertEqual(client.send_to_group.call_args.kwargs['group_openid'], 'g')
        self.assertEqual(client.send_to_group.call_args.kwargs['msg_id'], 'event')
        self.assertIn('继续', str(client.send_to_group.call_args.kwargs['message']))


class BusinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip_translation_uses_existing_client_without_plugin_registration(self):
        from hikari_bot.services.translation import translate
        translator = SimpleNamespace(client=AsyncMock(), translate=AsyncMock(side_effect=[
            SimpleNamespace(text='日本語'), SimpleNamespace(text='回译结果')]))
        factory = Mock(return_value=translator)
        with patch('hikari_bot.services.translation.translator_type', return_value=factory):
            self.assertEqual(await translate('中译中', '原文'), '回译结果')
        factory.assert_called_once_with(proxies=None, raise_exception=True)
        self.assertEqual(translator.translate.call_args_list[0].kwargs, {'src': 'zh-cn', 'dest': 'ja'})
        self.assertEqual(translator.translate.call_args_list[1].args, ('日本語',))

    async def test_power_requires_explicit_official_admin_and_private_chat(self):
        from hikari_bot.qq_official.admin import AdminQueries
        power = AsyncMock()
        config = PilotConfig('app', 'fake', users=frozenset({'u'}), admins=frozenset({'u'}))
        admin = AdminQueries(config, power=power)
        for req in (request(kind='group'), request(user='other')):
            result = await admin.handle(req, '关机', '')
            self.assertIn('权限不足', result.text)
        power.assert_not_awaited()
        result = await admin.handle(request(), '开机', 'unexpected')
        self.assertIn('不接受参数', result.text)
        power.assert_not_awaited()
        result = await admin.handle(request(), '开机', '')
        self.assertEqual(result.text, '已发送开机请求。')
        power.assert_awaited_once_with('wake')
        denied = AdminQueries(PilotConfig('app', 'fake', users=frozenset({'u'})), power=power)
        self.assertIn('权限不足', (await denied.handle(request(), '开机', '')).text)

    async def test_power_client_validates_response_with_no_real_side_effects(self):
        import httpx
        from hikari_bot.services.power import request_action
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={'ok': True, 'action': 'wake'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await request_action('http://nas.invalid', 'synthetic', 'wake', client=client)
            self.assertEqual(seen[0].url.path, '/v1/wake')
            with self.assertRaises(RuntimeError):
                await request_action('http://nas.invalid', 'synthetic', 'shutdown', client=client)

    async def test_deck_media_failure_and_pdf_overflow_are_explicit(self):
        from hikari_bot.services.deck_renderer import DeckRenderer
        catalog = Mock()
        catalog.by_id.return_value = {'text': {'types': '怪兽'}}
        renderer = DeckRenderer(catalog, AsyncMock(return_value=None))
        with self.assertRaisesRegex(ValueError, '未发送缺卡'):
            await renderer.card_list(['1', '2'])
        result = await renderer.render('卡组图片', '#main\n1\n#extra\n!side')
        self.assertIn('未发送缺卡', result.text)
        self.assertFalse(result.images)
        text = '#main\n' + '\n'.join(map(str, range(1, 22))) + '\n#extra\n!side'
        result = await renderer.render('卡组PDF', text)
        self.assertIn('超出模板容量', result.text)
        self.assertIsNone(result.file)

    async def test_bind_and_query_use_new_actor_and_group_subscribe_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = PlatformState(tmp)
            queries = Queries(None, state=state)
            self.assertIn('成功', (await queries.handle(request(), '绑定', 'player')).text)
            self.assertIn('player', (await queries.handle(request(), '查询绑定', '')).text)
            self.assertIn('player', (await queries.handle(request(kind='group'), '查询绑定', '')).text)
            self.assertIn('尚未启用', (await queries.handle(request(kind='group'), '订阅', 'player')).text)
            self.assertEqual(state.subscriptions(request(kind='group').conversation), [])

    async def test_firstwin_outage_is_not_reported_as_no_win(self):
        queries = Queries(None)
        with patch('hikari_bot.services.mycard._api_get', new=AsyncMock(return_value=None)):
            result = await queries.handle(request(), '首胜查询', 'player')
        self.assertIn('失败', result.text)
        self.assertNotIn('未完成', result.text)

    async def test_daily_card_is_stable_but_platform_scoped(self):
        catalog = Mock(random_id=Mock(return_value=123))
        queries = Queries(catalog, image_loader=AsyncMock(return_value=b'image'))
        await queries.handle(request(), '每日一卡', '')
        first = catalog.random_id.call_args.args[0]
        await queries.handle(request(), '每日一卡', '')
        self.assertEqual(first, catalog.random_id.call_args.args[0])
        await queries.handle(request(kind='group'), '每日一卡', '')
        self.assertNotEqual(first, catalog.random_id.call_args.args[0])

    async def test_attachment_event_is_acknowledged_without_downloading(self):
        req = request()
        req = Request(req.actor, req.conversation, req.event_id, '', (object(),))
        result = await Queries(None).handle(req, '', '')
        self.assertIn('收到附件', result.text)

    async def test_future_history_is_rejected_before_api(self):
        result = await Queries(None).handle(request('2099年1月历史 player'), '2099年1月历史', 'player')
        self.assertIn('未来', result.text)

    async def test_deferred_match_and_admin_commands_never_execute(self):
        from hikari_bot.qq_official.availability import unavailable_reply
        for command in ('比赛签到', '退赛', '环境统计'):
            self.assertIn('暂缓', unavailable_reply(command, command))
            self.assertIsNone(await Queries(None).handle(request(command), command, ''))
        for command in ('开机', '广播', '重启服务器', 'MyCard文件', '卡价图报'):
            self.assertIn('尚未', unavailable_reply(command, command))


class PriceTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_history_and_daily_report_cannot_modify_database(self):
        from hikari_bot.features.cardrush.read_only import ReadOnlyPrices
        from hikari_bot.services.price_reports import PriceReports
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prices.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE card_price_history(id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, rarity TEXT, model_number TEXT, price INTEGER, changed_at TEXT)')
                db.executemany('INSERT INTO card_price_history VALUES (?,?,?,?,?,?,?)',
                               [(1, 100, 'card', 'UR', 'TEST', 500, '2026-01-01T00:00:00+00:00'),
                                (2, 100, 'card', 'UR', 'TEST', 1000, '2026-01-02T00:00:00+00:00')])
            db.close()
            before = path.read_bytes()
            reader = ReadOnlyPrices(path)
            self.assertEqual(len(await reader.get_price_history(100)), 2)
            changes = await reader.get_daily_changes('2026-01-02', exclude_prefixes=['RD/'])
            self.assertEqual(changes[0].new_price, 1000)
            with reader._connect() as db, self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM card_price_history')
            result = await PriceReports(reader, None).handle(request(), '卡价曲线', 'card')
            self.assertTrue(result.images[0].startswith(b'\x89PNG'))
            self.assertIn('1,000', result.text)
            self.assertEqual(path.read_bytes(), before)


class ParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_does_not_start_driver_or_import_plugins_in_parent(self):
        from hikari_bot.services.link_parser import LinkParser
        result = await LinkParser().parse('https://example.com/unsupported')
        self.assertEqual(result, Result())
        for kind in ('c2c', 'group'):
            client = bot()
            await ResultDelivery(ReplyPager()).send(client, event(kind, text='https://example.com/unsupported'), result)
            client.send_to_c2c.assert_not_awaited()
            client.send_to_group.assert_not_awaited()
        self.assertNotIn('nonebot_plugin_parser', sys.modules)
        invalid = await LinkParser().parse('bm invalid')
        self.assertIn('正确的 BV', invalid.media[0])

    async def test_manifest_rejects_path_escape_and_preserves_media(self):
        from hikari_bot.services.link_parser import load_result
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'video').write_bytes(b'video')
            manifest = root / 'result.json'
            manifest.write_text(json.dumps([dict(kind='video', path='video', name='v.mp4')]))
            result = load_result(root)
            self.assertEqual(result.media, (Media('video', b'video', 'v.mp4'),))
            manifest.write_text(json.dumps([dict(kind='file', path='../outside')]))
            with self.assertRaises(ValueError):
                load_result(root)


class CallbackGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_callback_executes_once_and_foreign_handle_is_rejected(self):
        from hikari_bot.qq_official.callbacks import CallbackController, Action
        config = PilotConfig('app', 'fake', users=frozenset({'u', 'u2'}))
        process = AsyncMock()
        controller = CallbackController(config, process)
        controller.actions['handle'] = Action('app', 'u', 'button', '卡图 123', controller.clock() + 60,
                                             message_id='message', ready=True)
        source = InteractionCreateEvent.model_construct(id='interaction', event_id='outer-event',
            timestamp=datetime.now(timezone.utc).isoformat(), type=11, application_id='app', chat_type=2,
            scene='c2c', user_openid='u', group_openid=None, group_member_openid=None, guild_id=None, channel_id=None,
            data=SimpleNamespace(resolved=SimpleNamespace(button_data='handle', button_id='button', message_id='message')))
        client = SimpleNamespace(self_id='app', put_interaction=AsyncMock(), send_to_c2c=AsyncMock())
        await controller.handle(client, source)
        await controller.handle(client, source)
        await asyncio.gather(*controller.tasks)
        process.assert_awaited_once()
        self.assertEqual(client.put_interaction.call_count, 1)
        await controller.handle(client, source.model_copy(update={'id': 'foreign', 'user_openid': 'u2'}))
        self.assertEqual(client.put_interaction.call_args.kwargs['code'], 1)
        await controller.handle(client, source.model_copy(update={'id': 'already-used'}))
        self.assertEqual(client.put_interaction.call_args.kwargs['code'], 3)
        controller.actions['handle'].expires = controller.clock() - 1
        await controller.handle(client, source.model_copy(update={'id': 'expired-button'}))
        self.assertEqual(client.put_interaction.call_args.kwargs['code'], 1)
        client.send_to_c2c.assert_not_awaited()
        process.assert_awaited_once()

    def test_c2c_callbacks_keep_ownership_with_group_scope_enabled(self):
        from hikari_bot.qq_official.callbacks import CallbackController
        config = PilotConfig('app', 'fake', users=frozenset({'u', 'u2'}), groups=frozenset({'g'}))
        controller = CallbackController(config, AsyncMock())
        source = InteractionCreateEvent.model_construct(type=11, application_id='app', chat_type=2,
            scene='c2c', user_openid='u', group_openid=None, group_member_openid=None, guild_id=None, channel_id=None)
        self.assertTrue(controller.trusted(bot(), source))
        self.assertFalse(controller.trusted(bot(), source.model_copy(update={'user_openid': 'other'})))
        self.assertFalse(controller.trusted(bot(), source.model_copy(update={'group_openid': 'g'})))
        self.assertFalse(controller.trusted(bot(), source.model_copy(update={'application_id': 'other'})))

    async def test_gateway_idle_timeout_is_bounded_and_socket_remains_sdk_owned(self):
        from hikari_bot.qq_official.gateway import heartbeat_deadline, GatewaySocket
        self.assertEqual(heartbeat_deadline(45000), 100)
        with self.assertRaises(ValueError):
            heartbeat_deadline(0)
        inner = SimpleNamespace(receive=AsyncMock(side_effect=TimeoutError), send=AsyncMock())
        socket = GatewaySocket(inner)
        with patch('hikari_bot.qq_official.gateway.telemetry') as report, self.assertRaises(TimeoutError):
            await socket.receive()
        self.assertEqual(report.call_args.args[0], 'receive_timeout')
        self.assertIs(socket.send, inner.send)


def test_joined_group_access_is_explicit_and_does_not_grant_private_or_admin_access():
    import pytest
    from hikari_bot.qq_official.config import PilotConfig
    env = dict(HIKARI_QQ_ENABLED='true', HIKARI_QQ_SANDBOX='false',
               HIKARI_QQ_APP_ID='app', HIKARI_QQ_APP_SECRET='synthetic',
               HIKARI_QQ_ACCEPT_JOINED_GROUPS='true')
    config = PilotConfig.from_env(env)
    assert config.allows('group', 'member', 'new-group')
    assert config.allows('group_at', 'member', 'another-new-group')
    assert not config.allows('group', 'member', '')
    assert not config.allows('c2c', 'member')
    assert not config.admins
    assert not config.groups
    with pytest.raises(ValueError):
        PilotConfig.from_env({**env, 'HIKARI_QQ_ACCEPT_JOINED_GROUPS': 'maybe'})
    assert not PilotConfig.from_env(env, pairing=True).accept_joined_groups


def test_server_profile_v3_explicit_joined_groups_preserves_old_profiles(tmp_path):
    import pytest
    from run_srbs import configuration, read_profile
    from hikari_bot.qq_official.config import PilotConfig
    path = tmp_path / '.config/srbs/profile.json'
    path.parent.mkdir(parents=True)
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    (credentials / 'srbs-app-id').write_text('app')
    (credentials / 'srbs-app-secret').write_text('synthetic')
    data = dict(schema_version=3, allowed_users=[], allowed_groups=[], card_catalog='',
                verified_admin_users=[], receive_non_at=True, accept_joined_groups=True)
    path.write_text(json.dumps(data))
    config = PilotConfig.from_env(configuration(tmp_path, credentials))
    assert config.allows('group', 'member', 'new-group')
    data['accept_joined_groups'] = 'true'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_profile(path)
    data.update(schema_version=2, allowed_users=['owner'])
    data.pop('accept_joined_groups')
    path.write_text(json.dumps(data))
    config = PilotConfig.from_env(configuration(tmp_path, credentials))
    assert not config.accept_joined_groups
    assert not config.allows('group', 'member', 'new-group')


def test_unbound_mycard_queries_explain_username_and_rebinding(tmp_path):
    async def run():
        state = PlatformState(tmp_path)
        queries = Queries(None, state=state)
        for command in ('历史', '11月历史', '26年11月历史', '查分', '首胜查询', '首赢查询', '胜率查询', '胜率统计'):
            result = await queries.handle(request(command, kind='group'), command, '')
            assert f'{command} 用户名' in result.text
            assert '绑定 用户名' in result.text
            assert '旧机器人绑定不会自动沿用' in result.text
            assert 'OpenID' not in result.text
        assert (await queries.handle(request(kind='group'), '绑定', 'player')).text == '已成功绑定用户名 player。'
        assert 'player' in (await queries.handle(request(kind='group'), '查询绑定', '')).text
    asyncio.run(run())
