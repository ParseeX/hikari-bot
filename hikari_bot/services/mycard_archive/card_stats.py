"""当前榜单最新卡组的主卡与已观测 Side 投入统计。"""
from collections import Counter


def card_usage(players, latest, catalog):
    decks = [latest[player].deck for player in players if player in latest]
    codes = {code for deck in decks for code in (*deck.main, *deck.side)}
    metadata = catalog.cards_by_id(codes)
    canonical = {code: (metadata.get(code) or {}).get('id') or code for code in codes}
    names = {}
    for code in sorted(codes):
        info = metadata.get(code) or {}
        names[canonical[code]] = (info.get('cn_name') or info.get('sc_name') or
                                  info.get('jp_name') or f'卡片 {canonical[code]}')
    totals = {}
    for deck in decks:
        main = Counter(canonical[code] for code in deck.main)
        side = Counter(canonical[code] for code in deck.side)
        for code in main.keys() | side.keys():
            entry = totals.setdefault(code, {'code': code, 'name': names[code],
                                             'image': f'/mc/api/card-art/{code}',
                                             'users': 0, 'main_copies': 0, 'side_copies': 0,
                                             'main_users': [0, 0, 0], 'side_users': [0, 0, 0]})
            entry['users'] += 1
            entry['main_copies'] += main[code]
            entry['side_copies'] += side[code]
            for zone, counts in (('main', main), ('side', side)):
                copies = counts[code]
                if 1 <= copies <= 3:
                    entry[zone + '_users'][copies - 1] += 1
    cards = sorted(totals.values(), key=lambda item: (-item['users'], item['code']))
    for entry in cards:
        # 保留旧字段，让尚未刷新的页面仍可读取缓存；新页面使用人数分布。
        entry['main_average'] = entry.pop('main_copies') / entry['users']
        entry['side_average'] = entry.pop('side_copies') / entry['users']
    return {'cards': cards, 'total': len(players), 'recorded_players': len(decks)}
