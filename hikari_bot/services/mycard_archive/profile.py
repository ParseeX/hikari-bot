"""玩家公开卡组页：只输出公开资料，不返回采集状态、对局原文或用户绑定。"""
from .sorting import sort_sections


def profile_payload(player, version, stats, catalog, title=''):
    fields = ('rank', 'score', 'month_games', 'month_win_rate', 'month', 'trend',
              'stats_updated_at', 'history_complete')
    result = {'player': player, 'stats': {key: stats.get(key) for key in fields}, 'deck': None}
    if version is None:
        return result
    deck = version.deck
    main, extra, side = sort_sections(deck.main, deck.extra, deck.side, catalog=catalog)
    codes = set(main + extra + side)
    cards = catalog.cards_by_id(codes)
    # 异画卡号仍用于下载与图片；统一身份仅用于资料查询。
    details = {}
    for code in codes:
        info = cards.get(code) or {}
        details[str(code)] = {
            'code': code, 'id': info.get('id', code), 'cid': info.get('cid'),
            'name': info.get('cn_name') or info.get('jp_name') or info.get('en_name') or f'卡片 {code}',
            'jp_name': info.get('jp_name', ''), 'en_name': info.get('en_name', ''),
            'text': info.get('text', {}), 'data': info.get('data', {}),
            'image': f'/mc/api/card-image/{code}',
        }
    result['deck'] = {'name': title or '未识别', 'main': main, 'extra': extra, 'side': side,
                      'updated_at': version.updated_at, 'cards': details}
    return result


def profile_ydk(deck):
    # 不拼接玩家名称到注释，防止换行改变 YDK 结构。
    return '\n'.join(['#created by ygo.xyk.one', '#main', *map(str, deck['main']),
                      '#extra', *map(str, deck['extra']), '!side', *map(str, deck['side']), ''])
