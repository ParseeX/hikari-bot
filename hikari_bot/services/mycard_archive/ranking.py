"""按官方当前竞技场名次筛选，避免将仅含前 100 的榜单误用为前 500。"""
import asyncio
import time

import aiohttp


class RankFilter:
    def __init__(self, cutoff=500, ttl=300, fetch_rank=None):
        self.cutoff = cutoff
        self.ttl = ttl
        self.fetch_rank = fetch_rank or self.fetch
        self.cache = {}
        self.semaphore = asyncio.Semaphore(4)

    async def fetch(self, username):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get('https://sapi.moecube.com:444/ygopro/arena/user',
                                   params={'username': username}) as response:
                response.raise_for_status()
                data = await response.json()
        rank = data.get('arena_rank') if isinstance(data, dict) else None
        if isinstance(rank, str) and rank.isdecimal():
            rank = int(rank)
        if type(rank) is not int or rank < 1:
            raise ValueError('无法确认官方竞技场名次')
        return rank

    async def rank(self, username):
        async with self.semaphore:
            now = time.monotonic()
            cached = self.cache.get(username)
            if cached and now - cached[1] < self.ttl:
                return cached[0]
            rank = await self.fetch_rank(username)
            self.cache[username] = (rank, time.monotonic())
            # 仅保留近期缓存，避免持续运行时按历史玩家无限增长。
            if len(self.cache) > 5000:
                self.cache = {name: value for name, value in self.cache.items() if now-value[1] < self.ttl}
            return rank

    async def eligible(self, players):
        errors = []
        for player in players:
            try:
                if await self.rank(player) <= self.cutoff:
                    return True
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ValueError('无法确认双方名次') from None
        return False
