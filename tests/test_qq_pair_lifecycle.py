"""Real NoneBot lifecycle, synthetic events, no network or user environment."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def probe(scenario):
    import asyncio
    import io
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT))
    blocked = []

    def audit(event, args):
        if event == "open":
            name = Path(str(args[0])).name.lower()
            if name == ".env" or name.startswith(".env.") or "credentials" in name:
                blocked.append("secret-file")
                raise RuntimeError("offline secret-file guard")
        if event == "socket.connect" and args[1][0] not in {"127.0.0.1", "::1"}:
            blocked.append("network")
            raise RuntimeError("offline external-network guard")
        if event == "subprocess.Popen":
            blocked.append("subprocess")
            raise RuntimeError("offline subprocess guard")

    sys.addaudithook(audit)
    import nonebot
    from nonebot.adapters.qq import Adapter, Bot
    from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
    from nonebot.message import handle_event
    from hikari_bot.qq_official.config import PilotConfig
    from hikari_bot.qq_official import pairing
    import qq_bot
    output = io.StringIO()
    sent = []
    shutdowns = []
    original_run = nonebot.run
    original_shutdown = Adapter.shutdown
    session_type = pairing.PairingSession

    def make_session(channel, timeout):
        session = session_type(channel, timeout)
        if scenario == "timeout":
            session.deadline = session.clock() + 0.08
        return session

    async def fake_startup(self):
        async def deliver():
            await asyncio.sleep(0.02)
            if scenario == "timeout":
                return
            info = self.qq_config.qq_bots[0]
            bot = Bot(self, info.id, info)
            data = {"id": "synthetic-message", "content": "hikari-pair OFFLINE-ONLY",
                    "timestamp": "2026-10-12T00:00:00Z"}
            if scenario == "group":
                data.update(group_id="synthetic-group", group_openid="synthetic-group",
                    author={"id": "synthetic-member", "member_openid": "synthetic-member", "bot": False, "member_role": "member"})
                event = GroupAtMessageCreateEvent.model_validate(data)
            else:
                data["author"] = {"id": "synthetic-user", "user_openid": "synthetic-user"}
                event = C2CMessageCreateEvent.model_validate(data)
            await handle_event(bot, event)
        task = asyncio.create_task(deliver())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def forbidden_send(*args, **kwargs):
        sent.append(True)
        raise AssertionError("pairing must never send")

    async def shutdown(self):
        shutdowns.append(True)
        await original_shutdown(self)
        if scenario == "shutdown_logged_error":
            raise RuntimeError("SYNTHETIC-SECRET-MUST-NOT-LEAK")

    def run_driver():
        original_run()
        if scenario == "shutdown_exception":
            raise RuntimeError("SYNTHETIC-SECRET-MUST-NOT-LEAK")

    original_initialize = qq_bot.initialize

    def initialize(*args):
        if scenario == "initialize_error":
            raise ValueError("SYNTHETIC-SECRET-MUST-NOT-LEAK")
        return original_initialize(*args)

    with patch.object(pairing.sys, "stdout", output), \
         patch.object(pairing.secrets, "token_urlsafe", return_value="OFFLINE-ONLY"), \
         patch.object(pairing, "PairingSession", side_effect=make_session), \
         patch.object(Adapter, "startup", fake_startup), \
         patch.object(Adapter, "shutdown", shutdown), \
         patch.object(Bot, "send", forbidden_send), \
         patch.object(nonebot, "run", run_driver), \
         patch.object(qq_bot, "initialize", initialize):
        secret = None if scenario == "sdk_config_error" else "synthetic-secret"
        result = pairing.run(PilotConfig("synthetic-app", secret),
                             "group" if scenario == "group" else "c2c", 30)
    text = output.getvalue()
    assert not blocked and not sent
    assert "SYNTHETIC-SECRET-MUST-NOT-LEAK" not in text
    assert "synthetic-secret" not in text
    assert "hikari_bot.core.config" not in sys.modules
    assert "hikari_bot.qq_official.plugin" not in sys.modules
    assert not any(n.startswith("hikari_bot.plugins") for n in sys.modules)
    expected = {"c2c": 0, "group": 0, "timeout": 2, "initialize_error": 1,
                "sdk_config_error": 1,
                "shutdown_exception": 3, "shutdown_logged_error": 3}[scenario]
    assert result == expected, (scenario, result, text)
    if expected in {0, 3}:
        assert text.count('"openid"') == 1, text
        assert shutdowns, text
    else:
        assert '"openid"' not in text, text
    if expected == 0:
        assert "stopped cleanly" in text and "PAIR_DIAGNOSTIC" not in text, text
    if expected == 3:
        assert '"matched": true' in text and '"phase": "shutdown"' in text, text
    if scenario == "initialize_error":
        assert '"phase": "initialize"' in text and '"type": "ValueError"' in text, text
    if scenario == "sdk_config_error":
        assert '"phase": "adapter_register"' in text and '"type": "ValidationError"' in text, text
    print("PASS " + scenario + ": synthetic lifecycle, no secrets/network/business/send")


class LifecycleTests(unittest.TestCase):
    def test_real_driver_lifecycle_in_isolated_processes(self):
        for scenario in ("c2c", "group", "timeout", "initialize_error", "sdk_config_error",
                         "shutdown_exception", "shutdown_logged_error"):
            with self.subTest(scenario=scenario):
                # Deliberately do not inherit or inspect the user's environment.
                env = {"SystemRoot": os.environ.get("SystemRoot", "C:/Windows"),
                       "TEMP": tempfile.gettempdir(), "TMP": tempfile.gettempdir()}
                result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                         "--probe", scenario], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        probe(sys.argv[2])
    else:
        unittest.main()
