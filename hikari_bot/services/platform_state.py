"""官方入口的独立绑定、标签与待启用订阅；绝不打开旧 hikari.db。"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import hashlib
import json
import time

from .platform import Identity


class PlatformState:
    def __init__(self, directory):
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError("状态目录必须为绝对路径")
        self.path = directory / "qq-official-state.sqlite3"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bindings (actor TEXT PRIMARY KEY, player TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tags (actor TEXT, name TEXT, value TEXT, PRIMARY KEY(actor,name));
                CREATE TABLE IF NOT EXISTS subscriptions (
                    target TEXT, player TEXT, status TEXT NOT NULL, PRIMARY KEY(target,player));
                CREATE TABLE IF NOT EXISTS event_claims (digest TEXT PRIMARY KEY, expires REAL NOT NULL);
            ''')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(identity):
        if not isinstance(identity, Identity) or identity.platform != "qq_official":
            raise ValueError("仅接受明确的官方平台身份")
        return identity.key

    def binding(self, actor):
        key = self.key(actor)
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute("SELECT player FROM bindings WHERE actor=?", (key,)).fetchone()
            return row[0] if row else None

    def claim_event(self, request, *, now=None):
        """独立库原子去重，重启/重连不重复执行仍有效的旧事件。"""
        if not request.event_id:
            return False
        key = json.dumps([self.key(request.actor), self.key(request.conversation), request.event_id])
        digest = hashlib.sha256(key.encode()).hexdigest()
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM event_claims WHERE expires<=?', (now,))
            if db.execute('SELECT COUNT(*) FROM event_claims').fetchone()[0] >= 4096:
                return False
            return db.execute('INSERT OR IGNORE INTO event_claims VALUES (?,?)', (digest, now + 3600)).rowcount == 1

    def bind(self, actor, player):
        if not player or len(player) > 200:
            raise ValueError("MyCard 用户名无效")
        with self.connect() as db:
            db.execute("INSERT INTO bindings VALUES (?,?) ON CONFLICT(actor) DO UPDATE SET player=excluded.player",
                       (self.key(actor), player))

    def subscribe(self, target, player):
        if not player or len(player) > 200:
            raise ValueError("MyCard 用户名无效")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)",
                       (self.key(target), player, "paused_platform_unverified"))

    def unsubscribe(self, target, player):
        with self.connect() as db:
            db.execute("DELETE FROM subscriptions WHERE target=? AND player=?", (self.key(target), player))

    def subscriptions(self, target):
        if not self.path.exists():
            return []
        with self.connect() as db:
            return db.execute("SELECT player,status FROM subscriptions WHERE target=? ORDER BY player", (self.key(target),)).fetchall()
