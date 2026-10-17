"""官方对局历史的有效 PT 和月度汇总，不使用本地观战覆盖率推算。"""
from datetime import datetime
import math
from zoneinfo import ZoneInfo


def is_zero_delta_match(record, player):
    try:
        side = 'a' if record.get('usernamea') == player else 'b' if record.get('usernameb') == player else None
        return side is not None and abs(float(record['pt' + side]) - float(record['pt' + side + '_ex'])) < 1e-9
    except (KeyError, TypeError, ValueError):
        return False


def month_key(now):
    return datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')


def record_month(record):
    """按北京时间开局月份归属，跨月结算记录不混入新月份的 PT。"""
    start = datetime.fromisoformat(record['start_time'].replace('Z', '+00:00'))
    if start.tzinfo is None:
        raise ValueError('历史开局时间缺少时区')
    return start.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')


def month_start_point(year, month, previous_score=None):
    """月初重置点：季度首月统一 1000，其余月份只将超出 1000 的部分减半。"""
    at = datetime(year, month, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
    if month in (1, 4, 7, 10):
        score = 1000
    else:
        if type(previous_score) not in (int, float) or not math.isfinite(previous_score):
            raise ValueError('缺少官方上月重置前分数')
        score = previous_score if previous_score <= 1000 else 1000 + (previous_score - 1000) / 2
    return {'time': at.timestamp(), 'score': score}


def month_trend(records, player, year, month, previous_score=None):
    """网页与 Bot 共用起点及赛后点；不再插入首场赛前分数。"""
    points = [month_start_point(year, month, previous_score)]
    valid = [r for r in records if record_month(r) == f'{year:04d}-{month:02d}'
             and not is_zero_delta_match(r, player)]
    valid.sort(key=lambda r: (datetime.fromisoformat(r['end_time'].replace('Z', '+00:00')),
                              datetime.fromisoformat(r['start_time'].replace('Z', '+00:00'))))
    for record in valid:
        side = 'a' if record['usernamea'] == player else 'b'
        points.append({'time': datetime.fromisoformat(record['end_time'].replace('Z', '+00:00')).timestamp(),
                       'score': record['pt' + side]})
    return points


def history_summary(payload, player, now, previous_score=None):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('历史响应无效')
    records = payload['data']
    total = payload.get('total')
    if type(total) is not int or total < len(records):
        raise ValueError('历史总数无效')
    month = month_key(now)
    valid = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('历史记录无效')
        side = 'a' if record.get('usernamea') == player else 'b' if record.get('usernameb') == player else None
        if side is None or record.get('type') != 'athletic':
            raise ValueError('历史玩家或类型不匹配')
        start = datetime.fromisoformat(record['start_time'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(record['end_time'].replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end < start or end.timestamp() > now + 60:
            raise ValueError('历史时间无效')
        identity = (record['usernamea'], record['usernameb'], record['start_time'], record['end_time'])
        if identity in seen:
            raise ValueError('历史记录重复，不能确认覆盖完整')
        seen.add(identity)
        before, after = record.get('pt' + side + '_ex'), record.get('pt' + side)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (before, after)):
            raise ValueError('历史分数无效')
        if not isinstance(record.get('winner'), str):
            raise ValueError('历史胜者无效')
        if record_month(record) != month or is_zero_delta_match(record, player):
            continue
        valid.append((start.timestamp(), end.timestamp(), before, after, record['winner'] == player))
    valid.sort(key=lambda row: (row[1], row[0]))
    # 网页只请求最新 1000 场；按每位玩家每月最多 1000 场的业务上限，
    # 取满该窗口就已覆盖本月，无需取得账号全部历史。短响应不视为完整。
    complete = len(records) == min(total, 1000)
    year, month_number = map(int, month.split('-'))
    points = month_trend(records, player, year, month_number, previous_score)
    return {'month': month, 'month_games': len(valid) if complete else None,
            'month_win_rate': (sum(row[4] for row in valid) * 100 / len(valid)) if complete and valid else None,
            'trend': points, 'history_complete': complete}
