"""同一主机上采集端与官方发送端的有界队列，不保存 QQ 凭据。"""
from contextlib import contextmanager
from pathlib import Path
import base64
import hashlib
import json
import os
import sqlite3
import time

from .platform import Identity, Result


def encode(result):
    value = dict(text=result.text, images=[base64.b64encode(x).decode() for x in result.images],
                 file=base64.b64encode(result.file).decode() if result.file else None,
                 filename=result.filename)
    if getattr(result.text, 'mode', None) in {'text', 'markdown'}:
        value['presentation'] = {'key': result.text.key, 'mode': result.text.mode}
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw.encode()) > 40 * 1024 * 1024:
        raise ValueError('跨入口结果超过40 MiB，请使用网页或拆分文件')
    return raw


def decode(raw):
    value = json.loads(raw)
    presentation = value.get('presentation')
    if isinstance(presentation, dict) and presentation.get('mode') in {'text', 'markdown'}:
        from hikari_bot.qq_official.output_templates import Output
        value['text'] = Output(value['text'], str(presentation.get('key', '')), presentation['mode'])
    return Result(value['text'], tuple(base64.b64decode(x, validate=True) for x in value['images']),
                  base64.b64decode(value['file'], validate=True) if value['file'] else None, value['filename'])


class OfficialBus:
    def __init__(self, directory, *, clock=time.time):
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError('队列目录必须为绝对路径')
        self.path, self.clock = directory / 'official-bus.sqlite3', clock

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS routes (
                    target TEXT PRIMARY KEY, app TEXT NOT NULL, admin INTEGER NOT NULL, allowed INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS consent (
                    target TEXT, topic TEXT, enabled INTEGER NOT NULL, PRIMARY KEY(target,topic));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT, target TEXT, topic TEXT, body TEXT,
                    status TEXT, created REAL, expires REAL, updated REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS health (name TEXT PRIMARY KEY, updated REAL);
                CREATE TABLE IF NOT EXISTS settings (target TEXT,name TEXT,value INTEGER,PRIMARY KEY(target,name));
            ''')
            with db:
                yield db
        finally:
            db.close()

    def configure(self, config):
        with self.connect() as db:
            db.execute('UPDATE routes SET allowed=0,admin=0 WHERE app=?', (config.app_id,))
            for kind, values in [('user', config.users), ('group', config.groups)]:
                for value in values:
                    key = Identity('qq_official', config.app_id, kind, value).key
                    db.execute('INSERT INTO routes VALUES (?,?,?,1) ON CONFLICT(target) DO UPDATE SET '
                               'allowed=1,admin=excluded.admin', (key, config.app_id, int(kind == 'user' and value in config.admins)))

    def allow_topic(self, target, topic, enabled):
        with self.connect() as db:
            row = db.execute('SELECT admin FROM routes WHERE target=? AND allowed=1', (target.key,)).fetchone()
            if not row or (topic == 'admin' and not row['admin']):
                raise ValueError('未授权的通知对象')
            db.execute('INSERT INTO consent VALUES (?,?,?) ON CONFLICT(target,topic) DO UPDATE SET enabled=excluded.enabled',
                       (target.key, topic, int(enabled)))
            if not enabled:
                db.execute("UPDATE jobs SET status='cancelled' WHERE kind='notice' AND target=? AND topic=? AND status='pending'",
                           (target.key, topic))

    def topics(self, target):
        with self.connect() as db:
            return {r['topic'] for r in db.execute('SELECT topic FROM consent WHERE target=? AND enabled=1', (target.key,))}

    def recipients(self, topic):
        with self.connect() as db:
            rows = db.execute('SELECT r.target FROM routes r JOIN consent c ON r.target=c.target '
                              'WHERE r.allowed=1 AND c.enabled=1 AND c.topic=? AND (? != \'admin\' OR r.admin=1)', (topic, topic))
            return [r['target'] for r in rows]

    def enqueue(self, kind, target, topic, body, dedupe, *, lifetime=300):
        now = self.clock()
        key = hashlib.sha256(json.dumps([kind, target, topic, dedupe]).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("DELETE FROM jobs WHERE updated<? AND status NOT IN ('pending','processing')", (now - 86400,))
            db.execute("UPDATE jobs SET status='expired',body='',updated=? WHERE status='pending' AND expires<=?", (now, now))
            if db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= 2048:
                raise ValueError('通知/管理队列已满')
            if db.execute('SELECT coalesce(sum(length(body)),0) FROM jobs').fetchone()[0] + len(body) > 256 * 1024 * 1024:
                raise ValueError('通知/管理队列存储已满')
            db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                       (key, kind, target, topic, body, 'pending', now, now + lifetime, now))
        return key

    def publish(self, result, *, dedupe, topic='admin', targets=None, subscription=None):
        targets = self.recipients(topic) if targets is None else targets
        permitted = set(self.recipients(topic))
        for target in targets:
            if target not in permitted:
                continue
            # 每项一个媒体，避免适配器只取最后一个媒体而丢失前面的内容。
            parts = [Result(result.text, result.images[:1], result.file, result.filename)]
            parts.extend(Result(images=(image,)) for image in result.images[1:])
            for index, part in enumerate(parts):
                body = json.loads(encode(part))
                if subscription is not None:
                    body['subscription'] = subscription
                self.enqueue('notice', target, topic, json.dumps(body, ensure_ascii=False), f'{dedupe}:{index}')

    def claim(self, kind, *, app=None, daily_limit=20, topic=None):
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # 进程死亡后的不确定发送/操作不自动重放。
            db.execute("UPDATE jobs SET status='unknown',updated=? WHERE status='processing' AND updated<?", (now, now - 180))
            db.execute("UPDATE jobs SET status='expired',body='',updated=? WHERE status='pending' AND expires<=?", (now, now))
            rows = db.execute("SELECT j.*,r.admin,r.app,r.allowed FROM jobs j JOIN routes r ON j.target=r.target "
                              "WHERE j.kind=? AND j.status='pending' AND (? IS NULL OR r.app=?) "
                              "AND (? IS NULL OR j.topic=?) ORDER BY j.created LIMIT 100",
                              (kind, app, app, topic, topic)).fetchall()
            for row in rows:
                if topic is not None and row['topic'] != topic:
                    continue
                if app is not None and row['app'] != app:
                    continue
                authorized = bool(row['allowed'])
                if kind == 'control':
                    authorized = authorized and bool(row['admin'])
                else:
                    consent = db.execute('SELECT enabled FROM consent WHERE target=? AND topic=?', (row['target'], row['topic'])).fetchone()
                    authorized = authorized and bool(consent and consent[0]) and (row['topic'] != 'admin' or row['admin'])
                if not authorized:
                    db.execute("UPDATE jobs SET status='denied',body='',updated=? WHERE id=?", (now, row['id']))
                    continue
                if kind == 'notice':
                    paused = db.execute("SELECT value FROM settings WHERE target=? AND name='pause_until'", (row['target'],)).fetchone()
                    if paused and paused[0] > now:
                        continue
                    counts = db.execute("SELECT count(*),max(updated) FROM jobs WHERE kind='notice' AND target=? "
                                        "AND status IN ('api_accepted','failed','unknown','processing') AND updated>?",
                                        (row['target'], now - 86400)).fetchone()
                    if counts[0] >= daily_limit or (counts[1] and counts[1] > now - 5):
                        continue
                db.execute("UPDATE jobs SET status='processing',updated=? WHERE id=?", (now, row['id']))
                return dict(row)
        return None

    def finish(self, key, status, result=None):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,result=?,body=\'\',updated=? WHERE id=? AND status=\'processing\'',
                       (status, encode(result) if result else None, self.clock(), key))

    def response(self, key):
        with self.connect() as db:
            row = db.execute('SELECT status,result FROM jobs WHERE id=?', (key,)).fetchone()
        return (row['status'], decode(row['result']) if row['result'] else None) if row else ('missing', None)

    def heartbeat(self):
        with self.connect() as db:
            db.execute("INSERT INTO health VALUES ('backend',?) ON CONFLICT(name) DO UPDATE SET updated=excluded.updated", (self.clock(),))

    def set_setting(self, target, name, value):
        with self.connect() as db:
            db.execute('INSERT INTO settings VALUES (?,?,?) ON CONFLICT(target,name) DO UPDATE SET value=excluded.value',
                       (target, name, int(value)))

    def setting(self, target, name, default=True):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE target=? AND name=?', (target, name)).fetchone()
        return bool(row[0]) if row else default

    def status(self):
        with self.connect() as db:
            beat = db.execute("SELECT updated FROM health WHERE name='backend'").fetchone()
            counts = dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall())
        return bool(beat and self.clock() - beat[0] < 15), counts


def backend_bus():
    value = os.getenv('HIKARI_OFFICIAL_STATE_DIR')
    return OfficialBus(value) if value else None
