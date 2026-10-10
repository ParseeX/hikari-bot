"""持久化玩家随机短码；只有 Bot 查询生成，网站只读解析。"""
from contextlib import closing
from pathlib import Path
import re
import secrets
import sqlite3
import time


class ShortLinks:
    def __init__(self, path: Path):
        self.path = path

    def url(self, player: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            # 串行处理同一玩家的首次查询，保证并发请求获得同一个短码。
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS player_links (
                code TEXT PRIMARY KEY, player TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            )''')
            row = db.execute('SELECT code FROM player_links WHERE player=?', (player,)).fetchone()
            if row:
                code = row[0]
            else:
                for _ in range(10):
                    code = secrets.token_urlsafe(9)
                    try:
                        db.execute('INSERT INTO player_links VALUES(?,?,?)', (code, player, time.time()))
                        break
                    except sqlite3.IntegrityError:
                        # 极低概率短码碰撞时重新生成，不覆盖其他玩家的映射。
                        continue
                else:
                    raise ValueError('短码生成失败')
        return 'https://ygo.xyk.one/mc/' + code

    def player(self, code: str) -> str | None:
        if not re.fullmatch(r'[A-Za-z0-9_-]{12}', code) or not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro',
                                     uri=True, timeout=10)) as db:
            row = db.execute('SELECT player FROM player_links WHERE code=?', (code,)).fetchone()
        return row[0] if row else None
