"""卡组查询文本和当前竞技场前 100 名快照。"""
import asyncio
import io
from datetime import datetime
import time
from zoneinfo import ZoneInfo

import aiohttp

from .short_links import ShortLinks
from .presentation import classify, read_rules


class ClassificationRulesError(ValueError):
    pass


def query_rules(path):
    try:
        return read_rules(path)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ClassificationRulesError('分类规则读取失败') from exc


class Leaderboard:
    def __init__(self, fetch=None, ttl=60):
        self.fetch = fetch or self._fetch
        self.ttl = ttl
        self.lock = asyncio.Lock()
        self.cached = None
        self.expires = 0

    async def _fetch(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get('https://sapi.moecube.com:444/ygopro/arena/users',
                                   params={'o': 'pt'}) as response:
                response.raise_for_status()
                return await response.json()

    async def players(self):
        async with self.lock:
            if self.cached is not None and time.monotonic() < self.expires:
                return list(self.cached)
            rows = await self.fetch()
            if not isinstance(rows, list) or len(rows) < 100:
                raise ValueError('前 100 名榜单不完整')
            players = [row.get('username') if isinstance(row, dict) else None for row in rows[:100]]
            if any(not isinstance(name, str) or not name.strip() for name in players) or len(set(players)) != 100:
                raise ValueError('榜单玩家数据无效')
            self.cached = players
            self.expires = time.monotonic() + self.ttl
            return list(players)


def deck_title(deck, rules):
    classification = classify(deck, rules)
    if not classification['labels']:
        return ''
    return ('60' if len(deck.main) == 60 else '') + classification['name']


async def player_image(player, version, rules_path):
    from hikari_bot.services.ygodeck import generate_deck_image

    def prepare():
        return version.deck.ydk(), deck_title(version.deck, query_rules(rules_path)) or '未识别'

    ydk, name = await asyncio.to_thread(prepare)
    img = await generate_deck_image(ydk, player, '', deck_name=name)
    if img is None:
        raise ValueError('卡组图片生成失败')

    def encode():
        try:
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            return buffer.getvalue()
        finally:
            img.close()

    return await asyncio.to_thread(encode)


def player_text(player, version, rank=None, *, links, rules=None):
    title = (f'{rank}. ' if rank is not None else '') + player
    if version is None:
        return title + '：暂无卡组'
    if rules is None:
        rules = query_rules(links.path.with_name('deck-rules.json'))
    name = deck_title(version.deck, rules)
    if name:
        title += '【' + name + '】'
    updated = datetime.fromtimestamp(version.updated_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
    link = links.url(player)
    return title + f'（更新：{updated}）\n' + link


async def top_texts(library, leaderboard):
    players = await leaderboard.players()
    def render():
        latest = library.latest_many(players)
        links = ShortLinks(library.path.with_name('short-links.sqlite3'))
        rules = query_rules(library.path.with_name('deck-rules.json'))
        return [player_text(player, latest.get(player), index, links=links, rules=rules)
                for index, player in enumerate(players, 1)]
    return await asyncio.to_thread(render)
