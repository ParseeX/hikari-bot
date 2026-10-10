"""名额空出时重算等待对局的优先级，不中断已经获准采集的对局。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from nonebot import logger


@dataclass
class WaitingRoom:
    state: object
    ended: asyncio.Event
    ready: asyncio.Future
    sequence: int


class PrioritySlots:
    def __init__(self, concurrency, priorities):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.priorities = priorities
        self.waiting = []
        self.sequence = 0
        self.dispatcher = None

    async def dispatch(self):
        while self.waiting:
            await self.semaphore.acquire()
            assigned = False
            try:
                candidates = [item for item in self.waiting
                              if not item.ended.is_set() and not item.ready.done()]
                if not candidates:
                    return
                keys = await self.priorities([item.state for item in candidates])
                ordered = sorted(zip(keys, candidates), key=lambda pair: (pair[0], pair[1].sequence))
                for key, item in ordered:
                    if item in self.waiting and not item.ended.is_set() and not item.ready.done():
                        self.waiting.remove(item)
                        item.ready.set_result(True)
                        assigned = True
                        logger.info(f'[mycard_archive] 队列入场：priority={key}，候选 {len(candidates)} 场')
                        break
            except Exception as exc:
                # 查询失败不能丢弃排队项，也不能凭空占住名额。
                logger.warning(f'[mycard_archive] 队列排序失败：{type(exc).__name__}')
                await asyncio.sleep(1)
            finally:
                if not assigned:
                    self.semaphore.release()

    @asynccontextmanager
    async def slot(self, ended, state=None):
        ready = asyncio.get_running_loop().create_future()
        item = WaitingRoom(state, ended, ready, self.sequence)
        self.sequence += 1
        self.waiting.append(item)
        if self.dispatcher is None or self.dispatcher.done():
            self.dispatcher = asyncio.create_task(self.dispatch(), name='mycard-priority-dispatch')
        finish = asyncio.create_task(ended.wait())
        try:
            await asyncio.wait((ready, finish), return_when=asyncio.FIRST_COMPLETED)
            yield ready.done() and not ready.cancelled() and not ended.is_set()
        finally:
            finish.cancel()
            await asyncio.gather(finish, return_exceptions=True)
            if item in self.waiting:
                self.waiting.remove(item)
            if ready.done() and not ready.cancelled():
                self.semaphore.release()
            else:
                ready.cancel()

    async def close(self):
        if self.dispatcher:
            self.dispatcher.cancel()
            await asyncio.gather(self.dispatcher, return_exceptions=True)
            self.dispatcher = None
