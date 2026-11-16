"""Text parity and passive paging; synthetic inputs, no live APIs or plugins."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hikari_bot.services.card_text import format_card_effect
from hikari_bot.qq_official.commands import reply_for
from hikari_bot.qq_official.delivery import ReplyPager, TEXT_CHARS, CONTINUE_HINT
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def event(kind="c2c", *, identity="synthetic-user", message_id="synthetic-1", age=0):
    data = {"id": message_id, "content": "效果 synthetic",
            "timestamp": (NOW - timedelta(seconds=age)).isoformat()}
    if kind == "group":
        data.update(group_id="synthetic-group", group_openid="synthetic-group",
                    author={"id": identity, "member_openid": identity, "bot": False, "member_role": "member"})
        return GroupAtMessageCreateEvent.model_validate(data)
    data["author"] = {"id": identity, "user_openid": identity}
    return C2CMessageCreateEvent.model_validate(data)


def old_handler(matcher_name, info):
    # Compile only the actual handler body, without importing the old plugin and its jobs.
    tree = ast.parse((ROOT / "hikari_bot/plugins/ygocard_query.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and isinstance(d.func.value, ast.Name) and d.func.value.id == matcher_name
                        for d in node.decorator_list))
    node.decorator_list = []
    matcher = SimpleNamespace(finish=AsyncMock())
    service = SimpleNamespace(get_card_info=AsyncMock(return_value=info))
    namespace = {"Bot": object, "MessageEvent": object, "Message": object,
                 "CommandArg": lambda: None, "card_service": service,
                 "format_card_effect": format_card_effect, matcher_name: matcher}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated_actual_handler", "exec"), namespace)
    return namespace["_"], matcher, service


class FormatParityTests(unittest.IsolatedAsyncioTestCase):
    async def assert_parity(self, info, expected):
        function, matcher, service = old_handler("ygo_card_effect", info)
        args = SimpleNamespace(extract_plain_text=lambda: "synthetic query")
        await function(None, None, args)
        old = matcher.finish.call_args.args[0]
        service.get_card_info.assert_awaited_once_with("synthetic query")
        catalog = Mock()
        catalog.search.return_value = info
        from hikari_bot.qq_official.output_templates import default, draft
        spec = default('effect.card')
        spec.update(template='{body}', mode='text')
        with draft('effect.card', spec):
            new = await reply_for("效果 synthetic query", catalog)
        catalog.search.assert_called_once_with("synthetic query", fuzzy=True)
        self.assertEqual(old, expected)
        self.assertEqual(new, old)

    async def test_ash_blossom_header_and_body(self):
        await self.assert_parity({"cn_name": "灰流丽", "jp_name": "灰流うらら",
            "text": {"types": "[怪兽|效果|调整] 不死/炎 [★3] 0/1800", "pdesc": "", "desc": "效果正文。"}},
            "灰流丽（灰流うらら）\n[怪兽|效果|调整] 不死/炎 [★3] 0/1800\n效果正文。")

    async def test_pendulum_separator_preserves_lines(self):
        await self.assert_parity({"cn_name": "测试灵摆", "jp_name": "テスト",
            "text": {"types": "灵摆", "pdesc": "灵摆效果\n第二行", "desc": "怪兽效果"}},
            "测试灵摆（テスト）\n灵摆\n灵摆效果\n第二行\n---------------\n怪兽效果")

    async def test_missing_japanese_uses_english(self):
        await self.assert_parity({"cn_name": "测试", "en_name": "Example",
            "text": {"types": "魔法", "desc": "正文"}}, "测试（Example）\n魔法\n正文")

    async def test_missing_chinese_and_empty_effect(self):
        await self.assert_parity({"jp_name": "テスト", "text": {"types": "魔法"}},
                                "テスト\n魔法\n暂无效果资料。")

    async def test_long_text_is_complete_and_identical(self):
        body = ("长效果🙂 \n" * 2000) + "不可丢失的末尾"
        await self.assert_parity({"cn_name": "长文", "jp_name": "長文",
                                "text": {"types": "测试", "desc": body}}, "长文（長文）\n测试\n" + body)

    async def test_card_password_semantics_unchanged(self):
        for info, expected in (({"passcode": "00000123", "id": 123}, "00000123"),
                               ({"passcode": None, "id": 100012345}, "100012345"),
                               ({"passcode": None, "id": 0}, "该卡暂无卡密。")):
            function, matcher, _ = old_handler("ygo_card_id", info)
            await function(None, None, SimpleNamespace(extract_plain_text=lambda: "test"))
            catalog = Mock()
            catalog.search.return_value = info
            self.assertEqual(matcher.finish.call_args.args[0], expected)
            self.assertEqual(await reply_for("卡密 test", catalog), expected)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def pager(self, **kwargs):
        return ReplyPager(now=lambda: NOW, **kwargs)

    def bot(self):
        bot = SimpleNamespace(self_id="synthetic-app", send_to_c2c=AsyncMock(), send_to_group=AsyncMock())
        async def send(source, payload):
            return await Bot.send(bot, source, payload)
        bot.send = send
        return bot

    async def test_short_text_unchanged_and_bound_to_event(self):
        bot = self.bot()
        await self.pager().send(bot, event(), "灰流丽（灰流うらら）\n完整正文")
        call = bot.send_to_c2c.call_args
        self.assertEqual(call.kwargs["message"], "灰流丽（灰流うらら）\n完整正文")
        self.assertEqual(call.kwargs["msg_id"], "synthetic-1")
        self.assertEqual(call.kwargs["msg_seq"], 1)

    async def test_long_text_across_fresh_events_is_lossless_in_order(self):
        for kind, quota in (("c2c", 4), ("group", 5)):
            with self.subTest(kind=kind):
                pager = self.pager()
                bot = self.bot()
                text = "开头\n" + ("长正文🙂 " * 3000) + "\n尾部不可丢失"
                recovered = []
                index = 0
                while index == 0 or pager.pending:
                    method = bot.send_to_group if kind == "group" else bot.send_to_c2c
                    method.reset_mock()
                    source = event(kind, message_id=f"event-{index}")
                    await pager.send(bot, source, text if index == 0 else None)
                    calls = method.call_args_list
                    self.assertLessEqual(len(calls), quota)
                    self.assertEqual([call.kwargs["msg_seq"] for call in calls], list(range(1, len(calls) + 1)))
                    for call in calls:
                        payload = call.kwargs["message"]
                        self.assertLessEqual(len(payload), TEXT_CHARS)
                        self.assertEqual(call.kwargs["msg_id"], f"event-{index}")
                        recovered.append(payload.removesuffix(CONTINUE_HINT))
                    if pager.pending:
                        self.assertTrue(calls[-1].kwargs["message"].endswith(CONTINUE_HINT))
                    index += 1
                    self.assertLess(index, 30)
                self.assertEqual("".join(recovered), text)

    async def test_duplicate_event_does_not_restart_reply_budget(self):
        pager = self.pager()
        bot = self.bot()
        await pager.send(bot, event(), "x" * 9000)
        before = bot.send_to_c2c.call_count
        await pager.send(bot, event(), "duplicate response")
        self.assertEqual(bot.send_to_c2c.call_count, before)
        self.assertEqual(before, 4)

    async def test_existing_reply_sequence_and_expiry_respected(self):
        bot = self.bot()
        source = event()
        source._reply_seq = 3
        await self.pager().send(bot, source, "x" * 9000)
        self.assertEqual(bot.send_to_c2c.call_count, 1)
        self.assertEqual(bot.send_to_c2c.call_args.kwargs["msg_seq"], 4)
        for kind, age in (("c2c", 3600), ("group", 300)):
            expired_bot = self.bot()
            await self.pager().send(expired_bot, event(kind, age=age), "not sent")
            expired_bot.send_to_c2c.assert_not_called()
            expired_bot.send_to_group.assert_not_called()

    async def test_pending_isolated_by_user_group_and_app(self):
        pager = self.pager()
        bot = self.bot()
        await pager.send(bot, event(), "private result " * 1000)
        for other in (event(identity="other"), event("group")):
            bot.send_to_c2c.reset_mock(); bot.send_to_group.reset_mock()
            await pager.send(bot, other)
            calls = bot.send_to_c2c.call_args_list + bot.send_to_group.call_args_list
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private result", calls[0].kwargs["message"])
        bot.self_id = "other-app"
        await pager.send(bot, event(message_id="other-app-message"))
        self.assertNotIn("private result", bot.send_to_c2c.call_args.kwargs["message"])

    async def test_failure_stops_without_retry_and_retains_unconfirmed_text(self):
        pager = self.pager()
        bot = self.bot()
        bot.send_to_c2c.side_effect = [None, RuntimeError("synthetic-sensitive-error")]
        text = "x" * 7000
        with self.assertRaises(RuntimeError):
            await pager.send(bot, event(), text)
        self.assertEqual(bot.send_to_c2c.call_count, 2)
        state = next(iter(pager.pending.values()))
        self.assertEqual(state.text[state.offset:], text[TEXT_CHARS:])
        bot.send_to_c2c.side_effect = None
        bot.send_to_c2c.reset_mock()
        await pager.send(bot, event(message_id="explicit-continue"))
        self.assertEqual(bot.send_to_c2c.call_args_list[0].kwargs["message"], text[TEXT_CHARS:2 * TEXT_CHARS])

    async def test_expired_pending_is_explicit_and_new_queries_replace_old(self):
        clock = [0.0]
        pager = self.pager(clock=lambda: clock[0])
        bot = self.bot()
        await pager.send(bot, event(), "old" * 4000)
        clock[0] = 601
        await pager.send(bot, event(message_id="later"))
        self.assertIn("已过期", bot.send_to_c2c.call_args.kwargs["message"])
        await pager.send(bot, event(message_id="new"), "new result")
        self.assertEqual(bot.send_to_c2c.call_args.kwargs["message"], "new result")
        self.assertFalse(pager.pending)


if __name__ == "__main__":
    unittest.main()
