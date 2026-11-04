"""采集账号的持久配置和运行管理；对外快照不包含凭据及文件路径。"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from .accounts import Account
from .spectator import Credentials, CredentialError


active_runtime = None


class StaleAccounts(ValueError):
    pass


def _encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')


def _write(path, data):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.accounts-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_encoded(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_config(root, paths, concurrency):
    path = root / 'collector-accounts.json'
    if path.exists():
        data = json.loads(path.read_text('utf-8'))
        if (data.get('version') != 1 or type(data.get('concurrency')) is not int
                or not 1 <= data['concurrency'] <= 9 or not isinstance(data.get('accounts'), list)):
            raise ValueError('采集账号配置无效')
        entries = data['accounts']
        if any(not all(isinstance(a.get(k), str) and a[k] for k in ('id', 'path', 'username'))
               for a in entries) or len({a['id'] for a in entries}) != len(entries):
            raise ValueError('采集账号配置无效')
        return data
    entries, shared = [], None
    for source in paths:
        source = Path(source).resolve()
        info = {}
        try:
            info = json.loads(source.read_text('utf-8'))
        except (OSError, ValueError):
            pass
        if info.get('password_file') and shared is None:
            reference = Path(info['password_file']).expanduser()
            shared = str((source.parent / reference).resolve())
        entries.append({'id': hashlib.sha256(str(source).encode()).hexdigest()[:32],
                        'path': str(source), 'username': info.get('username') or '待认证账号'})
    return {'version': 1, 'accounts': entries, 'concurrency': concurrency,
            'shared_password_file': shared}


class AccountAdmin:
    def __init__(self, runtime, root, config):
        self.runtime = runtime
        self.root = root
        self.path = root / 'collector-accounts.json'
        self.config = config
        self.lock = asyncio.Lock()
        self.started_at = time.time()
        self.revision = self._revision()

    def _revision(self):
        raw = self.path.read_bytes() if self.path.exists() else _encoded(self.config)
        return hashlib.sha256(raw).hexdigest()

    def _check_revision(self, revision):
        if revision != self.revision or self._revision() != self.revision:
            raise StaleAccounts('账号配置已变更，请刷新后重试')

    def _save(self, config):
        # 保留上一版有效配置；移除账号只从列表移除，凭据和归档仍可恢复。
        _write(self.path.with_suffix('.json.bak'), self.config)
        _write(self.path, config)
        self.config = config
        self.revision = self._revision()

    def snapshot(self):
        runtime = self.runtime
        runtime.accounts._start_checks()
        rows = []
        by_path = {str(a.credentials.path): a for a in runtime.accounts.accounts}
        for entry in self.config['accounts']:
            account = by_path.get(entry['path'])
            room = account.room if account else None
            state = ('removing' if account and not account.enabled else
                     'watching' if room and room.connected else
                     'connecting' if account and account.busy else
                     'error' if account and account.failed else
                     'idle' if account and account.identity else 'checking')
            rows.append({'id': entry['id'], 'username': entry['username'], 'state': state,
                         'authenticated': bool(account and account.identity),
                         'email_verified': getattr(account.credentials, 'email_verified', None) if account else None,
                         'checked_at': account.checked_wall if account else 0,
                         'error': account.error if account else '',
                         'players': room.players if room else [],
                         'last_replay_at': account.last_replay_at if account else 0,
                         'replay_count': account.replay_count if account else 0})
        shared = self.config.get('shared_password_file')
        return {'revision': self.revision, 'accounts': rows,
                'concurrency': self.config['concurrency'], 'effective_concurrency': runtime.concurrency,
                'connected': sum(s.connected for s in runtime.rooms.values()),
                'waiting': len(runtime.slots.waiting), 'cooldown_seconds': int(runtime.join_gate.remaining),
                'shared_password_ready': bool(shared and Path(shared).is_file()),
                'started_at': self.started_at, 'updated_at': time.time()}

    async def add(self, revision, username):
        username = username.strip()
        if not username or len(username.encode('utf-16-le')) > 38 or any(ord(c) < 32 for c in username):
            raise ValueError('请输入有效的 MyCard 用户名（最多 19 个 UTF-16 字符）')
        async with self.lock:
            self._check_revision(revision)
            if any(a['username'] == username for a in self.config['accounts']):
                raise ValueError('该账号已在采集列表中')
            shared = self.config.get('shared_password_file')
            if not shared or not Path(shared).is_file():
                raise ValueError('服务器尚未配置共享密码')
            directory = self.root / 'collector-credentials'
            directory.mkdir(mode=0o700, exist_ok=True)
            identifier = uuid.uuid4().hex
            path = directory / (identifier + '.json')
            committed = False
            try:
                _write(path, {'username': username, 'password_file': shared})
                credentials = Credentials(path)
                identity = await credentials.get()
                self._check_revision(revision)
                config = deepcopy(self.config)
                config['accounts'].append({'id': identifier, 'username': identity[0], 'path': str(path)})
                self._save(config)
                committed = True
                account = Account(credentials, identity=identity, checked_wall=time.time())
                self.runtime.accounts.accounts.append(account)
                self.runtime.accounts.changed.set()
                await self.runtime.set_concurrency(config['concurrency'])
            finally:
                if not committed:
                    path.unlink(missing_ok=True)
            return self.snapshot()

    async def remove(self, revision, identifier):
        async with self.lock:
            self._check_revision(revision)
            entry = next((a for a in self.config['accounts'] if a['id'] == identifier), None)
            if entry is None:
                raise ValueError('账号不存在，请刷新列表')
            account = next(a for a in self.runtime.accounts.accounts if str(a.credentials.path) == entry['path'])
            config = deepcopy(self.config)
            config['accounts'].remove(entry)
            self._save(config)
            account.enabled = False
            if account.checking:
                account.checking.cancel()
                await asyncio.gather(account.checking, return_exceptions=True)
            if account.busy:
                account.room.interrupted.set()
                await account.released.wait()
            self.runtime.accounts.accounts.remove(account)
            self.runtime.accounts.changed.set()
            await self.runtime.set_concurrency(config['concurrency'])
            return self.snapshot()

    async def configure(self, revision, concurrency):
        if type(concurrency) is not int or not 1 <= concurrency <= 9:
            raise ValueError('总并发上限需为 1 至 9')
        async with self.lock:
            self._check_revision(revision)
            self._save({**self.config, 'concurrency': concurrency})
            await self.runtime.set_concurrency(concurrency)
            return self.snapshot()


async def finish_change(operation):
    """请求断开时仍完成已经保存的配置对应的运行调整。"""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
