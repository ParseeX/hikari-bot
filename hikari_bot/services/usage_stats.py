"""功能调用的每日聚合；不落盘消息、群号或用户标识。"""
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import secrets
import sqlite3
import threading
import time


class UsageStats:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.seen = OrderedDict()
        self.salt = secrets.token_bytes(32)

    def initialize(self, features):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS features (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS counts (
                    day TEXT NOT NULL,feature TEXT NOT NULL,scope TEXT NOT NULL,
                    count INTEGER NOT NULL CHECK(count>=0),
                    PRIMARY KEY(day,feature,scope), CHECK(scope IN ('group','private')));
            ''')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('started_at',?)",
                       (datetime.now(timezone.utc).isoformat(),))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version','1')")
            db.executemany('INSERT OR IGNORE INTO features VALUES (?)', [(name,) for name in sorted(set(features))])
        self.path.chmod(0o600)

    def record(self, feature, scope, event_key):
        if scope not in ('group', 'private'):
            raise ValueError('invalid scope')
        # 去重仅在内存中保存带随机盐的摘要；不持久保存任何会话或消息标识。
        digest = hashlib.sha256(self.salt + repr((feature, scope, event_key)).encode()).digest()
        with self.lock:
            now = time.monotonic()
            while self.seen and next(iter(self.seen.values())) <= now - 600:
                self.seen.popitem(last=False)
            if digest in self.seen:
                return False
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=2)) as db, db:
                if not db.execute('SELECT 1 FROM features WHERE name=?', (feature,)).fetchone():
                    raise ValueError('unknown feature')
                day = datetime.now(timezone.utc).date().isoformat()
                db.execute('''INSERT INTO counts VALUES (?,?,?,1)
                    ON CONFLICT(day,feature,scope) DO UPDATE SET count=count+1''', (day, feature, scope))
            self.seen[digest] = now
            while len(self.seen) > 10000:
                self.seen.popitem(last=False)
            return True
