"""Synthetic C2C preview tests; actual SDK serialization, mocked transport."""
import copy
import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from nonebot.drivers import Response, URL
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.preview import build_preview, handle_preview

NOW = datetime.now(timezone.utc)
INFO = {"id": 14558127, "cn_name": "灰流丽", "jp_name": "灰流うらら",
        "text": {"types": "[怪兽|效果|调整] 不死/炎 [★3] 0/1800", "pdesc": "",
                 "desc": "此卡名的效果１回合仅可使用１次。\n①：完整原始效果。"}}


def event(kind="c2c", user="only-user"):
    data = {"id": "synthetic-message", "content": "样式预览", "timestamp": NOW.isoformat(),
            "author": {"id": user, "user_openid": user}}
    if kind == "group":
        data.update(group_id="synthetic-group", group_openid="synthetic-group",
                    author={"id": user, "member_openid": user, "bot": False, "member_role": "member"})
        return GroupAtMessageCreateEvent.model_validate(data)
    return C2CMessageCreateEvent.model_validate(data)


def bot():
    fake = SimpleNamespace(self_id="synthetic-app", adapter=SimpleNamespace(
        get_api_base=lambda: URL("https://example.invalid")),
        _request=AsyncMock(return_value={"id": "synthetic-reply", "timestamp": NOW.isoformat()}),
        _prepare_message=Bot._prepare_message, _extract_send_message=Bot._extract_send_message)
    async def post(**kwargs):
        return await Bot.post_c2c_messages(fake, **kwargs)
    async def c2c(**kwargs):
        return await Bot.send_to_c2c(fake, **kwargs)
    async def send(source, message):
        return await Bot.send(fake, source, message)
    fake.post_c2c_messages, fake.send_to_c2c, fake.send = post, c2c, send
    return fake


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def run_preview(self, client, *, source=None, info=INFO, users=frozenset({"only-user"})):
        catalog = Mock()
        catalog.search.return_value = info
        config = PilotConfig("synthetic-app", "fake", users=users)
        pager = ReplyPager(now=lambda: NOW)
        output = io.StringIO()
        with redirect_stdout(output):
            await handle_preview(config, client, source or event(), pager, catalog)
        return pager, output.getvalue(), catalog

    async def test_sdk_serializes_markdown_keyboard_and_passive_context(self):
        client = bot()
        pager, output, catalog = await self.run_preview(client)
        catalog.search.assert_called_once_with("14558127")
        request = client._request.call_args.args[0]
        payload = request.json
        self.assertEqual(payload["msg_type"], 2)
        self.assertEqual(payload["msg_id"], "synthetic-message")
        self.assertEqual(payload["msg_seq"], 1)
        content = payload["markdown"]["content"]
        self.assertTrue(content.startswith("# 灰流丽\n灰流うらら\n"))
        self.assertIn(INFO["text"]["types"], content)
        self.assertTrue(content.endswith(INFO["text"]["desc"]))
        buttons = payload["keyboard"]["content"]["rows"][0]["buttons"]
        self.assertEqual([b["action"]["data"] for b in buttons], ["效果 14558127", "卡密 14558127"])
        for button in buttons:
            self.assertEqual(button["action"]["type"], 1)
            self.assertNotIn("enter", button["action"])
            self.assertEqual(button["action"]["permission"], {"type": 0, "specify_user_ids": ["only-user"]})
            self.assertNotIn("anchor", button["action"])
        json.dumps(payload)
        self.assertIn("rich_api_accepted", output)
        self.assertNotIn("only-user", output)
        self.assertFalse(pager.pending)

    async def test_explicit_rejection_falls_back_and_exposes_only_numeric_codes(self):
        client = bot()
        failure = ActionFailed(Response(403, content=json.dumps({"code": 304003, "message": "secret-payload"})))
        client._request.side_effect = [failure, {"id": "synthetic-reply", "timestamp": NOW.isoformat()}]
        _, output, _ = await self.run_preview(client)
        self.assertEqual(client._request.call_count, 2)
        payload = client._request.call_args.args[0].json
        self.assertEqual(payload["msg_type"], 0)
        self.assertEqual(payload["msg_seq"], 2)
        self.assertIn("304003", payload["content"])
        self.assertTrue(payload["content"].endswith(INFO["text"]["desc"]))
        self.assertIn("304003", output)
        self.assertIn("text_fallback_api_accepted", output)
        self.assertNotIn("secret-payload", output)

    async def test_network_failure_is_not_retried_or_reported_as_accepted(self):
        client = bot()
        client._request.side_effect = NetworkError("private-error")
        pager, output, _ = await self.run_preview(client)
        self.assertEqual(client._request.call_count, 1)
        self.assertTrue(pager.pending)
        self.assertNotIn("accepted", output)
        self.assertNotIn("private-error", output)

    async def test_no_preview_for_group_other_user_or_expanded_allowlist(self):
        for source, users in ((event("group"), frozenset({"only-user"})),
                              (event(user="other-user"), frozenset({"only-user"})),
                              (event(), frozenset({"only-user", "second"}))):
            client = bot()
            _, _, catalog = await self.run_preview(client, source=source, users=users)
            client._request.assert_not_called()
            catalog.search.assert_not_called()

    async def test_long_effect_remains_complete_using_existing_pager(self):
        info = copy.deepcopy(INFO)
        info["text"]["desc"] = "完整原文" * 3000
        rich, plain, content = build_preview(info, "only-user")
        self.assertTrue(content.endswith(info["text"]["desc"]))
        self.assertTrue(plain.endswith(info["text"]["desc"]))
        client = bot()
        pager, output, _ = await self.run_preview(client, info=info)
        self.assertLessEqual(client._request.call_count, 4)
        self.assertTrue(all(c.args[0].json["msg_type"] == 0 for c in client._request.call_args_list))
        self.assertTrue(next(iter(pager.pending.values())).text.endswith(info["text"]["desc"]))
        self.assertIn("local_plain_text", output)

    async def test_plugin_routes_preview_without_changing_default_queries(self):
        from hikari_bot.qq_official import plugin
        matcher = SimpleNamespace()
        def handle():
            def keep(function):
                matcher.function = function
                return function
            return keep
        matcher.handle = handle
        config = PilotConfig("synthetic-app", "fake", users=frozenset({"only-user"}))
        with patch.object(plugin, "Rule", side_effect=lambda check: check), \
                patch.object(plugin, "on_message", return_value=matcher):
            plugin.register(config)
        client = bot()
        source = event()
        with patch.object(plugin, "handle_preview", new_callable=AsyncMock) as preview, \
                patch.object(plugin, "reply_for", new_callable=AsyncMock) as regular:
            await matcher.function(client, source)
            preview.assert_awaited_once()
            regular.assert_not_called()
            source = C2CMessageCreateEvent.model_validate({**event().model_dump(), "id": "fresh-ping", "content": "ping"})
            regular.return_value = "plain effect"
            await matcher.function(client, source)
            regular.assert_awaited_once_with("ping", None)
            self.assertEqual(client._request.call_args.args[0].json["msg_type"], 0)


if __name__ == "__main__":
    unittest.main()
