"""主库中的完整版本快照：首次补齐，过期后台刷新，价格不缓存。"""
import asyncio
from dataclasses import asdict
from functools import lru_cache
import json
import logging
from pathlib import Path
import sqlite3
import time

from .client import JhsUnavailable
from .models import CardVersion, normalized
from scripts.card_catalog.catalog import encode, now, upsert_product


class VersionCatalog:
    def __init__(self, path, *, ttl=86400, retry_delay=300, clock=time.time):
        self.path = Path(path)
        self.ttl, self.retry_delay, self.clock = ttl, retry_delay, clock
        self.tasks = {}
        self.retry_after = {}

    def connect(self):
        # 不创建空库或在查询时执行迁移；部署时预先建立兼容的附加表。
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def identities(db, card_id):
        return [r[0] for r in db.execute('SELECT jhs_card_id FROM jhs_cards WHERE card_id=? ORDER BY jhs_card_id', (card_id,))]

    def load(self, card_id, name_jp):
        db = self.connect()
        try:
            db.execute('BEGIN')
            if not db.execute("SELECT 1 FROM card_names WHERE card_id=? AND language='ja' AND normalized=?",
                              (card_id, normalized(name_jp))).fetchone():
                return None
            ids = self.identities(db, card_id)
            row = db.execute('SELECT * FROM jhs_card_version_sync WHERE card_id=?', (card_id,)).fetchone()
            if row and json.loads(row['jhs_card_ids_json']) == ids:
                versions = [CardVersion.from_dict(r) for r in json.loads(row['versions_json'])]
                if versions and all(normalized(v.name_jp) == normalized(name_jp) and v.card_id in ids for v in versions):
                    return ids, versions, row['checked_at']
            return ids, None, None
        finally:
            db.close()

    @staticmethod
    def validate(payloads, ids, name_jp):
        rows, seen = [], set()
        if len(payloads) != len(ids):
            raise ValueError('缺少卡片完整列表')
        for expected, data in zip(ids, payloads):
            if (not isinstance(data, dict) or data.get('complete') is not True
                    or data.get('card_id') != expected or not isinstance(data.get('versions'), list)
                    or not data['versions']):
                raise ValueError('卡片版本列表不完整')
            for row in data['versions']:
                if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                        or row.get('card_id') != expected or row.get('object_type') != 'card'
                        or normalized(row.get('name_jp') or '') != normalized(name_jp)
                        or not isinstance(row.get('rarity'), str) or not row['rarity'].strip()
                        or not isinstance(row.get('number'), str) or not isinstance(row.get('packs'), list)
                        or not row['packs'] or row['id'] in seen):
                    raise ValueError('卡片版本身份或内容冲突')
                for pack in row['packs']:
                    if (not isinstance(pack, dict) or type(pack.get('id')) is not int or pack['id'] <= 0
                            or not isinstance(pack.get('name'), str) or not pack['name'].strip()):
                        raise ValueError('商品身份不完整')
                CardVersion.from_dict(row)
                rows.append(row)
                seen.add(row['id'])
        return rows

    def save(self, card_id, name_jp, expected_ids, ids, payloads):
        rows = self.validate(payloads, ids, name_jp)
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if self.identities(db, card_id) != expected_ids:
                    raise ValueError('查询期间卡片映射发生变化')
                if not db.execute("SELECT 1 FROM card_names WHERE card_id=? AND language='ja' AND normalized=?",
                                  (card_id, normalized(name_jp))).fetchone():
                    raise ValueError('查询期间卡片名称发生变化')
                stamp = now()
                for jhs_id in ids:
                    old = db.execute('SELECT card_id FROM jhs_cards WHERE jhs_card_id=?', (jhs_id,)).fetchone()
                    if old and old[0] not in (None, card_id):
                        raise ValueError('集换社卡片已关联其他卡片')
                    db.execute('INSERT INTO jhs_cards VALUES (?,?,?,?) ON CONFLICT(jhs_card_id) DO UPDATE SET '
                               'card_id=excluded.card_id,updated_at=excluded.updated_at',
                               (jhs_id, card_id, 'complete_card_identity', stamp))
                for row in rows:
                    old = db.execute('SELECT * FROM jhs_versions WHERE jhs_version_id=?', (row['id'],)).fetchone()
                    if old and (old['jhs_card_id'] != row['card_id'] or old['object_type'] != 'card'):
                        raise ValueError('版本归属或类型发生冲突')
                    products = [(upsert_product(db, None, p, stamp), p) for p in row['packs']]
                    target = old['product_id'] if old else products[0][0]
                    stored = {**row, 'pack': next((p for pid, p in products if pid == target), products[0][1])}
                    if old and (identity := json.loads(old['source_json']).get('identity')):
                        stored['identity'] = identity
                    raw = encode(stored)
                    db.execute('INSERT INTO jhs_versions(jhs_version_id,jhs_card_id,product_id,number_raw,rarity_raw,'
                               'name_cn,name_jp,source_json,first_seen_at,last_seen_at,updated_at,object_type) '
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?,'card') ON CONFLICT(jhs_version_id) DO UPDATE SET "
                               'number_raw=excluded.number_raw,rarity_raw=excluded.rarity_raw,name_cn=excluded.name_cn,'
                               'name_jp=excluded.name_jp,source_json=excluded.source_json,last_seen_at=excluded.last_seen_at,'
                               'updated_at=excluded.updated_at',
                               (row['id'], row['card_id'], target, row['number'], row['rarity'], row.get('name_cn', ''),
                                row['name_jp'], raw, stamp, stamp, stamp))
                    for product_id, _ in products:
                        db.execute('INSERT INTO jhs_version_products VALUES (?,?,?,?,?) '
                                   'ON CONFLICT(jhs_version_id,product_id) DO UPDATE SET '
                                   'last_seen_at=excluded.last_seen_at,source_json=excluded.source_json',
                                   (row['id'], product_id, stamp, stamp, raw))
                # 只替换完整快照；上游消失的旧版本和商品关联仍保留供追溯。
                versions = [CardVersion.from_dict(r) for r in rows]
                db.execute('INSERT INTO jhs_card_version_sync VALUES (?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET '
                           'jhs_card_ids_json=excluded.jhs_card_ids_json,versions_json=excluded.versions_json,checked_at=excluded.checked_at',
                           (card_id, encode(ids), encode([asdict(v) for v in versions]), self.clock()))
            return versions
        finally:
            db.close()

    async def refresh(self, card_id, name_jp, ids, client, discover):
        try:
            targets = ids
            if not targets:
                found = await discover()
                targets = sorted({v.card_id for v in found if v.card_id})
                # 未建立平台映射时，仅接受一个明确的卡片身份；歧义保持在线查询结果。
                if len(targets) != 1:
                    return found
            payloads = [await client.complete_versions(jhs_id) for jhs_id in targets]
            result = await asyncio.to_thread(self.save, card_id, name_jp, ids, targets, payloads)
            self.retry_after.pop(card_id, None)
            return result
        except (JhsUnavailable, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as error:
            self.retry_after[card_id] = self.clock() + self.retry_delay
            raise JhsUnavailable('完整版本列表暂时不可用') from error

    async def get(self, card_id, name_jp, client, discover):
        try:
            state = await asyncio.to_thread(self.load, card_id, name_jp)
        except (sqlite3.Error, ValueError, TypeError, KeyError, OSError):
            # 尚未迁移或本地不可读时保留原在线查询能力，不冒用零散版本。
            return await discover()
        if state is None:
            return await discover()
        ids, versions, checked_at = state
        if versions is not None and 0 <= self.clock() - checked_at < self.ttl:
            return versions
        if self.clock() < self.retry_after.get(card_id, 0):
            if versions is not None:
                return versions
            raise JhsUnavailable('完整版本列表等待重试')
        task = self.tasks.get(card_id)
        if task is None:
            task = asyncio.create_task(self.refresh(card_id, name_jp, ids, client, discover))
            self.tasks[card_id] = task
            def finished(done):
                if self.tasks.get(card_id) is done:
                    self.tasks.pop(card_id, None)
                if not done.cancelled() and done.exception():
                    logging.warning('卡片版本刷新失败 card_id=%s error=%s', card_id, type(done.exception()).__name__)
            task.add_done_callback(finished)
        if versions is not None:
            return versions
        return await asyncio.shield(task)


@lru_cache(maxsize=4)
def shared_version_catalog(path):
    return VersionCatalog(path)
