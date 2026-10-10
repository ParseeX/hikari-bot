"""网站的 MyCard 前百玩家卡组跳转，只读访问归档库。"""
import asyncio
from html import escape
import sqlite3

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from hikari_bot.core.config import settings
from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.presentation import deck_links
from hikari_bot.services.mycard_archive.queries import Leaderboard


router = APIRouter()
leaderboard = Leaderboard()
HEADERS = {'Cache-Control': 'no-store'}


def latest_links(player):
    library = DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3')
    version = library.latest(player)
    return deck_links(version.deck) if version else []


@router.api_route('/mc/{player:path}', methods=['GET', 'HEAD'])
async def player_deck(player: str):
    if not player or len(player) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in player):
        return PlainTextResponse('MyCard 用户名无效', status_code=400, headers=HEADERS)
    try:
        players = await leaderboard.players()
    except Exception:
        # 排名不可用时不使用过期快照放行，也不退回公开任意玩家的卡组。
        return PlainTextResponse('排名暂时无法查询，请稍后重试', status_code=503, headers=HEADERS)
    if player not in players:
        return PlainTextResponse('该玩家不在当前竞技场前 100 名', status_code=403, headers=HEADERS)
    try:
        links = await asyncio.to_thread(latest_links, player)
    except (sqlite3.Error, ValueError):
        return PlainTextResponse('卡组暂时无法查询，请稍后重试', status_code=503, headers=HEADERS)
    if not links:
        return PlainTextResponse('暂未收录该玩家的卡组', status_code=404, headers=HEADERS)
    if len(links) == 1:
        # 卡组和排名会更新，不允许浏览器永久记住旧的跳转目标。
        return RedirectResponse(links[0], status_code=302, headers=HEADERS)
    # 与 Bot 保持一致：极少数累计 Side 超限的卡组保留全部链接，避免静默丢卡。
    items = ''.join(f'<li><a href="{escape(url, quote=True)}">卡组链接 {i}</a></li>'
                    for i, url in enumerate(links, 1))
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<title>{escape(player)}的卡组</title><h1>{escape(player)}</h1>'
                        f'<ul>{items}</ul></html>', headers=HEADERS)
