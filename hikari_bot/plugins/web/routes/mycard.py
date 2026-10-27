"""MyCard 公开统计、玩家卡组页与随机短码，只读访问映射和归档库。"""
import asyncio
from collections import Counter
from urllib.parse import quote
import json
import hashlib
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, JSONResponse, Response

from hikari_bot.core.config import settings
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.presentation import deck_links, classify
from hikari_bot.services.mycard_archive.queries import deck_title
from hikari_bot.services.mycard_archive.front_stats import FrontStats
from hikari_bot.services.mycard_archive.series_stats import SeriesStats
from hikari_bot.services.mycard_archive.card_stats import card_usage
from hikari_bot.services.mycard_archive.matchups import build_matchups
from hikari_bot.services.mycard_archive.replay import Deck
from hikari_bot.services.mycard_archive.rule_admin import RuleAdmin, representative_cards
from hikari_bot.services.mycard_archive.art import card_art
from hikari_bot.services.mycard_archive.short_links import ShortLinks
from hikari_bot.services.mycard_archive.profile import profile_payload, profile_ydk
from hikari_bot.services.mycard_archive.full_image import full_card_image, check_chinese_image, next_chinese_check


router = APIRouter()
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
front_stats = FrontStats(settings.mycard_archive_dir / 'front-stats.json')


def catalog():
    return CardCatalog(settings.card_catalog_path)


def effective_representatives(data):
    return representative_cards(data, catalog())


def front_rows(players, stats, *, latest=None):
    root = settings.mycard_archive_dir
    data, _ = RuleAdmin(root / 'deck-rules.json').read()
    if latest is None:
        latest = DeckLibrary(root / 'archive.sqlite3').latest_many(players)
    representatives = effective_representatives(data)
    rows = []
    for player in players:
        version = latest.get(player)
        deck = None
        if version:
            labels = classify(version.deck, data['rules'])['labels']
            deck = {'name': deck_title(version.deck, data['rules']) or '未识别',
                    'categories': [{'label': label, 'image':
                     f'/mc/api/art/{representatives[label]}' if label in representatives else None}
                    for label in labels], 'links': deck_links(version.deck),
                    'updated_at': version.updated_at}
        # 分类属于同一完整卡组，不将混合构筑拆成多套卡组。
        rows.append({**{key: value for key, value in stats[player].items() if not key.startswith('_')},
                     'player': player, 'deck': deck})
    return rows


@router.get('/mc/api/leaderboard')
async def front_page(page: int = Query(default=1, ge=1, le=10)):
    content = series_stats.read_page(page)
    if content is None:
        return JSONResponse({'detail': '前排卡组暂时无法查询，请稍后重试'}, status_code=503, headers=HEADERS)
    return Response(content, media_type='application/json', headers=HEADERS)


def build_deck_stats():
    """仅供后台调用：系列表和下钻玩家使用同一份快照。"""
    snapshot = front_stats.snapshot()
    players = snapshot['players'][:100]
    if not players:
        raise ValueError('榜单尚未准备好')
    latest = DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3').latest_many(players)
    rows = front_rows(players, snapshot['stats'], latest=latest)
    rows.sort(key=lambda row: row['rank'] if isinstance(row.get('rank'), int) else float('inf'))
    summaries = {}
    for row in rows:
        categories = {c['label']: c['image'] for c in row['deck']['categories']} if row['deck'] else {}
        for label, image in categories.items():
            entry = summaries.setdefault(label, {'label': label, 'count': 0, 'image': image,
                                                  'highest_rank': None, 'partners': Counter()})
            entry['count'] += 1
            rank = row.get('rank')
            if isinstance(rank, int) and rank > 0:
                entry['highest_rank'] = min(entry['highest_rank'] or rank, rank)
            # 同一玩家每个系列只计一次，搭配也按共同使用的玩家人数计数。
            entry['partners'].update(other for other in categories if other != label)
    series = sorted(summaries.values(), key=lambda entry: (-entry['count'], entry['label']))
    for entry in series:
        entry['partners'] = [{'label': label, 'count': count} for label, count in
                             sorted(entry['partners'].items(), key=lambda item: (-item[1], item[0]))]
    rules, _ = RuleAdmin(settings.mycard_archive_dir / 'deck-rules.json').read()
    def labels(raw):
        return classify(Deck(raw['player'], tuple(raw['main']), tuple(raw['extra'])), rules['rules'])['labels']
    matchups = build_matchups(snapshot, settings.mycard_archive_dir / 'archive.sqlite3',
                              labels, effective_representatives(rules))
    if matchups is None and series_stats.read_matchups() is not None:
        matchups = json.loads(series_stats.read_matchups())
    return {'series': series, 'players': rows, 'total': len(players), 'updated_at': snapshot['updated_at'],
            'match_stats': matchups,
            'player_order': players, 'card_stats': {**card_usage(players, latest, catalog()),
                                                   'updated_at': snapshot['updated_at']}}


series_stats = SeriesStats(settings.mycard_archive_dir / 'series-stats.json', build_deck_stats)
front_stats.on_refreshed = series_stats.request_refresh


@router.get('/mc/api/deck-stats')
async def deck_stats():
    content = series_stats.read()
    if content is None:
        return JSONResponse({'detail': '系列统计暂时无法查询，请稍后重试'}, status_code=503, headers=HEADERS)
    return Response(content, media_type='application/json', headers=HEADERS)


@router.get('/mc/api/art-subjects')
async def art_subjects():
    # 独立读取轻量图片设置，保存后无需等待前百统计快照重算。
    try:
        data, _ = await asyncio.to_thread(RuleAdmin(settings.mycard_archive_dir / 'deck-rules.json').read)
    except (OSError, ValueError):
        raise HTTPException(503, '图片主体设置暂不可用') from None
    return JSONResponse({'subjects': data.get('art_subjects', {})}, headers=HEADERS)


@router.get('/mc/api/card-stats')
async def card_stats():
    content = series_stats.read_cards()
    if content is None:
        return JSONResponse({'detail': '卡片统计暂时无法查询，请稍后重试'}, status_code=503, headers=HEADERS)
    return Response(content, media_type='application/json', headers=HEADERS)


@router.get('/mc/api/match-stats')
async def match_stats():
    content = series_stats.read_matchups()
    if content is None:
        return JSONResponse({'detail': '系列胜率暂时无法查询，请稍后重试'}, status_code=503, headers=HEADERS)
    return Response(content, media_type='application/json', headers=HEADERS)


@router.get('/mc/api/card-art/{code}')
async def usage_art(code: int):
    if not series_stats.has_card(code):
        raise HTTPException(404, '图片不存在')
    data = await card_art(code)
    if not data:
        raise HTTPException(404, '图片暂不可用', headers=HEADERS)
    return Response(data, media_type='image/jpeg', headers={
        'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff'})


@router.get('/mc/api/art/{code}')
async def representative_art(code: int):
    try:
        data, _ = await asyncio.to_thread(RuleAdmin(settings.mycard_archive_dir / 'deck-rules.json').read)
        representatives = await asyncio.to_thread(effective_representatives, data)
    except (OSError, ValueError, sqlite3.Error):
        raise HTTPException(503, '分类资料暂不可用') from None
    if code not in representatives.values():
        raise HTTPException(404, '图片不存在')
    image = await card_art(code)
    if not image:
        raise HTTPException(404, '图片暂不可用', headers=HEADERS)
    return Response(image, media_type='image/jpeg', headers={
        'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff'})


def latest_links(player):
    library = DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3')
    version = library.latest(player)
    return deck_links(version.deck) if version else []


def load_profile(name, code):
    root = settings.mycard_archive_dir
    snapshot = front_stats.snapshot()
    if code:
        player = ShortLinks(root / 'short-links.sqlite3').player(code)
    else:
        # 姓名入口只公开已在榜单展示的玩家；其他玩家沿用既有随机短链接。
        player = name if name in snapshot['players'] else None
    if player is None:
        return None
    version = DeckLibrary(root / 'archive.sqlite3').latest(player)
    rules, _ = RuleAdmin(root / 'deck-rules.json').read()
    title = deck_title(version.deck, rules['rules']) if version else ''
    return profile_payload(player, version, snapshot['stats'].get(player, {}), catalog(), title)


@router.get('/mc/api/player')
async def player_profile(name: str = Query(default='', max_length=128),
                         code: str = Query(default='', max_length=12),
                         format: str = Query(default='json', pattern='^(json|ydk)$')):
    if bool(name) == bool(code):
        raise HTTPException(400, '请选择一个玩家', headers=HEADERS)
    try:
        data = await asyncio.to_thread(load_profile, name, code)
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, '玩家资料暂不可用，请稍后重试', headers=HEADERS) from None
    if data is None:
        raise HTTPException(404, '玩家链接不存在或已不在当前榜单', headers=HEADERS)
    if format == 'ydk':
        if data['deck'] is None:
            raise HTTPException(404, '暂未收录该玩家的卡组', headers=HEADERS)
        filename = quote(data['player'], safe='') + '.ydk'
        return Response(profile_ydk(data['deck']), media_type='application/octet-stream',
                        headers={**HEADERS, 'X-Content-Type-Options': 'nosniff',
                                 'Content-Disposition': f"attachment; filename=deck.ydk; filename*=UTF-8''{filename}"})
    return JSONResponse(data, headers=HEADERS)


@router.get('/mc/api/card-image/{code}')
async def profile_image(code: int, request: Request,
                        refresh: str = Query('', max_length=64, pattern=r'^[A-Za-z0-9_-]*$'),
                        v: str = Query('', max_length=64, pattern=r'^([a-f0-9]{64})?$'),
                        size: str = Query('full', pattern='^(full|thumb)$')):
    if not 0 < code < 2**31:
        raise HTTPException(404, '卡片不存在')
    try:
        info = await asyncio.to_thread(catalog().by_id, code)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, '卡片资料暂不可用', headers=HEADERS) from None
    if not info:
        raise HTTPException(404, '卡片不存在', headers=HEADERS)
    image, language = await full_card_image(code, info, refresh, size)
    if not image:
        raise HTTPException(404, '图片暂不可用', headers={'Cache-Control': 'public, max-age=60'})
    version = hashlib.sha256(image).hexdigest()
    headers = {
        'Cache-Control': 'no-store' if v and v != version else 'public, max-age=31536000',
        'X-Content-Type-Options': 'nosniff',
        'X-Card-Image-Language': language, 'ETag': '"' + version + '"'}
    if headers['ETag'] in request.headers.get('if-none-match', '').split(', '):
        return Response(status_code=304, headers=headers)
    return Response(image, media_type='image/jpeg', headers=headers)


@router.get('/mc/api/card-image/{code}/refresh')
async def refresh_profile_image(code: int, size: str = Query('full', pattern='^(full|thumb)$')):
    if not 0 < code < 2**31:
        raise HTTPException(404, '卡片不存在', headers=HEADERS)
    try:
        info = await asyncio.to_thread(catalog().by_id, code)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, '卡片资料暂不可用', headers=HEADERS) from None
    if not info:
        raise HTTPException(404, '卡片不存在', headers=HEADERS)
    image = await check_chinese_image(code, info, size)
    return JSONResponse({'language': 'zh' if image else 'en',
                         'version': hashlib.sha256(image).hexdigest() if image else None,
                         'next_check': None if image else await asyncio.to_thread(next_chinese_check, code)}, headers=HEADERS)


@router.api_route('/mc/{code:path}', methods=['GET', 'HEAD'])
async def player_deck(code: str):
    try:
        mapping = ShortLinks(settings.mycard_archive_dir / 'short-links.sqlite3')
        player = await asyncio.to_thread(mapping.player, code)
        if player is None:
            return PlainTextResponse('链接不存在', status_code=404, headers=HEADERS)
    except (sqlite3.Error, ValueError):
        return PlainTextResponse('卡组暂时无法查询，请稍后重试', status_code=503, headers=HEADERS)
    return RedirectResponse('/mc/player/?code=' + quote(code, safe=''), status_code=302, headers=HEADERS)
