"""读取网页同一份公开榜单快照，不触发抓取或写入玩家数据。"""
import asyncio
from dataclasses import dataclass
import time
from urllib.parse import quote

import httpx

WEB_URL = 'https://ygo.xyk.one/mc'


@dataclass(frozen=True)
class FrontDeck:
    rank: int
    player: str
    deck_name: str | None
    url: str


class FrontDecks:
    def __init__(self, fetch=None, *, clock=time.monotonic, ttl=30):
        self.fetch, self.clock, self.ttl = fetch or self._fetch, clock, ttl
        self.cached, self.expires = None, 0
        self.lock = asyncio.Lock()

    async def _fetch(self):
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(WEB_URL + '/api/deck-stats')
            response.raise_for_status()
            return response.json()

    async def top(self):
        async with self.lock:
            if self.cached is not None and self.clock() < self.expires:
                return self.cached
            data = await asyncio.wait_for(self.fetch(), timeout=18)
            rows, order = data['players'], data['ranking_order']
            if not isinstance(rows, list) or not isinstance(order, list) or len(order) < 20:
                raise ValueError('榜单快照不完整')
            if any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in order):
                raise ValueError('榜单玩家无效')
            lookup = {row['player']: row for row in rows}
            if len(lookup) != len(rows) or len(set(order)) != len(order) or set(order) != set(lookup):
                raise ValueError('榜单快照顺序不一致')
            result = []
            for rank, player in enumerate(order[:20], 1):
                deck = lookup[player]['deck']
                if deck is not None and not isinstance(deck, dict):
                    raise ValueError('卡组信息无效')
                name = (deck.get('name') or '') if deck is not None else None
                if name is not None and not isinstance(name, str):
                    raise ValueError('卡组名称无效')
                result.append(FrontDeck(rank, player, name, WEB_URL+'/player/?name='+quote(player, safe='')))
            self.cached, self.expires = tuple(result), self.clock() + self.ttl
            return self.cached
