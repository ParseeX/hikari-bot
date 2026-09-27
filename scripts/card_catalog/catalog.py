"""独立卡片主库工具：不加载 Bot、不修改原库、不自动启动定时任务。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse
import zipfile


BASE_URL = 'https://ygocdb.com/api/v0/'
TEMPORARY_MIN = 100000000
TYPE_TOKEN = 0x4000
TYPE_MONSTER = 0x1
TYPE_XYZ = 0x800000
TYPE_PENDULUM = 0x1000000
TYPE_LINK = 0x4000000
NAME_FIELDS = {
    'cn_name': 'zh', 'sc_name': 'zh-Hans', 'md_name': 'zh-Hans',
    'nwbbs_n': 'zh', 'cnocg_n': 'zh', 'jp_name': 'ja',
    'en_name': 'en', 'wiki_en': 'en', 'md_en_n': 'en',
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def normalize(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def identifier(value: int) -> tuple[str, str]:
    if value <= 0:
        raise ValueError('卡密必须为正整数')
    return ('temporary', str(value)) if value >= TEMPORARY_MIN else ('passcode', f'{value:08d}')


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    # 防止把原来的 MC、Cardrush 或其他数据库当作新主库打开。
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables and 'catalog_meta' not in tables:
        db.close()
        raise ValueError('目标文件不是卡片主库，拒绝修改')
    if tables:
        row = db.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
        if row is None or row[0] not in {'1', '2', '3', '4'}:
            db.close()
            raise ValueError('不支持的卡片主库版本')
        if row[0] in {'1', '2', '3'}:
            backup(db, path)
        if row[0] == '1':
            try:
                db.executescript(Path(__file__).with_name('migrate_v2.sql').read_text(encoding='utf-8'))
                for pack in db.execute('SELECT p.id,b.* FROM products p JOIN packs b ON b.prefix=p.legacy_prefix').fetchall():
                    link_official(db, pack['id'], pack['name'], pack['source_url'], pack['release_date'])
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('迁移后的外键校验失败')
                db.commit()
            except Exception:
                db.rollback()
                db.close()
                raise
        if row[0] in {'1', '2'}:
            try:
                db.executescript(Path(__file__).with_name('migrate_v3.sql').read_text(encoding='utf-8'))
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('迁移后的外键校验失败')
                db.commit()
            except Exception:
                db.rollback()
                db.close()
                raise
        if row[0] in {'1', '2', '3'}:
            # 重建 cards 的可空 cid 约束；子表仍引用原表名和原内部 ID。
            db.execute('PRAGMA foreign_keys=OFF')
            try:
                db.executescript(Path(__file__).with_name('migrate_v4.sql').read_text(encoding='utf-8'))
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('迁移后的外键校验失败')
                db.commit()
            except Exception:
                db.rollback()
                db.close()
                raise
            db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    db.execute("INSERT OR IGNORE INTO catalog_meta VALUES ('schema_version','4')")
    db.commit()
    return db


def link_official(db, product_id, name, source_url, release_date, konami_pid=None):
    pid = konami_pid or parse_qs(urlparse(source_url or '').query).get('pid', [None])[0]
    if pid:
        db.execute('INSERT INTO product_official_links VALUES (?,?,?,?,?) '
                   'ON CONFLICT(product_id,konami_pid) DO UPDATE SET '
                   'name=COALESCE(excluded.name,name),source_url=COALESCE(excluded.source_url,source_url),'
                   'release_date=COALESCE(excluded.release_date,release_date)',
                   (product_id, str(pid), name, source_url, release_date))


def upsert_product(db, prefix, pack, stamp):
    if pack:
        if type(pack.get('id')) is not int or pack['id'] <= 0 or not str(pack.get('name') or '').strip():
            raise ValueError('集换社商品身份不完整')
        db.execute('INSERT INTO products(prefix,jhs_pack_id,jhs_name,jhs_name_origin,release_date,updated_at) '
                   'VALUES (?,?,?,?,?,?) ON CONFLICT(jhs_pack_id) DO UPDATE SET '
                   'prefix=COALESCE(excluded.prefix,prefix),jhs_name=excluded.jhs_name,'
                   'jhs_name_origin=excluded.jhs_name_origin,release_date=excluded.release_date,updated_at=excluded.updated_at',
                   (prefix, pack['id'], pack['name'], pack.get('name_origin'), pack.get('released_at'), stamp))
        return db.execute('SELECT id FROM products WHERE jhs_pack_id=?', (pack['id'],)).fetchone()[0]
    db.execute('INSERT INTO products(legacy_prefix,prefix,updated_at) VALUES (?,?,?) '
               'ON CONFLICT(legacy_prefix) DO UPDATE SET updated_at=excluded.updated_at', (prefix, prefix, stamp))
    return db.execute('SELECT id FROM products WHERE legacy_prefix=?', (prefix,)).fetchone()[0]


def backup(db: sqlite3.Connection, path: Path) -> None:
    """SQLite backup API 保留 WAL 中已提交的数据；只为已有资料的库备份。"""
    if not db.execute('SELECT EXISTS(SELECT 1 FROM cards)').fetchone()[0]:
        return
    directory = path.parent / 'backups'
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with sqlite3.connect(directory / f'{path.stem}-{stamp}.sqlite3') as destination:
        db.backup(destination)


def issue(db, source, key, reason, payload):
    db.execute('INSERT INTO import_issues VALUES (?,?,?,?,?) '
               'ON CONFLICT(source,source_key) DO UPDATE SET reason=excluded.reason, '
               'payload_json=excluded.payload_json,updated_at=excluded.updated_at',
               (source, str(key), reason, encode(payload), now()))


def clear_issue(db, source, key):
    db.execute('DELETE FROM import_issues WHERE source=? AND source_key=?', (source, str(key)))


def bind_identifier(db, card_id, value, source):
    kind, text = identifier(value)
    old = db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?', (kind, text)).fetchone()
    if old and old[0] != card_id:
        raise ValueError(f'卡密 {text} 已关联其他卡片')
    db.execute('INSERT OR IGNORE INTO card_identifiers VALUES (?,?,?,?)', (kind, text, card_id, source))


def decode_stats(data):
    t = int(data['type'])
    packed = int(data.get('level') or 0)
    monster = bool(t & TYPE_MONSTER)
    link = bool(t & TYPE_LINK)
    atk = data.get('atk')
    defense = data.get('def')
    def display(value):
        return None if value is None else '?' if value < 0 else str(value)
    return {
        'type_code': t, 'race_code': data.get('race'), 'attribute_code': data.get('attribute'),
        'atk_raw': atk, 'def_raw': defense,
        'atk_text': display(atk) if monster else None,
        'def_text': display(defense) if monster and not link else None,
        'level': (packed & 0xff) if monster and not t & (TYPE_XYZ | TYPE_LINK) else None,
        'rank': (packed & 0xff) if t & TYPE_XYZ else None,
        'link_rating': (packed & 0xff) if link else None,
        'link_markers': defense if link else None,
        'pendulum_left': (packed >> 24) & 0xff if t & TYPE_PENDULUM else None,
        'pendulum_right': (packed >> 16) & 0xff if t & TYPE_PENDULUM else None,
        'level_raw': packed, 'setcode': str(data.get('setcode', 0)), 'scope_code': data.get('ot'),
    }


def import_cards(db, rows: dict, changes: dict) -> dict:
    """事务由调用者管理；cid 保持身份稳定，来源不完整的记录进入待核对。"""
    if not db.in_transaction:
        db.execute('BEGIN')
    counts = Counter()
    for source_key, record in rows.items():
        cid = int(record.get('cid') or 0)
        data = record.get('data') or {}
        t = data.get('type')
        if t is not None and int(t) & TYPE_TOKEN:
            issue(db, 'ygocdb', source_key, 'excluded_token', record)
            counts['excluded_token'] += 1
            continue
        old = db.execute('SELECT * FROM cards WHERE konami_cid=?', (cid,)).fetchone() if cid > 0 else None
        if old and not t and not record.get('id') and db.execute(
                "SELECT 1 FROM jhs_cards j JOIN jhs_versions v USING(jhs_card_id) "
                "JOIN jhs_version_products vp USING(jhs_version_id) WHERE j.card_id=? AND v.object_type='card' LIMIT 1",
                (old['id'],)).fetchone():
            # 百鸽没有模拟器参数不等于没有实体卡；保留已核实的集换社资料。
            clear_issue(db, 'ygocdb', source_key)
            counts['retained_jhs_metadata'] += 1
            continue
        # 不把缺少资料的官方奖品卡/特殊卡猜成普通卡或衍生物。
        if cid <= 0 or not t or not (int(data.get('ot') or 0) & 11):
            issue(db, 'ygocdb', source_key, 'missing_or_out_of_scope_data', record)
            counts['pending'] += 1
            continue
        if old is None and record.get('jp_name'):
            matches = db.execute("SELECT DISTINCT c.* FROM cards c JOIN card_names n ON n.card_id=c.id "
                "WHERE c.konami_cid IS NULL AND n.source='jhs:jp_name' AND n.normalized=?",
                (normalize(record['jp_name']),)).fetchall()
            if len(matches) == 1:
                old = matches[0]
            elif len(matches) > 1:
                issue(db, 'ygocdb', source_key, 'ambiguous_jhs_identity', record)
                counts['pending'] += 1
                continue
        source_hash = digest(record)
        if old and old['source_hash'] == source_hash:
            clear_issue(db, 'ygocdb', source_key)
            counts['unchanged'] += 1
            continue
        raw_id = int(record.get('id') or 0)
        # 部分确有实体的特殊卡无卡密；空值和 00000000 分开。
        passcode = old['passcode'] if old else None
        temporary = old['temporary_id'] if old else None
        if raw_id:
            kind, value = identifier(raw_id)
            if kind == 'temporary':
                temporary = value
            else:
                passcode = value
        stamp = now()
        fields = dict(konami_cid=cid, passcode=passcode, temporary_id=temporary,
                      japanese_reading=record.get('jp_ruby') or None, **decode_stats(data),
                      source_hash=source_hash, source_json=encode(record), updated_at=stamp)
        # 唯一冲突只隔离本条，不让错误数据污染已有映射。
        db.execute('SAVEPOINT card_import')
        try:
            if old:
                card_id = old['id']
                db.execute('UPDATE cards SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?',
                           (*fields.values(), card_id))
            else:
                fields['created_at'] = stamp
                cursor = db.execute('INSERT INTO cards (' + ','.join(fields) + ') VALUES (' +
                                    ','.join('?' for _ in fields) + ')', tuple(fields.values()))
                card_id = cursor.lastrowid
            if raw_id:
                bind_identifier(db, card_id, raw_id, 'ygocdb')
            db.execute("DELETE FROM card_names WHERE card_id=? AND source LIKE 'ygocdb:%'", (card_id,))
            for field, language in NAME_FIELDS.items():
                if name := record.get(field):
                    db.execute('INSERT INTO card_names VALUES (?,?,?,?,?)',
                               (card_id, language, f'ygocdb:{field}', name, normalize(name)))
            text = record.get('text') or {}
            db.execute('INSERT INTO card_texts VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT(card_id,language,source) DO UPDATE SET type_text=excluded.type_text, '
                       'effect=excluded.effect,pendulum_effect=excluded.pendulum_effect,updated_at=excluded.updated_at',
                       (card_id, 'zh', 'ygocdb', text.get('types', ''), text.get('desc', ''), text.get('pdesc', ''), stamp))
            db.execute('RELEASE card_import')
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.execute('ROLLBACK TO card_import')
            db.execute('RELEASE card_import')
            issue(db, 'ygocdb', source_key, str(exc), record)
            counts['pending'] += 1
            continue
        clear_issue(db, 'ygocdb', source_key)
        counts['updated' if old else 'inserted'] += 1
    counts.update(import_id_changes(db, changes))
    return dict(counts)


def import_id_changes(db, changes):
    counts = Counter()
    for before, after in changes.items():
        before, after = int(before), int(after)
        if before < TEMPORARY_MIN or not 0 < after < TEMPORARY_MIN:
            issue(db, 'id_changelog', before, 'unsupported_id_change', {'from': before, 'to': after})
            continue
        old = db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?', identifier(before)).fetchone()
        new = db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?', identifier(after)).fetchone()
        if not old and not new:
            continue
        if old and new and old[0] != new[0]:
            issue(db, 'id_changelog', before, 'conflicting_cards', {'from': before, 'to': after})
            counts['id_conflicts'] += 1
            continue
        card_id = (new or old)[0]
        current = db.execute('SELECT passcode FROM cards WHERE id=?', (card_id,)).fetchone()[0]
        if current and current != identifier(after)[1]:
            issue(db, 'id_changelog', before, 'conflicting_passcode', {'from': before, 'to': after})
            counts['id_conflicts'] += 1
            continue
        bind_identifier(db, card_id, before, 'ygocdb:id_changelog')
        bind_identifier(db, card_id, after, 'ygocdb:id_changelog')
        db.execute('UPDATE cards SET passcode=?,temporary_id=COALESCE(temporary_id,?) WHERE id=?',
                   (identifier(after)[1], str(before), card_id))
        clear_issue(db, 'id_changelog', before)
        counts['id_mappings'] += 1
    return counts


def import_artwork_ids(db, path):
    """迁入 MC 中明确指向同名原卡的异画编号，不合并规则同名卡。"""
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = source.execute(
            'SELECT d.id,d.alias,t.name,b.name,d.type FROM datas d '
            'JOIN texts t ON t.id=d.id JOIN texts b ON b.id=d.alias WHERE d.alias>0'
        ).fetchall()
    finally:
        source.close()
    counts = Counter()
    for image_id, alias, name, base_name, type_code in rows:
        if type_code & TYPE_TOKEN or normalize(name) != normalize(base_name):
            counts['skipped'] += 1
            continue
        known = db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?',
                           identifier(image_id)).fetchone()
        target = db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?',
                            identifier(alias)).fetchone()
        if known or not target:
            counts['skipped'] += 1
            continue
        old = db.execute('SELECT card_id FROM card_artwork_ids WHERE image_id=?', (image_id,)).fetchone()
        if old and old[0] != target[0]:
            raise ValueError(f'异画编号 {image_id} 已关联其他卡片')
        db.execute('INSERT OR IGNORE INTO card_artwork_ids VALUES (?,?,?)',
                   (image_id, target[0], 'mc:alias'))
        counts['unchanged' if old else 'inserted'] += 1
    return dict(counts)


def import_releases(db, rows):
    count = 0
    for row in rows:
        card = db.execute('SELECT id FROM cards WHERE konami_cid=?', (row.get('cid'),)).fetchone()
        if not card:
            continue
        for language, info in (row.get('release') or {}).items():
            if info is None:
                continue
            language = {'jp': 'ja', 'sc': 'zh-Hans'}.get(language, language)
            db.execute('INSERT INTO card_releases VALUES (?,?,?,?,?) '
                       'ON CONFLICT(card_id,language,source) DO UPDATE SET release_date=excluded.release_date,pack_name=excluded.pack_name',
                       (card[0], language, info.get('date'), info.get('pack'), 'ygocdb:releaseDates'))
            count += 1
    return count


def fetch(url, *, data=None, headers=None, timeout=45):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(64 * 1024 * 1024)


def save_snapshot(directory, filename, payload):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError('已有快照与待保存内容冲突')
        return
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_bytes(payload)
    temporary.replace(target)


def sync_ygocdb(db, snapshots):
    expected = json.loads(fetch(BASE_URL + 'cards.zip.md5'))
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{32}', expected):
        raise ValueError('来源校验值格式错误')
    old = db.execute("SELECT content_hash FROM sync_state WHERE source='ygocdb'").fetchone()
    changes_raw = fetch(BASE_URL + 'idChangelog.jsonp')
    changes = json.loads(changes_raw)
    releases_raw = fetch(BASE_URL + 'releaseDates.json')
    releases = json.loads(releases_raw)
    if not isinstance(changes, dict) or not isinstance(releases, list):
        raise ValueError('来源辅助数据格式错误')
    rows = None
    if not old or old[0] != expected:
        packed = fetch(BASE_URL + 'cards.zip')
        with zipfile.ZipFile(io.BytesIO(packed)) as archive:
            if archive.getinfo('cards.json').file_size > 64 * 1024 * 1024:
                raise ValueError('来源文件超出预期大小')
            raw = archive.read('cards.json')
        if hashlib.md5(raw).hexdigest() != expected:
            raise ValueError('来源正在变化或下载不完整，保留原库，请稍后重试')
        rows = json.loads(raw)
        if not isinstance(rows, dict) or len(rows) < 10000:
            raise ValueError('来源记录过少或格式错误，保留原库')
        save_snapshot(snapshots, f'ygocdb-{expected}.json', raw)
    save_snapshot(snapshots, f'id-changes-{hashlib.sha256(changes_raw).hexdigest()[:16]}.json', changes_raw)
    save_snapshot(snapshots, f'releases-{hashlib.sha256(releases_raw).hexdigest()[:16]}.json', releases_raw)
    with db:
        result = import_cards(db, rows, changes) if rows is not None else dict(import_id_changes(db, changes))
        result['downloaded_cards'] = rows is not None
        result['release_rows'] = import_releases(db, releases)
        stamp = now()
        db.execute('INSERT INTO sync_state VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET '
                   'content_hash=excluded.content_hash,checked_at=excluded.checked_at, '
                   'imported_at=CASE WHEN sync_state.content_hash!=excluded.content_hash THEN excluded.imported_at ELSE sync_state.imported_at END',
                   ('ygocdb', expected, stamp, stamp))
        db.execute('INSERT INTO sync_runs(source,completed_at,summary_json) VALUES (?,?,?)', ('ygocdb', stamp, encode(result)))
    return result


def candidate_cards(db, rows):
    # 日文原名对应卡片身份；中文译名可能在两张不同卡片间重名。
    japanese = {normalize((r.get('identity') or {}).get('name_jp') or r.get('name_jp', ''))
                for r in rows if (r.get('identity') or {}).get('name_jp') or r.get('name_jp')}
    if japanese:
        found = {r[0] for name in japanese for r in db.execute(
            "SELECT DISTINCT card_id FROM card_names WHERE language='ja' AND normalized=?", (name,))}
        # 混沌士兵等确有同名通常版和仪式版；仅使用已核实的类型值细分。
        types = {(r.get('identity') or {}).get('type') for r in rows} - {None, ''}
        required = {'通常怪兽': 0x11, '仪式怪兽': 0x81}.get(next(iter(types))) if len(types) == 1 else None
        if required:
            found = {card_id for card_id in found if db.execute(
                'SELECT 1 FROM cards WHERE id=? AND (type_code & ?)=?',
                (card_id, required, required)).fetchone()}
        return found
    names = {normalize(n) for r in rows for n in
             [r.get('name_cn', ''), r.get('name_jp', ''), *(r.get('aliases') or [])] if n}
    found = set()
    for name in names:
        found.update(r[0] for r in db.execute('SELECT DISTINCT card_id FROM card_names WHERE normalized=?', (name,)))
    return found


def cached_identity(db, jhs_id):
    row = db.execute("SELECT source_json FROM jhs_versions WHERE jhs_card_id=? "
                     "AND json_type(source_json,'$.identity')='object' LIMIT 1", (jhs_id,)).fetchone()
    return json.loads(row[0])['identity'] if row else None


def with_identity(db, row):
    row = dict(row)
    detail = row.get('identity') or cached_identity(db, int(row['card_id']))
    if detail:
        if (not isinstance(detail, dict) or type(detail.get('id')) is not int
                or detail['id'] != int(row['card_id'])
                or not isinstance(detail.get('name_jp', ''), str)
                or not isinstance(detail.get('type', ''), str)):
            raise ValueError('集换社卡片身份不一致')
        row['identity'] = detail
    return row


def resolve_jhs_card(db, jhs_id, entries):
    old = db.execute('SELECT card_id FROM jhs_cards WHERE jhs_card_id=?', (jhs_id,)).fetchone()
    if any((r.get('identity') or {}).get('type', '').casefold() == 'token' for r in entries):
        if old and old[0] is not None:
            raise ValueError(f'集换社卡片 {jhs_id} 的衍生物类型与已有映射冲突')
        return None, 'excluded_token'
    candidates = candidate_cards(db, entries)
    if old and old[0] is not None:
        if (candidates and old[0] not in candidates) or (not candidates and any(r.get('identity') for r in entries)):
            raise ValueError(f'集换社卡片 {jhs_id} 与已有映射冲突')
        return old[0], 'existing_mapping'
    if len(candidates) == 1:
        return next(iter(candidates)), 'unique_japanese_name' if any(
            (r.get('identity') or {}).get('name_jp') or r.get('name_jp') for r in entries) else 'unique_exact_name'
    # 平台详情和商品归属都已确认的实体卡，可以不依赖官方 cid 建立主库身份。
    # 存在名称歧义时仍待核对，不凭新建记录绕过冲突。
    if not candidates:
        detail = next((r['identity'] for r in entries if r.get('identity')), None)
        has_product = any((r.get('pack') or {}).get('id') for r in entries) or db.execute(
            'SELECT 1 FROM jhs_versions v JOIN jhs_version_products vp USING(jhs_version_id) '
            'JOIN products p ON p.id=vp.product_id WHERE v.jhs_card_id=? AND p.jhs_pack_id IS NOT NULL LIMIT 1',
            (jhs_id,)).fetchone()
        if detail and detail.get('type') and has_product:
            return create_jhs_card(db, jhs_id, entries, detail), 'jhs_identity'
    return None, 'pending'


def create_jhs_card(db, jhs_id, entries, detail):
    """只收录详情确认的卡片；无卡密和官方编号时留空，不生成替代号码。"""
    if detail['id'] != jhs_id or detail.get('type', '').casefold() == 'token':
        raise ValueError('集换社卡片身份或类型不符')
    name_cn = detail.get('name_cn') or entries[0].get('name_cn')
    if not name_cn:
        raise ValueError('集换社卡片缺少名称')
    stamp = now()
    # 来源已有官方 cid 时必须保留；没有卡密的 id=0 不能转换成 00000000。
    base_matches = []
    for source_key, raw in db.execute("SELECT source_key,payload_json FROM import_issues WHERE source='ygocdb'"):
        base = json.loads(raw)
        if (detail.get('name_jp') and normalize(base.get('jp_name') or '') == normalize(detail['name_jp'])
                and int(base.get('cid') or 0) > 0):
            base_matches.append((source_key, base))
    if len({b['cid'] for _, b in base_matches}) > 1:
        raise ValueError('集换社卡片对应多个官方编号')
    base = base_matches[0][1] if base_matches else {}
    if (base.get('data') or {}).get('type', 0) & TYPE_TOKEN:
        raise ValueError('集换社卡片与基础来源的衍生物类型冲突')
    payload = {'source': 'jhs', 'jhs_card_id': jhs_id, 'identity': detail, 'ygocdb': base}
    card_id = db.execute('INSERT INTO cards(konami_cid,japanese_reading,source_hash,source_json,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                         (base.get('cid'), base.get('jp_ruby'), digest(payload), encode(payload), stamp, stamp)).lastrowid
    names = [('zh', 'jhs:cn_name', name_cn), ('ja', 'jhs:jp_name', detail.get('name_jp'))]
    names.extend(('zh', 'jhs:alias', n) for r in entries for n in [r.get('name_cn'), *(r.get('aliases') or [])]
                 if n and normalize(n) != normalize(detail.get('name_jp', '')))
    for language, source, name in names:
        if name:
            db.execute('INSERT OR IGNORE INTO card_names VALUES (?,?,?,?,?)',
                       (card_id, language, source, name, normalize(name)))
    for field, language in NAME_FIELDS.items():
        if base.get(field):
            db.execute('INSERT OR IGNORE INTO card_names VALUES (?,?,?,?,?)',
                       (card_id, language, 'ygocdb:' + field, base[field], normalize(base[field])))
    for source_key, _ in base_matches:
        clear_issue(db, 'ygocdb', source_key)
    db.execute('INSERT INTO card_texts VALUES (?,?,?,?,?,?,?)',
               (card_id, 'zh', 'jhs', detail['type'], detail.get('desc') or '', detail.get('pendulum_desc') or '', stamp))
    return card_id


def enrich_identities(db, rows, env, snapshots):
    """仅对歧义或缺失身份读取详情；已验证的详情随版本原文保存并复用。"""
    grouped = defaultdict(list)
    for row in rows:
        if row.get('object_type', 'card') == 'card':
            grouped[int(row['card_id'])].append(row)
    details = {}
    for jhs_id, entries in grouped.items():
        detail = cached_identity(db, jhs_id)
        candidates = candidate_cards(db, entries)
        if (detail is None and len(candidates) != 1) or (detail is not None and not candidates
                and detail.get('type', '').casefold() != 'token' and 'desc' not in detail):
            raw = fetch(f"http://127.0.0.1:{int(env.get('JHS_HTTP_PORT', '8791'))}/v1/card-detail",
                        data=encode({'card_id': jhs_id}).encode(), timeout=180,
                        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['JHS_ACCESS_TOKEN']})
            body = json.loads(raw)
            detail = body.get('card') if isinstance(body, dict) else None
            if not isinstance(detail, dict) or detail.get('id') != jhs_id:
                raise ValueError('集换社卡片详情身份不一致')
            save_snapshot(snapshots, f'jhs-card-{jhs_id}-{hashlib.sha256(raw).hexdigest()[:16]}.json', raw)
        if detail:
            details[jhs_id] = detail
    return [with_identity(db, {**r, **({'identity': details[r['card_id']]} if r.get('card_id') in details else {})})
            if r.get('object_type', 'card') == 'card' else r for r in rows]


def reconcile_jhs(db, env, snapshots):
    """逐张提交，可重复执行；来源缺少的基础卡保留待核对，不猜测身份。"""
    ids = [r[0] for r in db.execute("SELECT j.jhs_card_id FROM jhs_cards j WHERE j.card_id IS NULL "
           "AND EXISTS (SELECT 1 FROM jhs_versions v WHERE v.jhs_card_id=j.jhs_card_id AND v.object_type='card')")]
    counts = Counter()
    for jhs_id in ids:
        versions = db.execute("SELECT jhs_version_id,source_json FROM jhs_versions WHERE jhs_card_id=? AND object_type='card'", (jhs_id,)).fetchall()
        entries = enrich_identities(db, [json.loads(v['source_json']) for v in versions], env, snapshots)
        stamp = now()
        with db:
            card_id, method = resolve_jhs_card(db, jhs_id, entries)
            db.execute('UPDATE jhs_cards SET card_id=?,match_method=?,updated_at=? WHERE jhs_card_id=?',
                       (card_id, method, stamp, jhs_id))
            for version, row in zip(versions, entries):
                db.execute('UPDATE jhs_versions SET source_json=?,object_type=?,updated_at=? WHERE jhs_version_id=?',
                           (encode(row), 'token' if method == 'excluded_token' else 'card', stamp, version['jhs_version_id']))
            if method != 'pending':
                clear_issue(db, 'jhs_mapping', jhs_id)
            else:
                issue(db, 'jhs_mapping', jhs_id, 'missing_or_ambiguous_base_card', entries)
        counts[method] += 1
        print(f'身份核对 {jhs_id}: {method}', flush=True)
    # 旧周边的失败项属于历史采集记录，不应继续报告为待匹配卡片。
    with db:
        db.execute("DELETE FROM import_issues WHERE source='jhs_mapping' AND NOT EXISTS "
                   "(SELECT 1 FROM jhs_versions v WHERE v.jhs_card_id=CAST(import_issues.source_key AS INTEGER) AND v.object_type='card')")
    return dict(counts)


def import_pack(db, prefix, rows, *, expected_cards=None, expected_versions=None,
                name=None, release_date=None, source_url=None, product=None, konami_pid=None):
    prefix = prefix.upper() if prefix else None
    if prefix is not None and not re.fullmatch('[A-Z0-9]{1,16}', prefix):
        raise ValueError('卡盒前缀格式错误')
    if prefix is None and not product:
        raise ValueError('无编号商品必须指定集换社商品 ID')
    selected, excluded = {}, []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('版本条目格式错误')
        # 搜索可能模糊命中其他物品；严格限定编号，原文仍完整存储。
        if product:
            if (row.get('pack') or {}).get('id') != product.get('id'):
                raise ValueError('版本与集换社商品 ID 不一致')
        elif not str(row.get('number', '')).upper().startswith(prefix + '-'):
            continue
        if row.get('object_type') == 'unknown':
            raise ValueError('源数据缺少商品类型，不能确认是否为卡片')
        if row.get('object_type', 'card') != 'card':
            excluded.append(row)
            continue
        if not str(row.get('rarity') or '').strip() or int(row.get('id') or 0) <= 0 or int(row.get('card_id') or 0) <= 0:
            raise ValueError('卡盒中存在缺少 ID 或罕贵的版本')
        row = with_identity(db, row)
        version_id = int(row['id'])
        if version_id in selected and selected[version_id] != row:
            raise ValueError('同一版本 ID 返回冲突内容')
        selected[version_id] = row
    if not selected:
        raise ValueError('未获得该卡盒版本，保留已有记录')
    grouped = defaultdict(list)
    for row in selected.values():
        grouped[int(row['card_id'])].append(row)
    if expected_cards is not None and len(grouped) != expected_cards:
        raise ValueError(f'卡片数不符：预期 {expected_cards}，实际 {len(grouped)}')
    if expected_versions is not None and len(selected) != expected_versions:
        raise ValueError(f'版本数不符：预期 {expected_versions}，实际 {len(selected)}')
    stamp = now()
    counts = Counter()
    with db:
        if prefix:
            db.execute('INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET '
                   'name=COALESCE(excluded.name,packs.name),release_date=COALESCE(excluded.release_date,packs.release_date), '
                   'source_url=COALESCE(excluded.source_url,packs.source_url), '
                   'expected_cards=COALESCE(excluded.expected_cards,packs.expected_cards), '
                   'expected_versions=COALESCE(excluded.expected_versions,packs.expected_versions), '
                   'observed_cards=excluded.observed_cards,observed_versions=excluded.observed_versions,last_synced_at=excluded.last_synced_at',
                   (prefix, name, release_date, source_url, expected_cards, expected_versions, len(grouped), len(selected), stamp))
        for row in excluded:
            db.execute('UPDATE jhs_versions SET object_type=? WHERE jhs_version_id=?',
                       (row['object_type'], row['id']))
        product_id = upsert_product(db, prefix, product, stamp)
        link_official(db, product_id, name, source_url, release_date, konami_pid)
        for jhs_id, entries in grouped.items():
            card_id, method = resolve_jhs_card(db, jhs_id, entries)
            db.execute('INSERT INTO jhs_cards VALUES (?,?,?,?) ON CONFLICT(jhs_card_id) DO UPDATE SET '
                       'card_id=excluded.card_id,match_method=excluded.match_method,updated_at=excluded.updated_at',
                       (jhs_id, card_id, method, stamp))
            if method == 'excluded_token':
                clear_issue(db, 'jhs_mapping', jhs_id)
                counts['excluded_tokens'] += 1
            elif card_id is None:
                issue(db, 'jhs_mapping', jhs_id, 'ambiguous_or_missing_name', entries)
                counts['unmatched_cards'] += 1
            else:
                clear_issue(db, 'jhs_mapping', jhs_id)
                counts['matched_cards'] += 1
        for version_id, row in selected.items():
            old = db.execute('SELECT v.*,p.jhs_pack_id FROM jhs_versions v JOIN products p ON p.id=v.product_id '
                             'WHERE jhs_version_id=?', (version_id,)).fetchone()
            if old:
                if old['jhs_card_id'] != int(row['card_id']) or (old['pack_prefix'] and prefix and old['pack_prefix'] != prefix):
                    raise ValueError(f'集换社版本 {version_id} 的归属发生冲突')
            # 已核实的平台商品归属不会被后续按前缀的旧采集降级覆盖。
            target_product = old['product_id'] if old and old['jhs_pack_id'] else product_id
            payload = encode(row)
            object_type = 'token' if (row.get('identity') or {}).get('type', '').casefold() == 'token' else 'card'
            db.execute('INSERT INTO jhs_versions(jhs_version_id,jhs_card_id,pack_prefix,product_id,number_raw,rarity_raw,name_cn,name_jp,source_json,first_seen_at,last_seen_at,updated_at,object_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(jhs_version_id) DO UPDATE SET '
                       "object_type=excluded.object_type,product_id=excluded.product_id,pack_prefix=COALESCE(excluded.pack_prefix,jhs_versions.pack_prefix),"
                       'number_raw=excluded.number_raw,rarity_raw=excluded.rarity_raw,name_cn=excluded.name_cn, '
                       'name_jp=excluded.name_jp,source_json=excluded.source_json,last_seen_at=excluded.last_seen_at, '
                       'updated_at=CASE WHEN jhs_versions.source_json!=excluded.source_json THEN excluded.updated_at ELSE jhs_versions.updated_at END',
                       (version_id, int(row['card_id']), prefix, target_product, row.get('number', ''), row['rarity'], row.get('name_cn', ''),
                        row.get('name_jp', ''), payload, stamp, stamp, stamp, object_type))
            if product:
                db.execute('INSERT INTO jhs_version_products VALUES (?,?,?,?,?) '
                           'ON CONFLICT(jhs_version_id,product_id) DO UPDATE SET '
                           'last_seen_at=excluded.last_seen_at,source_json=excluded.source_json',
                           (version_id, product_id, stamp, stamp, payload))
            if not old or old['number_raw'] != row.get('number', '') or old['rarity_raw'] != row['rarity']:
                # 卡盒同步发现新增/变更版本后，旧的整卡快照需要重新确认。
                db.execute('DELETE FROM jhs_card_version_sync WHERE card_id IN '
                           '(SELECT card_id FROM jhs_cards WHERE jhs_card_id=?)', (row['card_id'],))
            counts['inserted' if not old else 'unchanged' if old['source_json'] == payload else 'updated'] += 1
        # 未返回的旧版本不删除；last_seen_at 可识别需要复核的历史记录。
        result = dict(counts, prefix=prefix, product_id=product_id, cards=len(grouped), versions=len(selected))
        db.execute('INSERT INTO sync_runs(source,completed_at,summary_json) VALUES (?,?,?)',
                   ('jhs:' + (str(product['id']) if product else prefix), stamp, encode(result)))
    return result


def read_bridge_env(path):
    # 仅载入本工具需要的字段；不执行 shell，不输出凭据。
    allowed = {'JHS_ACCESS_TOKEN', 'JHS_HTTP_PORT'}
    values = {k: os.environ[k] for k in allowed if k in os.environ}
    if path:
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in allowed:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    if not values.get('JHS_ACCESS_TOKEN'):
        raise ValueError('未配置 JHS_ACCESS_TOKEN')
    return values


def sync_pack(db, args, snapshots):
    if getattr(args, 'backfill', False):
        return backfill_product_names(db, args, snapshots)
    product_id = getattr(args, 'jhs_pack_id', None)
    if product_id is not None and (type(product_id) is not int or not 0 < product_id < 2**53):
        raise ValueError('集换社商品 ID 格式错误')
    if (not product_id and not args.prefix) or (args.prefix and not re.fullmatch('[A-Z0-9]{1,16}', args.prefix)):
        raise ValueError('卡盒前缀格式错误')
    env = read_bridge_env(args.bridge_env)
    port = int(env.get('JHS_HTTP_PORT', '8791'))
    route = 'product-versions' if product_id else 'versions'
    payload = {'pack_id': product_id} if product_id else {'name_jp': args.prefix}
    raw = fetch(f'http://127.0.0.1:{port}/v1/{route}',
                data=encode(payload).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['JHS_ACCESS_TOKEN']},
                timeout=300)
    body = json.loads(raw)
    if not isinstance(body, dict) or body.get('error'):
        raise ValueError('桥接返回数据格式错误')
    rows = body.get('versions')
    if not isinstance(rows, list):
        raise ValueError('桥接没有返回完整版本列表')
    product = body.get('product') if product_id else None
    if product_id and (not isinstance(product, dict) or product.get('id') != product_id):
        raise ValueError('桥接返回商品 ID 不符')
    save_snapshot(snapshots, f'jhs-{product_id or args.prefix}-{hashlib.sha256(raw).hexdigest()[:16]}.json', raw)
    rows = enrich_identities(db, rows, env, snapshots)
    return import_pack(db, args.prefix, rows, expected_cards=args.expected_cards,
                       expected_versions=args.expected_versions, name=args.name,
                       release_date=args.release_date, source_url=args.source_url,
                       product=product, konami_pid=getattr(args, 'konami_pid', None))


def backfill_product_names(db, args, snapshots):
    """以未绑定版本发现商品，再按完整系列补齐；每个商品提交后都可续跑。"""
    from types import SimpleNamespace
    if not args.prefix or not re.fullmatch('[A-Z0-9]{1,16}', args.prefix):
        raise ValueError('卡盒前缀格式错误')
    env = read_bridge_env(args.bridge_env)
    touched, bound = set(), 0
    while True:
        row = db.execute('SELECT v.jhs_version_id FROM jhs_versions v JOIN products p ON p.id=v.product_id '
                         'WHERE v.pack_prefix=? AND v.object_type=\'card\' AND NOT EXISTS (SELECT 1 FROM jhs_version_products vp WHERE vp.jhs_version_id=v.jhs_version_id) ORDER BY v.jhs_version_id LIMIT 1',
                         (args.prefix,)).fetchone()
        if row is None:
            counts = db.execute('SELECT COUNT(DISTINCT jhs_card_id),COUNT(*) FROM jhs_versions WHERE pack_prefix=? AND object_type=\'card\'',
                                (args.prefix,)).fetchone()
            if not counts[1]:
                raise ValueError('没有可补齐的已导入盒号')
            return {'prefix': args.prefix, 'cards': counts[0], 'versions': counts[1],
                    'products_updated': len(touched), 'versions_bound': bound}
        seed = row[0]
        found = find_products(SimpleNamespace(bridge_env=args.bridge_env, version_id=seed, keyword=''))
        if found.get('object_type') == 'unknown':
            raise ValueError('源数据缺少商品类型，不能确认是否为卡片')
        if found.get('object_type', 'card') != 'card':
            with db:
                db.execute('UPDATE jhs_versions SET object_type=? WHERE jhs_version_id=?',
                           (found['object_type'], seed))
            continue
        product = found.get('product')
        if not isinstance(product, dict) or type(product.get('id')) is not int or product['id'] <= 0:
            raise ValueError('商品身份缺失')
        pid = product['id']
        if pid in touched:
            raise ValueError('系列返回结果未覆盖待补齐版本')
        raw = fetch(f"http://127.0.0.1:{int(env.get('JHS_HTTP_PORT', '8791'))}/v1/product-versions",
                    data=encode({'pack_id': pid}).encode(), timeout=300,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['JHS_ACCESS_TOKEN']})
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get('product'), dict) or body['product'].get('id') != pid:
            raise ValueError('系列身份不一致')
        rows = body.get('versions')
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows) or seed not in {r.get('id') for r in rows}:
            raise ValueError('系列没有返回用于确认归属的卡片版本')
        save_snapshot(snapshots, f'jhs-product-{pid}-{hashlib.sha256(raw).hexdigest()[:16]}.json', raw)
        rows = enrich_identities(db, rows, env, snapshots)
        # 原官网关联保留在旧盒号记录上，不按译名猜测两个平台的商品对应。
        import_pack(db, None, rows, product=body['product'])
        touched.add(pid)
        card_count = sum(row.get('object_type', 'card') == 'card' for row in rows)
        bound += card_count
        print(f'补齐 {args.prefix}：商品 {pid} {body["product"]["name"]}，{card_count} 个卡片版本', flush=True)
        time.sleep(1)


def find_products(args):
    """只读取平台商品目录或卡片版本归属，不修改主库。"""
    env = read_bridge_env(args.bridge_env)
    route = 'product-for-version' if args.version_id else 'products'
    payload = {'version_id': args.version_id} if args.version_id else {'keyword': args.keyword}
    raw = fetch(f"http://127.0.0.1:{int(env.get('JHS_HTTP_PORT', '8791'))}/v1/{route}",
                data=encode(payload).encode(), timeout=180,
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['JHS_ACCESS_TOKEN']})
    return json.loads(raw)


def find(db, query):
    if query.isdigit() and int(query) > 0:
        ids = [r[0] for r in db.execute('SELECT card_id FROM card_identifiers WHERE kind=? AND value=?', identifier(int(query)))]
    else:
        ids = [r[0] for r in db.execute('SELECT DISTINCT card_id FROM card_names WHERE normalized=?', (normalize(query),))]
    result = []
    for card_id in ids:
        card = dict(db.execute('SELECT * FROM card_catalog WHERE id=?', (card_id,)).fetchone())
        card.pop('source_json')
        card.pop('source_hash')
        card['names'] = [dict(r) for r in db.execute('SELECT language,source,name FROM card_names WHERE card_id=?', (card_id,))]
        card['texts'] = [dict(r) for r in db.execute('SELECT language,source,type_text,effect,pendulum_effect FROM card_texts WHERE card_id=?', (card_id,))]
        card['releases'] = [dict(r) for r in db.execute('SELECT language,release_date,pack_name,source FROM card_releases WHERE card_id=?', (card_id,))]
        card['versions'] = [dict(r) for r in db.execute('SELECT v.jhs_version_id,v.jhs_card_id,v.number_raw,v.rarity_raw,'
                            'v.product_id,p.jhs_pack_id,p.jhs_name,p.jhs_name_origin '
                            'FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) JOIN products p ON p.id=v.product_id '
                            "WHERE j.card_id=? AND v.object_type='card' ORDER BY v.number_raw,v.rarity_raw", (card_id,))]
        for version in card['versions']:
            version['products'] = [dict(r) for r in db.execute(
                'SELECT p.id AS product_id,p.jhs_pack_id,p.jhs_name,p.jhs_name_origin FROM jhs_version_products vp '
                'JOIN products p ON p.id=vp.product_id WHERE vp.jhs_version_id=? ORDER BY p.jhs_pack_id',
                (version['jhs_version_id'],))]
        result.append(card)
    return result


def stats(db):
    result = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
              ('cards', 'card_names', 'card_texts', 'card_identifiers', 'card_releases', 'packs', 'products', 'product_official_links', 'jhs_cards', 'jhs_versions', 'jhs_version_products')}
    result.update(
        temporary_only=db.execute('SELECT COUNT(*) FROM cards WHERE temporary_id IS NOT NULL AND passcode IS NULL').fetchone()[0],
        with_reading=db.execute('SELECT COUNT(*) FROM cards WHERE japanese_reading IS NOT NULL').fetchone()[0],
        excluded_or_pending=[dict(r) for r in db.execute('SELECT source,reason,COUNT(*) AS count FROM import_issues GROUP BY source,reason')],
        unmatched_jhs_cards=db.execute("SELECT COUNT(*) FROM jhs_cards j WHERE card_id IS NULL AND EXISTS "
            "(SELECT 1 FROM jhs_versions v WHERE v.jhs_card_id=j.jhs_card_id AND v.object_type='card')").fetchone()[0],
        version_types={r[0]: r[1] for r in db.execute('SELECT object_type,COUNT(*) FROM jhs_versions GROUP BY object_type')},
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0],
        foreign_key_errors=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')],
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('sync-base')
    reconcile = commands.add_parser('reconcile-jhs')
    reconcile.add_argument('--bridge-env', type=Path)
    artwork = commands.add_parser('import-artworks')
    artwork.add_argument('--mc-db', type=Path, required=True)
    commands.add_parser('stats')
    lookup = commands.add_parser('find')
    lookup.add_argument('query')
    products = commands.add_parser('find-products')
    products.add_argument('keyword', nargs='?', default='')
    products.add_argument('--version-id', type=int)
    products.add_argument('--bridge-env', type=Path)
    pack = commands.add_parser('sync-pack')
    pack.add_argument('prefix', type=str.upper, nargs='?')
    pack.add_argument('--jhs-pack-id', type=int)
    pack.add_argument('--konami-pid')
    pack.add_argument('--bridge-env', type=Path)
    pack.add_argument('--expected-cards', type=int)
    pack.add_argument('--expected-versions', type=int)
    pack.add_argument('--name')
    pack.add_argument('--release-date')
    pack.add_argument('--source-url')
    args = parser.parse_args()
    if args.command == 'find-products':
        print(json.dumps(find_products(args), ensure_ascii=False, indent=2))
        return
    with connect(args.db) as db:
        if args.command in ('sync-base', 'sync-pack', 'import-artworks', 'reconcile-jhs'):
            backup(db, args.db)
        if args.command == 'sync-base':
            result = sync_ygocdb(db, args.db.parent / 'source-snapshots')
        elif args.command == 'import-artworks':
            with db:
                result = import_artwork_ids(db, args.mc_db)
        elif args.command == 'sync-pack':
            result = sync_pack(db, args, args.db.parent / 'source-snapshots')
        elif args.command == 'reconcile-jhs':
            result = reconcile_jhs(db, read_bridge_env(args.bridge_env), args.db.parent / 'source-snapshots')
        elif args.command == 'find':
            result = find(db, args.query)
        else:
            result = stats(db)
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, urllib.error.URLError, zipfile.BadZipFile) as exc:
        # 不打印 Request/认证头/环境变量。
        raise SystemExit(f'{type(exc).__name__}: {exc}') from None
