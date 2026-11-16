"""srbs-only QQ gateway receive deadline, derived from the server heartbeat.

QQ adapter 1.7.1 supplies timeout=30. NoneBot's aiohttp driver interprets that
as ws_receive=30, shorter than QQ's idle heartbeat period. Keep SDK heartbeat,
authentication and dispatch intact; enforce a bounded deadline at receive().
"""
import asyncio
from contextlib import asynccontextmanager
from copy import copy
import json
import time

from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.models import HeartbeatAck, Hello, InvalidSession, Reconnect
from nonebot.internal.driver import Timeout


def heartbeat_deadline(interval_ms):
    if type(interval_ms) is not int or not 1000 <= interval_ms <= 300000:
        raise ValueError('Invalid gateway heartbeat interval')
    # Two full heartbeat periods plus scheduling/network grace, always finite.
    return max(30.0, interval_ms / 1000 * 2 + 10)


def telemetry(event, **numbers):
    allowed = {'hello', 'heartbeat_ack', 'receive_timeout', 'closed', 'server_reconnect', 'invalid_session'}
    assert event in allowed
    assert all(type(value) in (int, float) for value in numbers.values())
    print('SRBS_GATEWAY ' + json.dumps({'event': event, **numbers}, sort_keys=True), flush=True)


class GatewaySocket:
    def __init__(self, inner):
        self.inner = inner
        self.receive_seconds = 30.0  # Bound the initial Hello as before.
        self.ack_count = 0
        self.started = time.monotonic()

    async def receive(self):
        try:
            return await asyncio.wait_for(self.inner.receive(), self.receive_seconds)
        except TimeoutError:
            telemetry('receive_timeout', deadline_seconds=self.receive_seconds,
                      uptime_seconds=round(time.monotonic() - self.started, 3),
                      ack_count=self.ack_count)
            raise

    def __getattr__(self, name):
        # SDK owns authentication, heartbeat sends, closure and message payloads.
        return getattr(self.inner, name)


class SrbsQQAdapter(Adapter):
    @asynccontextmanager
    async def websocket(self, setup):
        request = copy(setup)
        # Remove only the conflicting lower-level read timer. GatewaySocket
        # bounds every SDK receive; HTTP API timeouts and TLS remain unchanged.
        request.timeout = Timeout(read=None, close=10.0)
        async with super().websocket(request) as inner:
            ws = GatewaySocket(inner)
            try:
                yield ws
            finally:
                telemetry('closed', uptime_seconds=round(time.monotonic() - ws.started, 3),
                          ack_count=ws.ack_count)

    async def receive_payload(self, bot, ws):
        payload = await super().receive_payload(bot, ws)
        if isinstance(payload, Hello):
            interval = payload.data.heartbeat_interval
            ws.receive_seconds = heartbeat_deadline(interval)
            telemetry('hello', heartbeat_ms=interval, receive_seconds=ws.receive_seconds)
        elif isinstance(payload, HeartbeatAck):
            ws.ack_count += 1
            telemetry('heartbeat_ack', ack_count=ws.ack_count,
                      uptime_seconds=round(time.monotonic() - ws.started, 3))
        elif isinstance(payload, Reconnect):
            telemetry('server_reconnect')
        elif isinstance(payload, InvalidSession):
            telemetry('invalid_session')
        return payload
