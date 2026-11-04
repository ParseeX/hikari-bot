"""正常账号的 YGOPro 观战协议；不发送对局操作，活跃对局没有总时限。"""
import asyncio
import base64
import json
import os
import struct
import tempfile
import time
from pathlib import Path

import aiohttp


class CredentialError(RuntimeError):
    pass


class HandshakeTimeoutError(TimeoutError):
    pass


class Credentials:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._cached = None
        self.email_verified = None
        self._expires = 0.0
        self._mtime = 0
        self._login_retry_at = 0
        self._login_blocked_mtime = None
        self._login_source_mtime = None

    def invalidate(self):
        self._expires = 0

    async def _auth_token(self, session, token):
        async with session.get('https://sapi.moecube.com:444/accounts/authUser',
                               headers={'Authorization': 'Bearer ' + token}, allow_redirects=False) as response:
            if response.status in (401, 403):
                return None
            if response.status != 200:
                raise CredentialError(f'MyCard 认证暂不可用（HTTP {response.status}）')
            return await response.json()

    async def _password_login(self, session, data, mtime):
        account = data.get('account') or data.get('username')
        password = data.get('password')
        if not isinstance(account, str) or not account or not isinstance(password, str) or not password:
            raise CredentialError('MyCard Token 已失效，且未配置账号密码')
        if self._login_blocked_mtime == mtime:
            raise CredentialError('MyCard 密码登录需人工处理，请更新凭据文件')
        if time.monotonic() < self._login_retry_at:
            raise CredentialError('MyCard 密码登录暂不可用，稍后重试')
        self._login_retry_at = time.monotonic() + 300
        async with session.post('https://sapi.moecube.com:444/accounts/signin',
                                json={'account': account, 'password': password},
                                allow_redirects=False) as response:
            if response.status in (400, 401, 403) or 300 <= response.status < 400:
                self._login_blocked_mtime = mtime
                raise CredentialError('MyCard 密码登录被拒绝或需要额外验证，请更新凭据文件')
            if response.status != 200:
                raise CredentialError(f'MyCard 登录暂不可用（HTTP {response.status}）')
            result = await response.json()
        token = result.get('token') if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            self._login_blocked_mtime = mtime
            raise CredentialError('MyCard 登录未返回 Token，需要人工处理')
        return token

    def _save_token(self, data, token, original):
        # 不覆盖登录期间由用户改过的密码；落盘只替换 Token，文件保持仅本人可读。
        if self.path.read_text(encoding='utf-8') != original:
            raise CredentialError('凭据文件已变更，等待重新加载')
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix='.credentials-', suffix='.tmp')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump({**data, 'token': token}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def get(self) -> tuple[str, int]:
        async with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
                if self._cached and mtime == self._mtime and time.monotonic() < self._expires:
                    return self._cached
                original = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
                data = json.loads(original)
                password_path = None
                revision = (mtime, None)
                if data.get('password_file'):
                    if data.get('password'):
                        raise CredentialError('请只配置共享密码文件或独立密码中的一种')
                    password_path = Path(data['password_file']).expanduser()
                    if not password_path.is_absolute():
                        password_path = self.path.parent / password_path
                    revision = (mtime, password_path.stat().st_mtime_ns)
                if revision != self._login_source_mtime:
                    self._login_retry_at = 0
                    self._login_blocked_mtime = None
                    self._login_source_mtime = revision
                renewed = None
                email_verified = None
                if data.get("token") or data.get('password') or password_path:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                        info = await self._auth_token(session, data['token']) if data.get('token') else None
                        if info is None:
                            login_data = data
                            if password_path:
                                shared = json.loads(await asyncio.to_thread(password_path.read_text, encoding='utf-8'))
                                login_data = {**data, 'password': shared.get('password')}
                            renewed = await self._password_login(session, login_data, revision)
                            info = await self._auth_token(session, renewed)
                            if info is None:
                                self._login_blocked_mtime = revision
                                raise CredentialError('MyCard 新登录凭据未通过验证，需要人工处理')
                    # 邮箱状态仅供管理页提示，录像权限由目标服务器决定。
                    if type(info.get('active')) is bool:
                        email_verified = info['active']
                    username, secret = info.get("username"), info.get("u16Secret")
                    if data.get('username') and username != data['username']:
                        raise CredentialError('MyCard 登录返回的账号与凭据文件不匹配')
                else:
                    username, secret = data.get("username"), data.get("u16_secret")
                if not isinstance(username, str) or not username or type(secret) is not int or secret < 0:
                    raise CredentialError("MyCard 凭据缺少用户名或有效观战密钥")
                if renewed:
                    await asyncio.to_thread(self._save_token, data, renewed, original)
                    mtime = self.path.stat().st_mtime_ns
                    self._login_retry_at = 0
                    self._login_blocked_mtime = None
                self._cached = (username, secret)
                self.email_verified = email_verified
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
                connected=None, handshake_seconds=15) -> bool:
    """返回是否收到 DUEL_END。房间从列表消失后仍留出时间接收延迟录像。"""
    packets = join_packets(*identity, room_id, version)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    end_task = asyncio.create_task(ended.wait())
    frame_task = None
    deadline = None
    spectator = False
    handshake_deadline = asyncio.get_running_loop().time() + handshake_seconds
    try:
        writer.write(packets)
        await writer.drain()
        while True:
            frame_task = asyncio.create_task(read_frame(reader))
            if not ended.is_set():
                timeout = None if spectator else max(0, handshake_deadline - asyncio.get_running_loop().time())
                done, _ = await asyncio.wait((frame_task, end_task), timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise HandshakeTimeoutError("服务器未在握手期限内授予观战身份")
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
        try:
            await asyncio.wait_for(writer.wait_closed(), 5)
        except (OSError, asyncio.TimeoutError):
            pass
