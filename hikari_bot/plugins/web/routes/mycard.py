"""网站的 MyCard 随机短码跳转，只读访问映射和归档库。"""
import asyncio
from html import escape
import sqlite3

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from hikari_bot.core.config import settings
from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.presentation import deck_links
from hikari_bot.services.mycard_archive.short_links import ShortLinks


router = APIRouter()
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}


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
