"""固定日期候选链：缺图继续下一张，记住当天第一次可用的结果。"""
import asyncio
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

from .card_media import image_trace


class DailyCards:
    LIMIT = 5

    def __init__(self, catalog, image_loader, *, path=None, card_timeout=18, total_timeout=90):
        self.catalog, self.image_loader = catalog, image_loader
        self.path = Path(path) if path is not None else None
        self.card_timeout, self.total_timeout = card_timeout, total_timeout
        self.memory = OrderedDict()

    def _read(self, day, actor):
        if self.path is None:
            return self.memory.get((day, actor))
        if not self.path.exists():
            return None
        with sqlite3.connect(self.path, timeout=2) as db:
            return db.execute('SELECT position,code FROM daily_cards WHERE day=? AND actor=?',
                              (day, actor)).fetchone()

    def _save(self, day, actor, position, code):
        if self.path is None:
            self.memory[(day, actor)] = (position, code)
            while len(self.memory) > 4096:
                self.memory.popitem(last=False)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute('CREATE TABLE IF NOT EXISTS daily_cards ('
                       'day TEXT, actor TEXT, position INTEGER, code INTEGER, PRIMARY KEY(day,actor))')
            db.execute('INSERT INTO daily_cards VALUES (?,?,?,?) '
                       'ON CONFLICT(day,actor) DO UPDATE SET position=excluded.position,code=excluded.code',
                       (day, actor, position, code))

    async def draw(self, request, *, day=None):
        day = day or datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d')
        seed_key = f'{request.actor.key}_{day}'
        actor = hashlib.sha256(request.actor.key.encode()).hexdigest()
        trace = hashlib.sha256((seed_key + request.event_id).encode()).hexdigest()[:16]

        def report(stage, **fields):
            print('SRBS_DAILY_CARD ' + json.dumps(
                {'trace': trace, 'day': day, 'stage': stage, **fields}, sort_keys=True), flush=True)

        saved = None
        try:
            saved = await asyncio.to_thread(self._read, day, actor)
        except (OSError, sqlite3.Error) as error:
            report('cache_read_failed', error=type(error).__name__)
        deadline = time.monotonic() + self.total_timeout
        seen = set()
        token = image_trace.set(trace)
        try:
            for position in range(saved[0] if saved else 0, self.LIMIT):
                if saved and position == saved[0]:
                    code = saved[1]
                else:
                    # 第一张与旧实现完全一致；其余候选使用带序号的固定种子。
                    key = seed_key if position == 0 else f'{seed_key}#fallback:{position}'
                    seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big')
                    code = await asyncio.to_thread(self.catalog.random_id, seed)
                if code in seen:
                    report('duplicate', position=position + 1, code=code)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                seen.add(code)
                report('attempt', position=position + 1, code=code)
                try:
                    data = await asyncio.wait_for(self.image_loader(code), min(self.card_timeout, remaining))
                except TimeoutError:
                    report('timeout', position=position + 1, code=code)
                    continue
                except Exception as error:
                    # 不输出可能含 URL、鉴权或正文的异常文本。
                    report('image_error', position=position + 1, code=code, error=type(error).__name__)
                    continue
                if not data:
                    report('missing', position=position + 1, code=code)
                    continue
                try:
                    await asyncio.to_thread(self._save, day, actor, position, code)
                except (OSError, sqlite3.Error) as error:
                    report('cache_write_failed', error=type(error).__name__)
                report('selected', position=position + 1, code=code, bytes=len(data))
                return data
            report('exhausted', tried=len(seen))
            return None
        finally:
            image_trace.reset(token)
