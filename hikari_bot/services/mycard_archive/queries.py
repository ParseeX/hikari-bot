"""卡组查询文本和当前竞技场前 100 名快照。"""
import asyncio
from datetime import datetime
import time
from zoneinfo import ZoneInfo

import aiohttp

from .short_links import ShortLinks


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


def player_text(player, version, rank=None, *, links):
    title = (f'{rank}. ' if rank is not None else '') + player
    if version is None:
        return title + '：暂无卡组'
    updated = datetime.fromtimestamp(version.updated_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
    link = links.url(player)
    return title + f'（更新：{updated}）\n' + link


async def top_texts(library, leaderboard):
    players = await leaderboard.players()
    def render():
        latest = library.latest_many(players)
        links = ShortLinks(library.path.with_name('short-links.sqlite3'))
        return [player_text(player, latest.get(player), index, links=links)
                for index, player in enumerate(players, 1)]
    return await asyncio.to_thread(render)
