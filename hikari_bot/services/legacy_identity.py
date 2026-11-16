"""经人工核验的身份关联只读复用旧库，不初始化或迁移旧数据库。"""
from pathlib import Path
import sqlite3


class LegacyRecords:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or not self.path.is_file():
            raise ValueError('旧记录库必须是已存在的绝对路径')

    def read(self, sql, user):
        with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            return db.execute(sql, (user,)).fetchall()

    def binding(self, user):
        rows = self.read('SELECT username FROM mycard_bindings WHERE qq_id=?', user)
        return rows[0][0] if rows else None

    def subscriptions(self, user):
        return [r[0] for r in self.read(
            "SELECT username FROM mycard_subscriptions WHERE target_type='private' AND target_id=?", user)]
