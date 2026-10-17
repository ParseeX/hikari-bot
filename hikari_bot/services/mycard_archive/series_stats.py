"""前排卡组与系列统计的后台预计算；请求只读取已序列化的快照。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time

from nonebot import logger
from hikari_bot.services.mycard_history import month_key


class SeriesStats:
    def __init__(self, path, build, *, clock=time.time, retry_base=5, retry_max=300):
        self.path = Path(path)
        self.build, self.clock = build, clock
        self.retry_base, self.retry_max = retry_base, retry_max
        self.event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task = None
        self.month = None
        self.content = self.expired_content = None
        self.pages = self.expired_pages = ()
        self.card_content = None
        self.match_content = None
        self.match_month = None
        self.card_codes = frozenset()
        try:
            saved = json.loads(self.path.read_text('utf-8'))
            self._install(saved)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    @staticmethod
    def _encode(data):
        return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')

    def _prepare(self, saved):
        payload = saved['payload']
        if (saved['version'] != 1 or not isinstance(saved['month'], str)
                or not isinstance(payload['series'], list) or not isinstance(payload['players'], list)
                or len(payload['players']) != 100 or any(not isinstance(row, dict) for row in payload['players'])
                or payload['total'] != 100):
            raise ValueError('系列缓存格式无效')
        # 同一份玩家资料生成两个页面；前排分页保留榜单顺序，系列详情按排名排序。
        by_player = {row['player']: row for row in payload['players']}
        order = payload.get('player_order', list(by_player))
        if len(by_player) != 100 or len(order) != 100 or set(order) != set(by_player):
            raise ValueError('分页玩家列表无效')
        public = {key: value for key, value in payload.items() if key not in ('player_order', 'card_stats', 'match_stats')}
        content = self._encode(public)
        expired = deepcopy(public)
        for row in expired['players']:
            row.update(month_games=None, month_win_rate=None, trend=[])
        def pages(data):
            lookup = {row['player']: row for row in data['players']}
            ordered = [lookup[player] for player in order]
            return tuple(self._encode({'page': index + 1, 'page_size': 10, 'total': 100,
                                       'players': ordered[index * 10:(index + 1) * 10],
                                       'updated_at': data['updated_at']}) for index in range(10))
        card_stats = payload.get('card_stats')
        card_content, card_codes = None, frozenset()
        if card_stats is not None:
            if (not isinstance(card_stats['cards'], list) or card_stats['total'] != 100
                    or card_stats['updated_at'] != payload['updated_at']):
                raise ValueError('卡片统计缓存无效')
            card_content = self._encode(card_stats)
            card_codes = frozenset(card['code'] for card in card_stats['cards'])
        match_stats = payload.get('match_stats')
        match_content = None
        if match_stats is not None:
            if (not isinstance(match_stats['pairs'], list) or match_stats['total'] != 100
                    or match_stats['updated_at'] > payload['updated_at']):
                raise ValueError('系列胜率缓存无效')
            match_content = self._encode(match_stats)
        return (saved['month'], content, self._encode(expired), pages(public), pages(expired),
                card_content, card_codes, match_content, match_stats['month'] if match_stats else None)

    def _install(self, saved):
        self._publish(self._prepare(saved))

    def _publish(self, prepared):
        (self.month, self.content, self.expired_content, self.pages, self.expired_pages,
         self.card_content, self.card_codes, self.match_content, self.match_month) = prepared

    def read(self):
        # 跨月刷新未结束时隐藏旧月字段，不能将上月战绩当成本月数据。
        return self.content if self.month == month_key(self.clock()) else self.expired_content

    def read_page(self, page):
        pages = self.pages if self.month == month_key(self.clock()) else self.expired_pages
        return pages[page - 1] if 1 <= page <= len(pages) else None

    def read_cards(self):
        return self.card_content

    def read_matchups(self):
        # 月份随胜率内容保存，跨月时不显示上月数字或伪造零场。
        if self.match_month == month_key(self.clock()):
            return self.match_content
        return None

    def has_card(self, code):
        return code in self.card_codes

    def request_refresh(self):
        self.event.set()

    def _build_and_save(self):
        saved = {'version': 1, 'month': month_key(self.clock()), 'payload': self.build()}
        prepared = self._prepare(saved)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_bytes(self._encode(saved))
        temporary.chmod(0o600)
        temporary.replace(self.path)
        return prepared

    async def refresh(self):
        async with self.lock:
            prepared = await asyncio.to_thread(self._build_and_save)
            # 完整计算和落盘成功后才整体替换，失败不会影响在用的旧结果。
            self._publish(prepared)

    async def _run(self):
        delay = self.retry_base
        while True:
            try:
                await asyncio.wait_for(self.event.wait(), timeout=60)
            except TimeoutError:
                if self.month == month_key(self.clock()):
                    continue
            self.event.clear()
            try:
                await self.refresh()
                delay = self.retry_base
                logger.info('[mc_series_cache] 前排卡组、系列统计、卡片统计及系列胜率已更新')
            except Exception as exc:
                logger.warning('[mc_series_cache] 生成失败，保留旧结果并重试：{}', type(exc).__name__)
                try:
                    await asyncio.wait_for(self.event.wait(), timeout=delay)
                except TimeoutError:
                    pass
                self.event.set()
                delay = min(self.retry_max, delay * 2)

    def start(self):
        if self.task is None or self.task.done():
            self.request_refresh()
            self.task = asyncio.create_task(self._run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
