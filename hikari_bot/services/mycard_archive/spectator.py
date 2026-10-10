"""正常账号的 YGOPro 观战协议；不发送对局操作，活跃对局没有总时限。"""
import asyncio
import base64
import json
import struct
import time
from pathlib import Path

import aiohttp


class CredentialError(RuntimeError):
    pass


class Credentials:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._cached = None
        self._expires = 0.0
        self._mtime = 0

    def invalidate(self):
        self._expires = 0

    async def get(self) -> tuple[str, int]:
        async with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
                if self._cached and mtime == self._mtime and time.monotonic() < self._expires:
                    return self._cached
                data = json.loads(await asyncio.to_thread(self.path.read_text, encoding="utf-8"))
                if data.get("token"):
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                        async with session.get("https://sapi.moecube.com:444/accounts/authUser",
                                               headers={"Authorization": "Bearer " + data["token"]}) as response:
                            if response.status != 200:
                                raise CredentialError(f"MyCard 认证失败（HTTP {response.status}）")
                            info = await response.json()
                    username, secret = info.get("username"), info.get("u16Secret")
                else:
                    username, secret = data.get("username"), data.get("u16_secret")
                if not isinstance(username, str) or not username or type(secret) is not int or secret < 0:
                    raise CredentialError("MyCard 凭据缺少用户名或有效观战密钥")
                self._cached = (username, secret)
                self._expires = time.monotonic() + 600
                self._mtime = mtime
                return self._cached
            except CredentialError:
                raise
            except Exception:
                # 不透传 HTTP 请求对象、原始响应或 JSON，避免认证信息进入日志。
                raise CredentialError("无法读取或刷新 MyCard 观战凭据") from None


def join_packets(username: str, secret: int, room_id: str, version: int) -> bytes:
    def text40(value):
        raw = value.encode("utf-16-le")
        if not raw or len(raw) > 38 or "\0" in value:
            raise ValueError("观战用户名或房间编号长度不受协议支持")
        return raw.ljust(40, b"\0")

    def frame(kind, body):
        return struct.pack("<HB", len(body) + 1, kind) + body

    buf = bytearray([208, 48, 0, 0, 0, 0])  # 官方 action=3，加入已存在的房间。
    key = secret % 65535 + 1
    for pos in (0, 2, 4):
        struct.pack_into("<H", buf, pos, struct.unpack_from("<H", buf, pos)[0] ^ key)
    password = base64.b64encode(buf).decode("ascii") + room_id
    return frame(0x10, text40(username)) + frame(0x12, struct.pack("<H2xI", version, 0) + text40(password))


async def read_frame(reader) -> tuple[int, bytes]:
    header = await reader.readexactly(2)
    size = struct.unpack("<H", header)[0]
    if size < 1:
        raise ValueError("空观战数据包")
    packet = await asyncio.wait_for(reader.readexactly(size), 30)
    return packet[0], packet[1:]


async def watch(room_id: str, identity: tuple[str, int], ended: asyncio.Event, save_replay,
                *, host="tiramisu.moecube.com", port=8911, version=0x1362, drain_seconds=30,
                connected=None) -> bool:
    """返回是否收到 DUEL_END。房间从列表消失后仍留出时间接收延迟录像。"""
    packets = join_packets(*identity, room_id, version)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    end_task = asyncio.create_task(ended.wait())
    frame_task = None
    deadline = None
    spectator = False
    try:
        writer.write(packets)
        await writer.drain()
        while True:
            frame_task = asyncio.create_task(read_frame(reader))
            if not ended.is_set():
                await asyncio.wait((frame_task, end_task), return_when=asyncio.FIRST_COMPLETED)
            if ended.is_set():
                if deadline is None:
                    deadline = asyncio.get_running_loop().time() + drain_seconds
                remaining = max(0.001, deadline - asyncio.get_running_loop().time())
                try:
                    kind, body = await asyncio.wait_for(frame_task, remaining)
                except asyncio.TimeoutError:
                    return False
            else:
                kind, body = await frame_task
            if kind == 0x13:
                if not body or body[0] & 15 != 7:
                    raise ValueError("服务器未授予观战身份")
                spectator = True
                if connected:
                    connected()
            elif kind == 0x02:
                raise CredentialError("服务器拒绝观战，请检查账号、版本或房间权限")
            elif kind == 0x17:
                if not spectator:
                    raise ValueError("未确认观战身份，拒绝接收录像")
                await save_replay(body)
            elif kind == 0x16:
                return spectator
    except asyncio.IncompleteReadError:
        return False
    finally:
        for task in (end_task, frame_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (end_task, frame_task) if task), return_exceptions=True)
        writer.close()
        await writer.wait_closed()
