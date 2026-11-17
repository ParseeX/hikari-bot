HINT = '> 卡名识别有误？可使用[添加别名 卡名 别名]指令为卡片添加别名！'
import importlib.util
import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("hikari_bot.qq_official.qq_pilot_test_" + name,
                    ROOT / "hikari_bot" / "qq_official" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


config = load("config")
sys.path.insert(0, str(ROOT))
try:
    commands = load("commands")
    from hikari_bot.qq_official import pairing
finally:
    sys.path.pop(0)

try:
    from nonebot.adapters.qq import Bot
    from nonebot.adapters.qq.config import BotInfo
    from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
except ImportError:
    Bot = None


class ConfigTests(unittest.TestCase):
    def env(self, **extra):
        return {"HIKARI_QQ_ENABLED": "true", "HIKARI_QQ_APP_ID": "test-app",
                "HIKARI_QQ_SANDBOX": "false",
                "HIKARI_QQ_APP_SECRET": "synthetic-secret",
                "HIKARI_QQ_ALLOWED_USERS": '["opaque-user"]', **extra}

    def test_disabled_by_default(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            config.PilotConfig.from_env({})

    def test_missing_credentials_do_not_echo_values(self):
        with self.assertRaises(ValueError) as error:
            config.PilotConfig.from_env(self.env(HIKARI_QQ_APP_SECRET=""))
        self.assertNotIn("test-app", str(error.exception))

    def test_no_allowlist_refuses_connection(self):
        with self.assertRaisesRegex(ValueError, "allowed"):
            config.PilotConfig.from_env(self.env(HIKARI_QQ_ALLOWED_USERS="[]"))

    def test_ids_are_opaque_and_scope_separated(self):
        value = config.PilotConfig.from_env(self.env(HIKARI_QQ_ALLOWED_GROUPS='["opaque-group"]'))
        self.assertTrue(value.allows("c2c", "opaque-user"))
        self.assertTrue(value.allows("group_at", "member", "opaque-group"))
        self.assertFalse(value.allows("group_at", "opaque-user", "unlisted-group"))
        self.assertFalse(value.allows("c2c", "opaque-group"))
        self.assertTrue(value.allows("group", "opaque-user", "opaque-group"))
        self.assertFalse(value.allows("group", "opaque-user", "unlisted-group"))
        self.assertFalse(value.allows("guild", "opaque-user"))

    def test_invalid_allowlist_does_not_echo_input(self):
        for raw in ('"private-value"', '[123]', '{"private-value": 1}', 'not-json'):
            with self.assertRaises(ValueError) as error:
                config.PilotConfig.from_env(self.env(HIKARI_QQ_ALLOWED_USERS=raw))
            self.assertNotIn(raw, str(error.exception))

    def test_repr_and_minimum_intents(self):
        value = config.PilotConfig.from_env(self.env())
        self.assertNotIn("synthetic-secret", repr(value))
        self.assertNotIn("opaque-user", repr(value))
        self.assertFalse(value.sandbox)
        bot = value.bot_info()
        self.assertTrue(bot["use_websocket"])
        self.assertEqual([key for key, enabled in bot["intent"].items() if enabled],
                         ["c2c_group_at_messages", "interaction"])

    def test_sandbox_typo_rejected(self):
        with self.assertRaises(ValueError):
            config.PilotConfig.from_env(self.env(HIKARI_QQ_SANDBOX="flase"))

    def test_environment_choice_is_explicit(self):
        for value in ("", "auto"):
            with self.assertRaisesRegex(ValueError, "SANDBOX"):
                config.PilotConfig.from_env(self.env(HIKARI_QQ_SANDBOX=value), pairing=True)

    def test_pairing_ignores_business_configuration(self):
        value = config.PilotConfig.from_env(self.env(
            HIKARI_QQ_ALLOWED_USERS="not-json", HIKARI_QQ_ALLOWED_GROUPS="not-json",
            HIKARI_QQ_CARD_CATALOG="nonexistent-private-path"), pairing=True)
        self.assertFalse(value.users or value.groups)
        self.assertIsNone(value.catalog_path)


class PairingTests(unittest.IsolatedAsyncioTestCase):
    def test_wrong_phrase_channel_expiry_and_replay(self):
        now = [10.0]
        session = pairing.PairingSession("c2c", 30, clock=lambda: now[0])
        phrase = session.phrase
        self.assertGreaterEqual(len(phrase), 54)
        self.assertFalse(session.accept("c2c", "wrong"))
        self.assertFalse(session.accept("group", phrase))
        self.assertTrue(session.accept("c2c", phrase))
        self.assertTrue(session.matched)
        self.assertFalse(session.accept("c2c", phrase))
        self.assertEqual(session.phrase, "")
        session = pairing.PairingSession("group", 30, clock=lambda: now[0])
        phrase = session.phrase
        now[0] = 40
        self.assertFalse(session.accept("group", phrase))
        self.assertTrue(session.finished)
        self.assertFalse(session.matched)

    def test_fresh_entropy_and_limits(self):
        with patch.object(pairing.secrets, "token_urlsafe", return_value="synthetic") as token:
            pairing.PairingSession("group", 120)
        token.assert_called_once_with(32)
        for channel, timeout in (("guild", 30), ("c2c", 29), ("c2c", 301)):
            with self.assertRaises(ValueError):
                pairing.PairingSession(channel, timeout)

    @unittest.skipIf(Bot is None, "QQ SDK not installed")
    async def test_sdk_events_channel_isolation_no_reply_or_identity_logging(self):
        import nonebot
        for channel in ("c2c", "group"):
            session = pairing.PairingSession(channel, 30)
            phrase = session.phrase
            matcher = SimpleNamespace()
            def handle():
                def keep(function):
                    matcher.function = function
                    return function
                return keep
            matcher.handle = handle
            hooks = {}
            driver = SimpleNamespace(exit=Mock(),
                on_startup=lambda fn: hooks.update(start=fn),
                on_shutdown=lambda fn: hooks.update(stop=fn))
            output = Mock()
            with patch.object(nonebot, "on_message", return_value=matcher):
                pairing.register_receiver(session, driver, output)
            c2c = C2CMessageCreateEvent.model_validate({
                "id": "synthetic-message", "content": phrase, "timestamp": datetime.now(timezone.utc).isoformat(),
                "author": {"id": "synthetic-user", "user_openid": "synthetic-user"}})
            group = GroupAtMessageCreateEvent.model_validate({
                "id": "synthetic-message", "content": phrase, "timestamp": datetime.now(timezone.utc).isoformat(),
                "group_id": "synthetic-group", "group_openid": "synthetic-group",
                "author": {"id": "unrelated-member", "member_openid": "unrelated-member", "bot": False, "member_role": "member"}})
            matching, other = (c2c, group) if channel == "c2c" else (group, c2c)
            with patch.object(Bot, "send", new_callable=AsyncMock) as send:
                await matcher.function(other)
                await matcher.function(SimpleNamespace())
                bad = matching.model_copy(update={"content": "wrong-secret"})
                await matcher.function(bad)
                output.assert_not_called()
                driver.exit.assert_not_called()
                await matcher.function(matching)
                await matcher.function(matching)
                send.assert_not_called()
            output.assert_called_once()
            self.assertIn('"channel": "' + channel + '"', output.call_args.args[0])
            self.assertIn("synthetic-" + ("user" if channel == "c2c" else "group"), output.call_args.args[0])
            self.assertNotIn("unrelated-member", output.call_args.args[0])
            self.assertNotIn(phrase, output.call_args.args[0])
            driver.exit.assert_called_once_with()

    @unittest.skipIf(Bot is None, "QQ SDK not installed")
    async def test_timeout_hook_exits_and_cancels_timer(self):
        import nonebot
        session = pairing.PairingSession("group", 30, clock=lambda: 0)
        session.clock = lambda: 31
        self.assertFalse(session.accept("group", session.phrase))
        hooks = {}
        matcher = SimpleNamespace(handle=lambda: lambda fn: fn)
        driver = SimpleNamespace(exit=Mock(), on_startup=lambda fn: hooks.update(start=fn),
                                 on_shutdown=lambda fn: hooks.update(stop=fn))
        output = Mock()
        with patch.object(nonebot, "on_message", return_value=matcher):
            pairing.register_receiver(session, driver, output)
        await hooks["start"]()
        await asyncio.sleep(0.01)
        await hooks["stop"]()
        driver.exit.assert_called_once_with()
        self.assertIn("timed out", output.call_args.args[0])

    @unittest.skipIf(Bot is None, "QQ SDK not installed")
    async def test_run_isolates_business_suppresses_payloads_and_bounds_hangs(self):
        import nonebot
        sys.path.insert(0, str(ROOT))
        try:
            import qq_bot
            value = config.PilotConfig("fake-app", "synthetic-secret")
            driver = SimpleNamespace(register_adapter=Mock(), on_shutdown=lambda fn: fn)
            stdout = io.StringIO()
            def fail():
                print("unrelated-payload-and-secret")
                raise RuntimeError("unrelated-payload-and-secret")
            with patch.object(pairing.sys, "stdout", stdout), \
                 patch.object(pairing.threading, "Timer") as timer, \
                 patch.object(qq_bot, "initialize"), \
                 patch.object(nonebot.logger, "remove"), \
                 patch.object(nonebot.logger, "add", return_value=1), \
                 patch.object(nonebot, "get_driver", return_value=driver), \
                 patch.object(pairing, "register_receiver"), \
                 patch.object(nonebot, "run", side_effect=fail), \
                 patch.object(nonebot, "load_from_toml") as load_plugins:
                self.assertEqual(pairing.run(value, "c2c", 30), 1)
                timer.assert_called_once()
                self.assertEqual(timer.call_args.args[0], 35)
                timer.return_value.start.assert_called_once()
                timer.return_value.cancel.assert_called_once()
                with patch.object(pairing.os, "_exit") as hard_exit:
                    timer.call_args.args[1]()
                    hard_exit.assert_called_once_with(2)
                load_plugins.assert_not_called()
            self.assertNotIn("unrelated-payload-and-secret", stdout.getvalue())
            self.assertNotIn("synthetic-secret", stdout.getvalue())
            self.assertIn('"phase": "runtime"', stdout.getvalue())
            self.assertIn('"type": "RuntimeError"', stdout.getvalue())
        finally:
            sys.path.pop(0)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_and_ping_need_no_catalog(self):
        self.assertIn("pong", await commands.reply_for(" /ping "))
        self.assertIn("卡密", await commands.reply_for("帮助"))

    async def test_deck_is_web_link_not_file_generation(self):
        self.assertIn(commands.DECK_URL, await commands.reply_for("日文卡表"))

    async def test_admin_price_and_subscription_commands_are_not_exposed(self):
        for text in ("广播 test", "重启服务器", "卡价 青眼白龙", "订阅 player", "卡密青眼白龙"):
            self.assertIsNone(await commands.reply_for(text))

    async def test_missing_arguments_catalog_and_card(self):
        self.assertIn("用法", await commands.reply_for("卡密"))
        self.assertIn("暂不可用", await commands.reply_for("效果 card"))
        catalog = Mock()
        catalog.search.return_value = None
        self.assertIn("未找到", await commands.reply_for("卡密 card", catalog))

    async def test_catalog_result_and_error_are_safe(self):
        catalog = Mock()
        catalog.search.return_value = {"passcode": "00001234", "cn_name": "测试卡",
                                      "text": {"types": "魔法", "desc": "测试效果"}}
        self.assertEqual(await commands.reply_for("卡密 测试卡", catalog), "00001234\n\n" + HINT)
        from hikari_bot.qq_official.card_actions import card_text
        result = await commands.reply_for("/效果 测试卡", catalog)
        self.assertEqual(result, card_text(catalog.search.return_value))
        self.assertEqual(result.mode, 'markdown')
        catalog.search.side_effect = RuntimeError("sensitive-path")
        self.assertNotIn("sensitive-path", await commands.reply_for("效果 card", catalog))

    async def test_output_and_input_are_bounded(self):
        catalog = Mock()
        self.assertIn("过长", await commands.reply_for("效果 " + "a" * 201, catalog))
        catalog.search.assert_not_called()
        catalog.search.return_value = {"text": {"desc": "a" * 3000}}
        result = await commands.reply_for("效果 card", catalog)
        self.assertTrue(result.endswith("a" * 3000 + "\n\n" + HINT))
        self.assertGreater(len(result), 3000)


@unittest.skipIf(Bot is None, "QQ SDK not installed")
class SdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_environment_switch_changes_api_domain_only(self):
        from nonebot.adapters.qq import Adapter
        from nonebot.adapters.qq.config import Config
        sdk = Config()
        auth = str(sdk.qq_auth_base)
        holder = SimpleNamespace(qq_config=sdk)
        from importlib.metadata import version
        current = tuple(map(int, version('nonebot-adapter-qq').split('.')))
        domain = 'bot.qq.com' if current >= (1, 7, 3) else 'sgroup.qq.com'
        self.assertEqual(Adapter.get_api_base(holder).host, 'api.' + domain)
        if current >= (1, 7, 3):
            import qq_bot
            with self.assertRaisesRegex(ValueError, '沙箱'):
                qq_bot.initialize(Mock(), config.PilotConfig('app', 'fake', sandbox=True))
        else:
            self.assertFalse(sdk.qq_is_sandbox)
            sdk.qq_is_sandbox = True
            self.assertEqual(Adapter.get_api_base(holder).host, 'sandbox.api.' + domain)
        self.assertEqual(str(sdk.qq_auth_base), auth)

    async def test_config_validates_with_installed_sdk(self):
        value = config.PilotConfig("test-app", "synthetic-secret")
        info = BotInfo.model_validate(value.bot_info())
        self.assertEqual(info.intent.to_int(), (1 << 25) | (1 << 26))

    async def test_c2c_reply_preserves_message_and_sequence(self):
        event = C2CMessageCreateEvent.model_validate({
            "id": "fake-message", "content": "ping", "timestamp": datetime.now(timezone.utc).isoformat(),
            "author": {"id": "opaque-user", "user_openid": "opaque-user"}})
        bot = SimpleNamespace(send_to_c2c=AsyncMock())
        await Bot.send(bot, event, "pong")
        await Bot.send(bot, event, "second")
        self.assertEqual(bot.send_to_c2c.call_args.kwargs["openid"], "opaque-user")
        self.assertEqual(bot.send_to_c2c.call_args.kwargs["msg_id"], "fake-message")
        self.assertEqual(bot.send_to_c2c.call_args.kwargs["msg_seq"], 2)

    async def test_group_reply_preserves_group_and_message(self):
        event = GroupAtMessageCreateEvent.model_validate({
            "id": "fake-message", "content": "ping", "timestamp": datetime.now(timezone.utc).isoformat(),
            "group_id": "opaque-group", "group_openid": "opaque-group",
            "author": {"id": "member", "member_openid": "member", "bot": False, "member_role": "member"}})
        bot = SimpleNamespace(send_to_group=AsyncMock())
        await Bot.send(bot, event, "pong")
        self.assertEqual(bot.send_to_group.call_args.kwargs["group_openid"], "opaque-group")
        self.assertEqual(bot.send_to_group.call_args.kwargs["msg_id"], "fake-message")
        self.assertEqual(bot.send_to_group.call_args.kwargs["msg_seq"], 1)

    async def test_plugin_filters_scope_and_replies_only_to_source_event(self):
        sys.path.insert(0, str(ROOT))
        try:
            from hikari_bot.qq_official import plugin
        finally:
            sys.path.pop(0)
        matcher = SimpleNamespace()

        def handle():
            def keep(function):
                matcher.function = function
                return function
            return keep

        matcher.handle = handle
        value = config.PilotConfig("test", "fake", users=frozenset({"allowed"}))
        with patch.object(plugin, "Rule", side_effect=lambda check: check), \
             patch.object(plugin, "on_message", return_value=matcher) as register:
            plugin.register(value)
        allowed = register.call_args.kwargs["rule"]
        event = C2CMessageCreateEvent.model_validate({
            "id": "fake-message", "content": "ping", "timestamp": datetime.now(timezone.utc).isoformat(),
            "author": {"id": "allowed", "user_openid": "allowed"}})
        self.assertTrue(allowed(event))
        denied = event.model_copy(deep=True)
        denied.author.user_openid = "unlisted"
        self.assertFalse(allowed(denied))
        self.assertFalse(allowed(SimpleNamespace()))
        bot = SimpleNamespace(self_id="test", send=AsyncMock())
        await matcher.function(bot, event)
        self.assertIs(bot.send.call_args.args[0], event)
        self.assertIn("pong", str(bot.send.call_args.args[1]))
        bot.send.reset_mock()
        event.content = "广播 test"
        with patch.object(plugin, "reply_for", return_value=None):
            await matcher.function(bot, event)
        bot.send.assert_not_called()
        bot.send.side_effect = RuntimeError("private-error")
        event.id = "fake-error-message"
        with patch.object(plugin, "reply_for", return_value="pong"), \
             patch.object(plugin, "logger") as logger:
            await matcher.function(bot, event)
        self.assertEqual(bot.send.call_count, 1)
        self.assertNotIn("private-error", str(logger.warning.call_args))


class StartupTests(unittest.TestCase):
    def test_pair_cli_does_not_initialize_business(self):
        sys.path.insert(0, str(ROOT))
        try:
            import qq_bot
            from hikari_bot.qq_official import pairing as live_pairing
            env = ConfigTests().env(HIKARI_QQ_ALLOWED_USERS="[]")
            with patch.dict(qq_bot.os.environ, env, clear=True), \
                 patch.object(sys, "argv", ["qq_bot.py", "--pair", "group", "--pair-timeout", "45"]), \
                 patch.object(qq_bot, "initialize") as initialize, \
                 patch.object(live_pairing, "run", return_value=0) as run:
                self.assertEqual(qq_bot.main(), 0)
                initialize.assert_not_called()
                self.assertEqual(run.call_args.args[1:], ("group", 45))
                self.assertFalse(run.call_args.args[0].users)
            with patch.object(sys, "argv", ["qq_bot.py", "--pair", "c2c", "--connect"]), \
                 patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
                qq_bot.main()
        finally:
            sys.path.pop(0)

    def test_initialization_cannot_discover_repository_dotenv(self):
        spec = importlib.util.spec_from_file_location("qq_pilot_entry", ROOT / "qq_bot.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous = Path.cwd()

        def init(**kwargs):
            self.assertNotEqual(Path.cwd(), previous)
            self.assertEqual(list(Path.cwd().iterdir()), [])
            self.assertIsInstance(kwargs["_env_file"], list)
            self.assertFalse(kwargs["_env_file"][0].exists())
            self.assertEqual(kwargs["driver"], "~aiohttp")
            self.assertEqual(kwargs["log_level"], "WARNING")

        module.initialize(SimpleNamespace(init=init), config.PilotConfig("test", "fake"))
        self.assertEqual(Path.cwd(), previous)


class DiagnosticsTests(unittest.TestCase):
    def test_exception_groups_never_format_secret_messages(self):
        from exceptiongroup import ExceptionGroup
        from hikari_bot.qq_official.diagnostics import exception_metadata
        class HiddenError(Exception):
            def __str__(self):
                raise AssertionError("exception text must not be read")
        error = ExceptionGroup("synthetic-secret", [RuntimeError("synthetic-secret"), HiddenError()])
        self.assertEqual(exception_metadata(error),
                         [{"type": "RuntimeError"}, {"type": "OtherException"}])

    @unittest.skipIf(Bot is None, "QQ SDK not installed")
    def test_sdk_error_only_reports_numeric_status_and_code(self):
        from nonebot.adapters.qq.exception import ActionFailed
        from nonebot.drivers import Response
        from hikari_bot.qq_official.diagnostics import exception_metadata
        error = ActionFailed(Response(401, headers={"Authorization": "synthetic-secret"},
            content='{"code":12345,"message":"synthetic-secret","data":"private-user"}'))
        self.assertEqual(exception_metadata(error),
            [{"type": "ActionFailed", "http_status": 401, "sdk_code": 12345}])

    def test_sink_ignores_raw_record_text_and_deduplicates(self):
        output = Mock()
        session = SimpleNamespace(matched=True)
        diagnostic = pairing.Diagnostics(output, session)
        diagnostic.phase = "runtime"
        message = SimpleNamespace(record={"message": "synthetic-secret",
            "exception": SimpleNamespace(value=ValueError("synthetic-secret"))})
        diagnostic.sink(message)
        diagnostic.sink(message)
        output.assert_called_once()
        line = output.call_args.args[0]
        self.assertNotIn("synthetic-secret", line)
        self.assertIn('"matched": true', line)
        self.assertIn('"phase": "shutdown"', line)


if __name__ == "__main__":
    unittest.main()
