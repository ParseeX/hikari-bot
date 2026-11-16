"""Explicit receive-only discovery. No business imports or persistent storage."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time

from .diagnostics import Diagnostics


class PairingSession:
    def __init__(self, channel, timeout, *, clock=time.monotonic):
        if channel not in {"c2c", "group"} or not 30 <= timeout <= 300:
            raise ValueError("Invalid pairing channel or timeout")
        self.channel = channel
        self.phrase = "hikari-pair " + secrets.token_urlsafe(32)
        self.clock = clock
        self.deadline = clock() + timeout
        self.finished = False
        self.matched = False

    def expire(self):
        if self.finished or self.clock() < self.deadline:
            return False
        self.finished = True
        self.phrase = ""
        return True

    def accept(self, channel, text):
        if self.finished or self.expire() or channel != self.channel:
            return False
        if len(text) > 256 or not hmac.compare_digest(
                text.strip().encode("utf-8"), self.phrase.encode("utf-8")):
            return False
        self.finished = True
        self.matched = True
        self.phrase = ""
        return True


def register_receiver(session, driver, output):
    from nonebot import on_message
    from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent, MessageEvent

    matcher = on_message(priority=0, block=True)

    @matcher.handle()
    async def receive(event: MessageEvent):
        if isinstance(event, GroupAtMessageCreateEvent):
            channel = "group"
        elif isinstance(event, C2CMessageCreateEvent):
            channel = "c2c"
        else:
            return
        if session.accept(channel, event.get_plaintext()):
            # Do not inspect/report any identity until the challenge matches.
            openid = event.group_openid if channel == "group" else event.author.user_openid
            try:
                output(json.dumps({"channel": channel, "openid": openid}, ensure_ascii=True))
            finally:
                # Force-cancelling the None driver TaskGroup can break AnyIO's
                # cancel-scope stack on Windows. Let shutdown hooks run normally.
                driver.exit()

    timer = None

    def expired():
        if session.expire() or (session.finished and not session.matched):
            try:
                output("Pairing timed out; no identity saved.")
            finally:
                driver.exit()

    @driver.on_startup
    async def start_timer():
        nonlocal timer
        timer = asyncio.get_running_loop().call_later(
            max(0, session.deadline - session.clock()), expired)

    @driver.on_shutdown
    async def stop_timer():
        if timer is not None:
            timer.cancel()

    return matcher


def run(config, channel, timeout):
    # Keep our output separate; suppress SDK logs/tracebacks containing payloads.
    terminal = sys.stdout

    def output(message):
        print(message, file=terminal, flush=True)

    session = PairingSession(channel, timeout)

    def deadline_exit():
        try:
            output("Pairing matched; shutdown deadline reached." if session.matched else
                   "Pairing process deadline reached; exiting.")
        finally:
            os._exit(3 if session.matched else 2)  # Bounds a stuck startup/shutdown.

    watchdog = threading.Timer(timeout + 5, deadline_exit)
    watchdog.daemon = True
    watchdog.start()
    previous_disable = logging.root.manager.disable
    diagnostic = Diagnostics(output, session)
    log_sink = None
    nonebot = None
    try:
        output(f"Receive-only pairing: {channel}; expires in {timeout}s. Send exactly:")
        output(session.phrase)
        with open(os.devnull, "w") as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            logging.disable(logging.CRITICAL)
            diagnostic.phase = "sdk_import"
            import nonebot
            from nonebot.adapters.qq import Adapter
            from qq_bot import initialize

            nonebot.logger.remove()
            # Read exception metadata only; never echo SDK messages or tracebacks.
            log_sink = nonebot.logger.add(diagnostic.sink, level="ERROR", format=lambda record: "{level}",
                                          backtrace=False, diagnose=False)
            diagnostic.phase = "initialize"
            initialize(nonebot, config)
            driver = nonebot.get_driver()
            diagnostic.phase = "receiver_register"
            register_receiver(session, driver, output)
            diagnostic.phase = "adapter_register"
            driver.register_adapter(Adapter)

            @driver.on_shutdown
            async def mark_shutdown():
                diagnostic.phase = "shutdown"

            diagnostic.phase = "runtime"
            nonebot.run()
        if session.matched:
            output("Pairing matched; shutdown warning." if diagnostic.shutdown_failed else
                   "Pairing matched; process stopped cleanly.")
            return 3 if diagnostic.shutdown_failed else 0
        if session.finished:
            return 2
        diagnostic.report(None, code="stopped_without_match")
        return 1
    except KeyboardInterrupt:
        output("Pairing matched; interrupted during shutdown." if session.matched else
               "Pairing cancelled; no identity saved.")
        return 3 if session.matched else 1
    except Exception as exc:
        diagnostic.report(exc)
        output("Pairing matched; shutdown warning." if session.matched else
               "Pairing stopped before a match; see the safe diagnostic above.")
        return 3 if session.matched else 1
    finally:
        if nonebot is not None and log_sink is not None:
            nonebot.logger.remove(log_sink)
        watchdog.cancel()
        session.phrase = ""
        logging.disable(previous_disable)
