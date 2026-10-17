"""网站的 MyCard 随机短码跳转，只读访问映射和归档库。"""
import asyncio
from collections import Counter
from html import escape
import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, JSONResponse, Response

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


@router.api_route('/mc/{code:path}', methods=['GET', 'HEAD'])
async def player_deck(code: str):
    try:
        mapping = ShortLinks(settings.mycard_archive_dir / 'short-links.sqlite3')
        player = await asyncio.to_thread(mapping.player, code)
        if player is None:
            return PlainTextResponse('链接不存在', status_code=404, headers=HEADERS)
        links = await asyncio.to_thread(latest_links, player)
    except (sqlite3.Error, ValueError):
        return PlainTextResponse('卡组暂时无法查询，请稍后重试', status_code=503, headers=HEADERS)
    if not links:
        return PlainTextResponse('暂未收录该玩家的卡组', status_code=404, headers=HEADERS)
    if len(links) == 1:
        # 卡组会更新，不允许浏览器永久记住旧的跳转目标。
        return RedirectResponse(links[0], status_code=302, headers=HEADERS)
    # 与 Bot 保持一致：极少数累计 Side 超限的卡组保留全部链接，避免静默丢卡。
    items = ''.join(f'<li><a href="{escape(url, quote=True)}">卡组链接 {i}</a></li>'
                    for i, url in enumerate(links, 1))
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<title>{escape(player)}的卡组</title><h1>{escape(player)}</h1>'
                        f'<ul>{items}</ul></html>', headers=HEADERS)
