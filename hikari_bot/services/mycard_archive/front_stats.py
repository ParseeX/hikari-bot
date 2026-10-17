"""每十五分钟刷新官方榜单与历史；访客请求只读取持久快照。"""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time
import math
import random
from zoneinfo import ZoneInfo
import aiohttp
from nonebot import logger
from hikari_bot.services import mycard
from hikari_bot.services.mycard_history import history_summary, month_key
from .queries import Leaderboard
from .matchups import compact_history

def official_stats(data):
    result = {'rank': None, 'score': None, 'month_games': None, 'month_win_rate': None}
    if not isinstance(data, dict):
        return result
    rank = data.get('arena_rank')
    if isinstance(rank, str) and rank.isascii() and rank.isdecimal():
        rank = int(rank)
    # 官方未找到用户时返回 rank=0 和默认 PT=1000，不能当作真实统计。
    if type(rank) is not int or rank < 1:
        return result
    result['rank'] = rank
    score = data.get('pt')
    if type(score) in (int, float) and math.isfinite(score):
        result['score'] = score
    # athletic_all / athletic_wl_ratio 没有月份标识；不猜测其重置口径。
    return result


class FrontStats:
    def __init__(self, path=None, *, leaderboard=None, fetch=None, history_fetch=None, previous_fetch=None,
                 interval=900, timeout=8, clock=time.time, request_pause=0.1,
                 retry_base=5, retry_max=300, on_refreshed=None):
        self.path = Path(path) if path else None
        self.leaderboard = leaderboard or Leaderboard()
        self.fetch = fetch or mycard.fetch_player_info
        self.history_fetch = history_fetch or mycard.fetch_player_history_page
        self.previous_fetch = previous_fetch or mycard.fetch_previous_month_score
        self.interval, self.timeout, self.clock = interval, timeout, clock
        self.request_pause = request_pause
        self.retry_base, self.retry_max = retry_base, retry_max
        self.on_refreshed = on_refreshed
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(2)
        self.task = None
        self.state = {'version': 1, 'updated_at': None, 'players': [], 'stats': {}}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text('utf-8'))
                self._validate(data)
                self.state = data
            except (OSError, ValueError, TypeError, KeyError):
                logger.warning('[mc_front_cache] 缓存不可读，等待后台刷新')

    @staticmethod
    def _validate(data):
        if data.get('version') != 1 or not isinstance(data.get('stats'), dict):
            raise ValueError('缓存格式无效')
        players = data.get('players')
        if not isinstance(players, list) or len(players) != 100 or any(not isinstance(p, str) or not p.strip() for p in players) or len(set(players)) != 100:
            raise ValueError('缓存榜单无效')
        for player in players:
            row = data['stats'][player]
            if not isinstance(row, dict) or not isinstance(row.get('trend', []), list):
                raise ValueError('缓存玩家无效')
        json.dumps(data, allow_nan=False)

    def snapshot(self):
        result = deepcopy(self.state)
        month = month_key(self.clock())
        for row in result['stats'].values():
            if row.get('month') != month:
                row.update(month_games=None, month_win_rate=None, trend=[])
        return result

    def _save(self, data):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(self.path)

    async def refresh(self):
        if self.lock.locked():
            return False
        async with self.lock:
            delay = self.retry_base
            while True:
                try:
                    players = await asyncio.wait_for(self.leaderboard.players(), timeout=20)
                    break
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError):
                    logger.warning('[mc_front_cache] 榜单失败，退避后重试')
                    await self._backoff(delay)
                    delay = min(self.retry_max, delay * 2)
            pending = {player: {'info', 'history'} for player in players}
            round_month = month_key(self.clock())
            delay = self.retry_base

            async def one(player, fields, old):
                row = old.get(player, {**official_stats(None), 'trend': []})
                failed = set()
                async with self.semaphore:
                    if 'info' in fields:
                        try:
                            data = official_stats(await asyncio.wait_for(self.fetch(player), self.timeout))
                            if data['rank'] is None or data['score'] is None:
                                raise ValueError('当前排名或分数缺失')
                            row.update(rank=data['rank'], score=data['score'], stats_updated_at=self.clock())
                        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError):
                            failed.add('info')
                    if 'history' in fields:
                        try:
                            payload = await asyncio.wait_for(self.history_fetch(player), self.timeout)
                            sampled_at = self.clock()
                            year, month = map(int, month_key(sampled_at).split('-'))
                            previous = None
                            if month not in (1, 4, 7, 10):
                                previous = await asyncio.wait_for(self.previous_fetch(player, year, month), self.timeout)
                            summary = history_summary(payload, player, sampled_at, previous)
                            if not summary['history_complete']:
                                raise ValueError('本月历史覆盖不足')
                            row.update(summary, history_updated_at=self.clock())
                            row['_matches'] = compact_history(payload['data'], players, summary['month'])
                        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError):
                            failed.add('history')
                    await asyncio.sleep(self.request_pause)
                return player, row, failed

            while pending:
                current_month = month_key(self.clock())
                if current_month != round_month:
                    # 重试跨月后重新采集全榜历史，不把上月成功状态带入本月。
                    for player in players:
                        pending.setdefault(player, set()).add('history')
                    round_month = current_month
                old = self.snapshot()['stats']
                rows = await asyncio.gather(*(one(player, fields, old) for player, fields in pending.items()))
                old.update({player: row for player, row, _ in rows})
                pending = {player: failed for player, _, failed in rows if failed}
                state = {'version': 1, 'updated_at': self.clock(), 'players': players,
                         'stats': {player: old[player] for player in players},
                         'pending_count': sum(len(fields) for fields in pending.values())}
                self._validate(state)
                await asyncio.to_thread(self._save, state)
                self.state = state
                if pending:
                    logger.warning('[mc_front_cache] 已保存成功项，剩余 {} 项，退避后重试', state['pending_count'])
                    # 等待不占请求并发槽；整轮锁仍持有，定时轮次不会重叠。
                    await self._backoff(delay)
                    delay = min(self.retry_max, delay * 2)
            logger.info('[mc_front_cache] 刷新完成：{} 人，失败 0 项', len(players))
            if self.on_refreshed:
                self.on_refreshed()
            return True

    async def _backoff(self, delay):
        # 5、10、20…秒，最多五分钟；少量抖动避免上游恢复时集中重试。
        await asyncio.sleep(min(self.retry_max, delay * random.uniform(1, 1.2)))

    async def _wait_next_slot(self):
        # Unix 时间的 900 秒边界同样对应北京时间每小时的 00、15、30、45 分。
        target = (math.floor(self.clock() / self.interval) + 1) * self.interval
        logger.info('[mc_front_cache] 下次定时刷新：{}', datetime.fromtimestamp(target, ZoneInfo('Asia/Shanghai')).isoformat())
        while True:
            remaining = target - self.clock()
            if remaining <= 0:
                return
            # 定期重看墙上时钟，避免系统校时后仍等待完整的旧间隔。
            await asyncio.sleep(min(60, remaining))

    async def _run(self):
        updated = self.state.get('updated_at')
        if self.state.get('pending_count'):
            await self._backoff(self.retry_base)
        elif isinstance(updated, (int, float)) and all('_matches' in row for row in self.state['stats'].values()):
            # 已有快照时等下一个整刻；空缓存和中断的重试优先恢复数据。
            await self._wait_next_slot()
        while True:
            try:
                logger.info('[mc_front_cache] 开始刷新')
                await self.refresh()
            except Exception as exc:
                logger.warning('[mc_front_cache] 刷新失败，保留快照：{}', type(exc).__name__)
                await self._backoff(self.retry_base)
                continue
            # 过点仍在采集/重试时不叠加任务；完成后等待下一个整刻，不补跑。
            await self._wait_next_slot()

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
