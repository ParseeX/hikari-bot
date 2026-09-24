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
import unicodedata
import urllib.error
import urllib.request
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
        if row is None or row[0] != '1':
            db.close()
            raise ValueError('不支持的卡片主库版本')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    db.execute("INSERT OR IGNORE INTO catalog_meta VALUES ('schema_version','1')")
    db.commit()
    return db


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
        # 不把缺少资料的官方奖品卡/特殊卡猜成普通卡或衍生物。
        if cid <= 0 or not t or not (int(data.get('ot') or 0) & 11):
            issue(db, 'ygocdb', source_key, 'missing_or_out_of_scope_data', record)
            counts['pending'] += 1
            continue
        old = db.execute('SELECT * FROM cards WHERE konami_cid=?', (cid,)).fetchone()
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
    names = {normalize(n) for r in rows for n in
             [r.get('name_cn', ''), r.get('name_jp', ''), *(r.get('aliases') or [])] if n}
    found = set()
    for name in names:
        found.update(r[0] for r in db.execute('SELECT DISTINCT card_id FROM card_names WHERE normalized=?', (name,)))
    return found


def import_pack(db, prefix, rows, *, expected_cards=None, expected_versions=None,
                name=None, release_date=None, source_url=None):
    prefix = prefix.upper()
    if not re.fullmatch('[A-Z0-9]{1,16}', prefix):
        raise ValueError('卡盒前缀格式错误')
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('版本条目格式错误')
        # 搜索可能模糊命中其他物品；严格限定编号，原文仍完整存储。
        if not str(row.get('number', '')).upper().startswith(prefix + '-'):
            continue
        if not str(row.get('rarity') or '').strip() or int(row.get('id') or 0) <= 0 or int(row.get('card_id') or 0) <= 0:
            raise ValueError('卡盒中存在缺少 ID 或罕贵的版本')
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
        db.execute('INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET '
                   'name=COALESCE(excluded.name,packs.name),release_date=COALESCE(excluded.release_date,packs.release_date), '
                   'source_url=COALESCE(excluded.source_url,packs.source_url), '
                   'expected_cards=COALESCE(excluded.expected_cards,packs.expected_cards), '
                   'expected_versions=COALESCE(excluded.expected_versions,packs.expected_versions), '
                   'observed_cards=excluded.observed_cards,observed_versions=excluded.observed_versions,last_synced_at=excluded.last_synced_at',
                   (prefix, name, release_date, source_url, expected_cards, expected_versions, len(grouped), len(selected), stamp))
        for jhs_id, entries in grouped.items():
            old = db.execute('SELECT card_id FROM jhs_cards WHERE jhs_card_id=?', (jhs_id,)).fetchone()
            candidates = candidate_cards(db, entries)
            if old and old[0] is not None:
                if candidates and old[0] not in candidates:
                    raise ValueError(f'集换社卡片 {jhs_id} 与已有映射冲突')
                card_id, method = old[0], 'existing_mapping'
            elif len(candidates) == 1:
                card_id, method = next(iter(candidates)), 'unique_exact_name'
            else:
                card_id, method = None, 'pending'
            db.execute('INSERT INTO jhs_cards VALUES (?,?,?,?) ON CONFLICT(jhs_card_id) DO UPDATE SET '
                       'card_id=excluded.card_id,match_method=excluded.match_method,updated_at=excluded.updated_at',
                       (jhs_id, card_id, method, stamp))
            if card_id is None:
                issue(db, 'jhs_mapping', jhs_id, 'ambiguous_or_missing_name', entries)
                counts['unmatched_cards'] += 1
            else:
                clear_issue(db, 'jhs_mapping', jhs_id)
                counts['matched_cards'] += 1
        for version_id, row in selected.items():
            old = db.execute('SELECT jhs_card_id,pack_prefix,source_json FROM jhs_versions WHERE jhs_version_id=?', (version_id,)).fetchone()
            if old and (old['jhs_card_id'] != int(row['card_id']) or old['pack_prefix'] != prefix):
                raise ValueError(f'集换社版本 {version_id} 的归属发生冲突')
            payload = encode(row)
            db.execute('INSERT INTO jhs_versions VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(jhs_version_id) DO UPDATE SET '
                       'number_raw=excluded.number_raw,rarity_raw=excluded.rarity_raw,name_cn=excluded.name_cn, '
                       'name_jp=excluded.name_jp,source_json=excluded.source_json,last_seen_at=excluded.last_seen_at, '
                       'updated_at=CASE WHEN jhs_versions.source_json!=excluded.source_json THEN excluded.updated_at ELSE jhs_versions.updated_at END',
                       (version_id, int(row['card_id']), prefix, row['number'], row['rarity'], row.get('name_cn', ''),
                        row.get('name_jp', ''), payload, stamp, stamp, stamp))
            counts['inserted' if not old else 'unchanged' if old['source_json'] == payload else 'updated'] += 1
        # 未返回的旧版本不删除；last_seen_at 可识别需要复核的历史记录。
        result = dict(counts, prefix=prefix, cards=len(grouped), versions=len(selected))
        db.execute('INSERT INTO sync_runs(source,completed_at,summary_json) VALUES (?,?,?)',
                   ('jhs:' + prefix, stamp, encode(result)))
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
    if not re.fullmatch('[A-Z0-9]{1,16}', args.prefix):
        raise ValueError('卡盒前缀格式错误')
    env = read_bridge_env(args.bridge_env)
    port = int(env.get('JHS_HTTP_PORT', '8791'))
    raw = fetch(f'http://127.0.0.1:{port}/v1/versions',
                data=encode({'name_jp': args.prefix}).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['JHS_ACCESS_TOKEN']},
                timeout=300)
    body = json.loads(raw)
    if not isinstance(body, dict) or body.get('error'):
        raise ValueError('桥接返回数据格式错误')
    rows = body.get('versions')
    if not isinstance(rows, list):
        raise ValueError('桥接没有返回完整版本列表')
    save_snapshot(snapshots, f'jhs-{args.prefix}-{hashlib.sha256(raw).hexdigest()[:16]}.json', raw)
    return import_pack(db, args.prefix, rows, expected_cards=args.expected_cards,
                       expected_versions=args.expected_versions, name=args.name,
                       release_date=args.release_date, source_url=args.source_url)


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
        card['versions'] = [dict(r) for r in db.execute('SELECT v.jhs_version_id,v.jhs_card_id,v.number_raw,v.rarity_raw '
                            'FROM jhs_versions v JOIN jhs_cards j USING(jhs_card_id) WHERE j.card_id=? ORDER BY v.number_raw,v.rarity_raw', (card_id,))]
        result.append(card)
    return result


def stats(db):
    result = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
              ('cards', 'card_names', 'card_texts', 'card_identifiers', 'card_releases', 'packs', 'jhs_cards', 'jhs_versions')}
    result.update(
        temporary_only=db.execute('SELECT COUNT(*) FROM cards WHERE temporary_id IS NOT NULL AND passcode IS NULL').fetchone()[0],
        with_reading=db.execute('SELECT COUNT(*) FROM cards WHERE japanese_reading IS NOT NULL').fetchone()[0],
        excluded_or_pending=[dict(r) for r in db.execute('SELECT source,reason,COUNT(*) AS count FROM import_issues GROUP BY source,reason')],
        unmatched_jhs_cards=db.execute('SELECT COUNT(*) FROM jhs_cards WHERE card_id IS NULL').fetchone()[0],
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
    artwork = commands.add_parser('import-artworks')
    artwork.add_argument('--mc-db', type=Path, required=True)
    commands.add_parser('stats')
    lookup = commands.add_parser('find')
    lookup.add_argument('query')
    pack = commands.add_parser('sync-pack')
    pack.add_argument('prefix', type=str.upper)
    pack.add_argument('--bridge-env', type=Path)
    pack.add_argument('--expected-cards', type=int)
    pack.add_argument('--expected-versions', type=int)
    pack.add_argument('--name')
    pack.add_argument('--release-date')
    pack.add_argument('--source-url')
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.command in ('sync-base', 'sync-pack', 'import-artworks'):
            backup(db, args.db)
        if args.command == 'sync-base':
            result = sync_ygocdb(db, args.db.parent / 'source-snapshots')
        elif args.command == 'import-artworks':
            with db:
                result = import_artwork_ids(db, args.mc_db)
        elif args.command == 'sync-pack':
            result = sync_pack(db, args, args.db.parent / 'source-snapshots')
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
