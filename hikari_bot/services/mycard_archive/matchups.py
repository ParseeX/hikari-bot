"""关联官方整场结果与当场录像；只统计有唯一证据的本月系列对阵。"""
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from hikari_bot.services.mycard_history import month_key, record_month


HISTORY_FIELDS = ('usernamea', 'usernameb', 'start_time', 'end_time',
                  'winner', 'userscorea', 'userscoreb')


def compact_history(records, players, month):
    """复用本轮已取得的历史，只保留当前榜内双方的本月结算字段。"""
    names = set(players)
    return [{key: row.get(key) for key in HISTORY_FIELDS} for row in records
            if row.get('type') == 'athletic' and row.get('usernamea') in names
            and row.get('usernameb') in names and record_month(row) == month]


def timestamp(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('对局时间缺少时区')
    return at.timestamp()


def results(snapshot):
    """双边历史去重；冲突结果整场排除，避免按拉取顺序选一个赢家。"""
    players = set(snapshot['players'])
    month = month_key(snapshot['updated_at'])
    found, conflicts = {}, set()
    for player in players:
        row = snapshot['stats'][player]
        if row.get('month') != month or '_matches' not in row:
            return None
        for raw in row['_matches']:
            try:
                a, b = raw['usernamea'], raw['usernameb']
                if a == b or a not in players or b not in players or record_month(raw) != month:
                    continue
                start, end = timestamp(raw['start_time']), timestamp(raw['end_time'])
                if end <= start or end > snapshot['updated_at'] + 60:
                    continue
                pair = tuple(sorted((a, b)))
                key = (*pair, start, end)
                scores = {a: raw['userscorea'], b: raw['userscoreb']}
                value = (raw['winner'], scores[pair[0]], scores[pair[1]])
                if key in found and found[key] != value:
                    conflicts.add(key)
                found[key] = value
            except (KeyError, TypeError, ValueError):
                continue
    result = []
    for key, (winner, score_a, score_b) in found.items():
        # 平局直接忽略；退出惩罚等负比分不能冒充已确认的小局数量。
        if (key in conflicts or winner not in key[:2]
                or any(type(score) is not int or not 0 <= score <= 2 for score in (score_a, score_b))
                or score_a == score_b or winner != key[0 if score_a > score_b else 1]):
            continue
        result.append({'key': key, 'players': key[:2], 'start': key[2], 'end': key[3],
                       'winner': winner, 'games': score_a + score_b})
    return result


def archives(path, players, earliest):
    """只读索引；不实例化会迁移或补录数据库的 ArchiveStore。"""
    grouped = defaultdict(list)
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        for match_id, raw_players in db.execute(
                "SELECT id,players FROM matches WHERE status='complete' AND finished>=?", (earliest,)):
            names = json.loads(raw_players)
            if len(names) != 2 or len(set(names)) != 2 or not set(names) <= players:
                continue
            replays = [json.loads(row[0]) for row in db.execute(
                'SELECT metadata FROM replays WHERE match_id=? ORDER BY ordinal', (match_id,))]
            if not replays or any(type(r.get('start_time')) is not int or r['start_time'] <= 0
                                  or len(r.get('decks', [])) != 2
                                  or {d['player'] for d in r['decks']} != set(names) for r in replays):
                continue
            replays.sort(key=lambda replay: replay['start_time'])
            grouped[tuple(sorted(names))].append({'id': match_id, 'replays': replays})
    return grouped


def build_matchups(snapshot, path, classify_labels, representatives):
    if snapshot.get('pending_count'):
        return None
    official = results(snapshot)
    if official is None:
        return None  # 旧缓存升级时等待现有十五分钟任务补齐，而非显示伪造的零场。
    output = {'month': month_key(snapshot['updated_at']), 'updated_at': snapshot['updated_at'],
              'total': 100, 'effective_matches': 0, 'pairs': [], 'series_totals': []}
    if not official:
        return output
    groups = archives(path, set(snapshot['players']), min(row['start'] for row in official))
    candidates, usages = [], defaultdict(int)
    for result in official:
        matching = [archive for archive in groups[result['players']]
                    if result['start'] <= archive['replays'][0]['start_time']
                    and archive['replays'][-1]['start_time'] <= result['end']]
        for archive in matching:
            usages[archive['id']] += 1
        # 同一双方连续重赛或重复归档不能挑最近的一场猜测。
        if len(matching) != 1:
            continue
        archive = matching[0]
        candidates.append((result, archive))
    pairs, totals = {}, {}
    for result, archive in candidates:
        if usages[archive['id']] != 1 or len(archive['replays']) != result['games']:
            continue
        first = archive['replays'][0]['decks']
        labels = {deck['player']: set(classify_labels(deck)) for deck in first}
        a, b = result['players']
        outcomes = defaultdict(set)
        for left in labels[a]:
            for right in labels[b]:
                if left != right:
                    pair = tuple(sorted((left, right)))
                    outcomes[pair].add(left if result['winner'] == a else right)
        contributed = False
        for pair, winners in outcomes.items():
            # 双方都混合 A+B 时，A 对 B 归属冲突；不计成一胜一负。
            if len(winners) != 1:
                continue
            row = pairs.setdefault(pair, {'a': pair[0], 'b': pair[1], 'wins_a': 0, 'wins_b': 0})
            row['wins_a' if next(iter(winners)) == pair[0] else 'wins_b'] += 1
            contributed = True
        output['effective_matches'] += int(contributed)
        if contributed:
            # 均胜率覆盖所有对手系列，每场每系列只计一次；双方共有系列属于内战。
            for label in labels[a] ^ labels[b]:
                owner = a if label in labels[a] else b
                row = totals.setdefault(label, {'label': label, 'wins': 0, 'losses': 0})
                row['wins' if result['winner'] == owner else 'losses'] += 1
    for row in pairs.values():
        row['games'] = row['wins_a'] + row['wins_b']
        row['rate_a'] = row['wins_a'] * 100 / row['games']
        row['rate_b'] = row['wins_b'] * 100 / row['games']
        for side in ('a', 'b'):
            code = representatives.get(row[side])
            row['image_' + side] = f'/mc/api/art/{code}' if code else None
    output['pairs'] = sorted(pairs.values(), key=lambda row: (-row['games'], row['a'], row['b']))
    for row in totals.values():
        row['games'] = row['wins'] + row['losses']
        row['rate'] = row['wins'] * 100 / row['games']
    output['series_totals'] = sorted(totals.values(), key=lambda row: row['label'])
    return output
