"""单次官方注册请求；备用机仅转发 TLS，不保存或处理账号密码。"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import time

import aiohttp


API = 'https://sapi.moecube.com:444/accounts'
SERVERS = {'primary': '主服务器', 'backup': '备用服务器'}


class RegistrationError(ValueError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class TLSForwardConnector(aiohttp.UnixConnector):
    async def _create_connection(self, req, traces, timeout):
        # aiohttp 的普通 UnixConnector 不执行 TLS，必须在主服务器显式建立 TLS。
        if req.url.scheme != 'https' or req.url.raw_host != 'sapi.moecube.com' or req.url.port != 444:
            raise RegistrationError('注册转发只允许访问官方账号服务')
        _, protocol = await self._loop.create_unix_connection(
            self._factory, self.path, ssl=ssl.create_default_context(),
            server_hostname='sapi.moecube.com', ssl_handshake_timeout=20)
        return protocol


@asynccontextmanager
async def registration_session(server):
    if server not in SERVERS:
        raise RegistrationError('请选择有效的注册服务器')
    timeout = aiohttp.ClientTimeout(total=35)
    if server == 'primary':
        async with aiohttp.ClientSession(timeout=timeout) as session:
            yield session
        return
    # Unix socket 位于私有临时目录，不新增公网或固定本地端口。
    with tempfile.TemporaryDirectory(prefix='hikari-signup-') as directory:
        socket = Path(directory) / 'https.sock'
        process = await asyncio.create_subprocess_exec(
            'ssh', '-N', '-T', '-o', 'BatchMode=yes', '-o', 'IdentityAgent=none',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=2', '-o', 'StreamLocalBindMask=0177',
            '-L', f'{socket}:sapi.moecube.com:444', 'xyk@103.27.186.14',
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(15):
                while not socket.exists():
                    if process.returncode is not None:
                        raise RegistrationError('备用服务器连接失败，请稍后重试')
                    await asyncio.sleep(0.05)
            # URL 仍为官方 HTTPS 域名，aiohttp 按此域名验证证书和发送 SNI。
            async with aiohttp.ClientSession(timeout=timeout, connector=TLSForwardConnector(path=str(socket))) as session:
                yield session
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


def failure_message(status, result):
    # 官方错误只映射已知类别，不透传响应正文，防止密码或 Token 被回显。
    errors = {
        'i_email_exists': '邮箱已被注册，请换一个邮箱或添加已有账号',
        'i_username_exists': '用户名已被注册，请更换用户名或添加已有账号',
        'i_username_exists_or_invalid': '用户名已被占用或格式不符合要求',
        'i_email_illegal': '邮箱格式不符合官方要求',
        'i_username_illegal': '用户名不符合官方要求',
    }
    values = [result] if isinstance(result, str) else [result.get(k) for k in ('error', 'message', 'code')] if isinstance(result, dict) else []
    for value in values:
        if isinstance(value, str):
            if value in errors:
                return errors[value]
            if re.search(r'captcha|vaptcha|验证码', value, re.I):
                return '官方要求验证码，请到官方注册页面完成验证'
            if re.search(r'ip.*(limit|注册|frequen)|too.many|rate.limit|注册.{0,8}(次数|频繁)', value, re.I):
                return '该服务器出口已触发官方注册限额，本次已停止，请等待限额恢复'
    if status == 429:
        return '该服务器出口已触发官方注册限额，本次已停止，请等待限额恢复'
    return f'官方拒绝注册（HTTP {status}），请检查注册信息或到官方页面查看要求'


async def signup(username, email, password, server):
    submitted = False
    try:
        async with registration_session(server) as session:
            submitted = True
            async with session.post(API + '/signup', json={'username': username, 'email': email, 'password': password},
                                    allow_redirects=False) as response:
                if response.status >= 500:
                    raise RegistrationError('官方服务异常，注册结果未确认；请先尝试添加已有账号，不要重复注册', uncertain=True)
                try:
                    result = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    result = None
                if response.status not in (200, 201):
                    raise RegistrationError(failure_message(response.status, result))
                if not isinstance(result, dict) or not isinstance(result.get('token'), str) or not result['token']:
                    raise RegistrationError('官方响应未能确认注册结果；请先尝试添加已有账号，不要重复注册', uncertain=True)
                return result['token']
    except RegistrationError:
        raise
    except Exception:
        if submitted:
            raise RegistrationError('请求中断，注册结果未确认；请先尝试添加已有账号，不要重复注册', uncertain=True) from None
        raise RegistrationError('注册服务器连接失败，尚未提交注册请求') from None


class Registrations:
    def __init__(self, root):
        self.path = root / 'registration-attempts.json'
        self.lock = asyncio.Lock()

    def read(self):
        return json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}

    def save(self, records):
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix='.registrations-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(records, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def create(self, username, email, shared, server, request_id):
        email = email.strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
            raise RegistrationError('请输入有效邮箱')
        if server not in SERVERS:
            raise RegistrationError('请选择有效的注册服务器')
        if not re.fullmatch(r'[a-f0-9]{32}', request_id):
            raise RegistrationError('注册请求标识无效，请刷新页面')
        key = hashlib.sha256(username.casefold().encode()).hexdigest()
        async with self.lock:
            records = self.read()
            previous = records.get(key)
            if previous and (previous['status'] != 'rejected' or previous['request_id'] == request_id):
                raise RegistrationError('该用户名已有注册提交记录；请尝试添加已有账号，避免重复注册')
            password = json.loads(Path(shared).read_text('utf-8')).get('password')
            if not isinstance(password, str) or not password:
                raise RegistrationError('服务器共享密码配置无效')
            record = {'username': username, 'server': server, 'request_id': request_id,
                      'status': 'submitted', 'at': time.time()}
            records[key] = record
            self.save(records)
            try:
                token = await signup(username, email, password, server)
            except RegistrationError as exc:
                record['status'] = 'unknown' if exc.uncertain else 'rejected'
                self.save(records)
                raise
            # 重启或断网也不能自动重新创建账号。
            record['status'] = 'created'
            try:
                self.save(records)
            except OSError:
                raise RegistrationError('账号已注册，但本地记录保存失败；请尝试添加已有账号，无需再次注册', uncertain=True) from None
            return token
