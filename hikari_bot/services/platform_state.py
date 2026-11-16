"""官方状态独立保存；已核验关联通过只读服务读取旧绑定与订阅。"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import hashlib
import json
import time

from .platform import Identity


class BindingConflict(ValueError):
    """同一个官方用户存在不同的旧绑定，需要本人明确选择。"""


class PlatformState:
    def __init__(self, directory, *, legacy=None):
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError("状态目录必须为绝对路径")
        self.path = directory / "qq-official-state.sqlite3"
        self.legacy = legacy

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bindings (actor TEXT PRIMARY KEY, player TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_bindings (actor TEXT PRIMARY KEY, player TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tags (actor TEXT, name TEXT, value TEXT, PRIMARY KEY(actor,name));
                CREATE TABLE IF NOT EXISTS subscriptions (
                    target TEXT, player TEXT, status TEXT NOT NULL, PRIMARY KEY(target,player));
                CREATE TABLE IF NOT EXISTS event_claims (digest TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS verified_links (
                    actor TEXT PRIMARY KEY, legacy_user TEXT NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS verified_member_links (
                    actor TEXT PRIMARY KEY, user_actor TEXT NOT NULL, evidence TEXT NOT NULL);
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
            key = self._binding_key(db, actor)
            row = db.execute("SELECT player FROM shared_bindings WHERE actor=?", (key,)).fetchone()
            if row:
                return row[0]
            # 兼容旧的分群记录，只读汇总，不批量改写或删除原记录。
            players = set()
            for old_key, player in db.execute('SELECT actor,player FROM bindings'):
                old = self._identity(old_key)
                if old.app == actor.app and self._binding_key(db, old) == key:
                    players.add(player)
            link = db.execute('SELECT legacy_user FROM verified_links WHERE actor=?', (key,)).fetchone()
        if link and self.legacy:
            player = self.legacy.binding(link[0])
            if player:
                players.add(player)
        if len(players) > 1:
            raise BindingConflict('旧绑定不一致')
        return next(iter(players), None)

    @staticmethod
    def _identity(key):
        platform, app, kind, scope, opaque = json.loads(key)
        return Identity(platform, app, kind, opaque, scope)

    def _binding_key(self, db, actor):
        self.key(actor)
        if actor.kind not in {'member', 'user'}:
            raise ValueError('绑定必须属于用户')
        if actor.kind == 'member':
            linked = set()
            for member_key, user_key in db.execute('SELECT actor,user_actor FROM verified_member_links'):
                member = self._identity(member_key)
                if member.app == actor.app and member.opaque_id == actor.opaque_id:
                    linked.add(user_key)
            if len(linked) > 1:
                raise BindingConflict('已核验身份关联不一致')
            if linked:
                return linked.pop()
        # 仅 MyCard 默认查询用户名共用；事件身份、权限和订阅目标仍带原范围。
        return Identity('qq_official', actor.app, 'user', actor.opaque_id).key

    def link_member(self, member, user, *, evidence):
        """人工核验后的同人绑定关联；不改变事件身份、订阅目标或管理员权限。"""
        if (member.kind != 'member' or user.kind != 'user' or member.app != user.app
                or not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError('必须核验同一应用中的群成员与私聊身份并记录依据')
        member_key, user_key = self.key(member), self.key(user)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT user_actor FROM verified_member_links WHERE actor=?', (member_key,)).fetchone()
            if existing:
                if existing[0] != user_key:
                    raise ValueError('该群成员已关联其他身份，未覆盖')
                return
            local = db.execute('SELECT player FROM shared_bindings WHERE actor=?',
                               (self._binding_key(db, member),)).fetchone()
            if local is None:
                local = db.execute('SELECT player FROM bindings WHERE actor=?', (member_key,)).fetchone()
            canonical = db.execute('SELECT player FROM shared_bindings WHERE actor=?', (user_key,)).fetchone()
            if canonical is None:
                canonical = db.execute('SELECT player FROM bindings WHERE actor=?', (user_key,)).fetchone()
            if canonical:
                player = canonical[0]
            else:
                legacy = db.execute('SELECT legacy_user FROM verified_links WHERE actor=?', (user_key,)).fetchone()
                player = self.legacy.binding(legacy[0]) if legacy and self.legacy else None
            if local and local[0] != player:
                raise ValueError('群内与私聊已有不同绑定，需明确选择后再关联；未覆盖记录')
            db.execute('INSERT INTO verified_member_links VALUES (?,?,?)', (member_key, user_key, evidence))

    def link_legacy(self, actor, legacy_user, *, evidence):
        """仅供受控部署工具调用；不是聊天命令，不按昵称/数字相同自动关联。"""
        if actor.kind != 'user' or not legacy_user.isascii() or not legacy_user.isdigit() or not evidence:
            raise ValueError('必须核验私聊身份并记录关联依据')
        if self.legacy is None or self.legacy.binding(legacy_user) is None:
            raise ValueError('原绑定不存在，未关联')
        with self.connect() as db:
            db.execute('INSERT INTO verified_links VALUES (?,?,?) ON CONFLICT(actor) DO UPDATE SET '
                       'legacy_user=excluded.legacy_user,evidence=excluded.evidence',
                       (self.key(actor), legacy_user, evidence))
            # 去掉此前临时复制的绑定，之后直接读旧库；旧库没有任何写操作。
            db.execute('DELETE FROM bindings WHERE actor=?', (self.key(actor),))
            db.execute('DELETE FROM shared_bindings WHERE actor=?', (self.key(actor),))

    def legacy_subscriptions(self, target):
        if not self.legacy or not self.path.exists() or target.kind != 'user':
            return []
        with self.connect() as db:
            link = db.execute('SELECT legacy_user FROM verified_links WHERE actor=?', (self.key(target),)).fetchone()
        return self.legacy.subscriptions(link[0]) if link else []

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
            db.execute("INSERT INTO shared_bindings VALUES (?,?) ON CONFLICT(actor) DO UPDATE SET player=excluded.player",
                       (self._binding_key(db, actor), player))

    def subscribe(self, target, player):
        if not player or len(player) > 200:
            raise ValueError("MyCard 用户名无效")
        with self.connect() as db:
            db.execute("INSERT INTO subscriptions VALUES (?,?,?) ON CONFLICT(target,player) DO UPDATE SET status=excluded.status",
                       (self.key(target), player, "paused_platform_unverified"))

    def unsubscribe(self, target, player):
        inherited = player in self.legacy_subscriptions(target)
        with self.connect() as db:
            if inherited:
                db.execute("INSERT INTO subscriptions VALUES (?,?,'disabled') ON CONFLICT(target,player) "
                           "DO UPDATE SET status='disabled'", (self.key(target), player))
            else:
                db.execute("DELETE FROM subscriptions WHERE target=? AND player=?", (self.key(target), player))

    def subscriptions(self, target):
        if not self.path.exists():
            return []
        with self.connect() as db:
            rows = dict(db.execute("SELECT player,status FROM subscriptions WHERE target=?", (self.key(target),)).fetchall())
        for player in self.legacy_subscriptions(target):
            rows.setdefault(player, 'paused_platform_unverified')
        return sorted((player, status) for player, status in rows.items() if status != 'disabled')
