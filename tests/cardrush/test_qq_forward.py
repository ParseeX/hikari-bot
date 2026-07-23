import asyncio
import importlib.util
from pathlib import Path

import pytest

from nonebot.adapters.onebot.v11 import ActionFailed

module_path = Path(
    "hikari_bot/plugins/monitors/cardrush_forward.py"
)


def load_forward_module():
    assert module_path.is_file(), "cardrush_forward.py is missing"
    spec = importlib.util.spec_from_file_location(
        "cardrush_test_forward",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBot:
    self_id = "123456"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def call_api(self, api, **data):
        self.calls.append((api, data))
        if self.error:
            raise self.error
        return {"message_id": 42}


def test_private_forward_sends_ordered_pages_in_one_call():
    forward = load_forward_module()
    bot = FakeBot()

    confirmed = asyncio.run(
        forward.send_qq_forward(
            bot,
            [b"one", b"two"],
            user_id=654321,
            log_prefix="[test]",
        )
    )

    assert confirmed is True
    assert len(bot.calls) == 1
    api, data = bot.calls[0]
    assert api == "send_private_forward_msg"
    assert data["user_id"] == 654321
    nodes = data["messages"]
    assert [node.type for node in nodes] == ["node", "node"]
    assert [node.data["nickname"] for node in nodes] == [
        "Cardrush 图报 1/2",
        "Cardrush 图报 2/2",
    ]
    assert [
        node.data["content"][0].data["file"]
        for node in nodes
    ] == [
        "base64://b25l",
        "base64://dHdv",
    ]


def test_group_forward_uses_group_api_once():
    forward = load_forward_module()
    bot = FakeBot()

    asyncio.run(
        forward.send_qq_forward(
            bot,
            [b"one"],
            group_id=456789,
            log_prefix="[test]",
        )
    )

    assert len(bot.calls) == 1
    api, data = bot.calls[0]
    assert api == "send_group_forward_msg"
    assert data["group_id"] == 456789


def test_forward_ignores_onebot_retcode_1200(monkeypatch):
    forward = load_forward_module()
    logs = []
    error = ActionFailed(
        status="failed",
        retcode=1200,
        message="Timeout",
    )
    bot = FakeBot(error)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(forward, "log_message", fake_log)

    confirmed = asyncio.run(
        forward.send_qq_forward(
            bot,
            [b"one"],
            user_id=654321,
            log_prefix="[test]",
        )
    )

    assert confirmed is False
    assert len(bot.calls) == 1
    assert "retcode=1200" in logs[0]


def test_forward_reraises_other_action_errors():
    forward = load_forward_module()
    error = ActionFailed(
        status="failed",
        retcode=100,
        message="Other failure",
    )
    bot = FakeBot(error)

    with pytest.raises(ActionFailed) as caught:
        asyncio.run(
            forward.send_qq_forward(
                bot,
                [b"one"],
                user_id=654321,
                log_prefix="[test]",
            )
        )

    assert caught.value is error

