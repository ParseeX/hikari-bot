"""独立密码与可撤销的管理会话；只保存密码和会话令牌的摘要。"""
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time


SESSION_SECONDS = 12 * 60 * 60


def password_record(password):
    if not 10 <= len(password) <= 1024:
        raise ValueError('密码长度需要 10 至 1024 个字符')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return {'salt': base64.b64encode(salt).decode(), 'digest': base64.b64encode(digest).decode()}


class LoginRejected(ValueError):
    pass


class LoginLimited(ValueError):
    pass


class AdminAuth:
    def __init__(self, root: Path):
        self.root = root
        self.password_path = root / 'admin-password.json'
        self.db_path = root / 'admin-sessions.sqlite3'

    def _password(self):
        raw = self.password_path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def login(self, password, address):
        record, revision = self._password()
        now = time.time()
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS sessions (
                digest TEXT PRIMARY KEY, expires REAL NOT NULL, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (address TEXT NOT NULL, at REAL NOT NULL);''')
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE at<?', (now - 900,))
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            if db.execute('SELECT count(*) FROM attempts WHERE address=?', (address,)).fetchone()[0] >= 5:
                raise LoginLimited('尝试次数过多，请 15 分钟后重试')
            # 失败尝试先提交，不能被异常回滚；并发登录也不能越过限流。
            db.execute('INSERT INTO attempts VALUES(?,?)', (address, now))
            db.commit()
            actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(record['salt']), n=16384, r=8, p=1)
            if not secrets.compare_digest(actual, base64.b64decode(record['digest'])):
                raise LoginRejected('密码错误')
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), now + SESSION_SECONDS, revision))
            db.execute('DELETE FROM attempts WHERE address=?', (address,))
        return token

    def valid(self, token):
        if not token or len(token) > 128 or not self.db_path.exists():
            return False
        _, revision = self._password()
        with closing(sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT expires,revision FROM sessions WHERE digest=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0] > time.time() and row[1] == revision)

    def logout(self, token):
        if not token or not self.db_path.exists():
            return
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
