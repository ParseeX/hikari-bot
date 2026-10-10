"""按权重分配名额；抢占先通知旧连接退出，释放后才能分配新连接。"""
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
    preempting: bool = False


class NotifyingSemaphore(asyncio.Semaphore):
    def __init__(self, value, changed):
        super().__init__(value)
        self.changed = changed

    def release(self):
        super().release()
        self.changed.set()


class PrioritySlots:
    def __init__(self, concurrency, priorities, preempt=None, refresh_interval=5):
        self.changed = asyncio.Event()
        self.semaphore = NotifyingSemaphore(concurrency, self.changed)
        self.priorities = priorities
        self.preempt = preempt
        self.refresh_interval = refresh_interval
        self.waiting = []
        self.active = []
        self.sequence = 0
        self.dispatcher = None

    async def dispatch(self):
        while self.waiting:
            self.changed.clear()
            try:
                candidates = [item for item in self.waiting
                              if not item.ended.is_set() and not item.ready.done()]
                if not candidates:
                    return
                active = list(self.active) if self.preempt else []
                keys = await self.priorities([item.state for item in candidates + active])
                ordered = sorted(zip(keys[:len(candidates)], candidates),
                                 key=lambda pair: (pair[0], pair[1].sequence))
                ordered = [(key,item) for key,item in ordered if item in self.waiting
                           and not item.ended.is_set() and not item.ready.done()]
                if not ordered:
                    continue
                key, item = ordered[0]
                if not self.semaphore.locked():
                    await self.semaphore.acquire()
                    self.waiting.remove(item)
                    self.active.append(item)
                    item.ready.set_result(True)
                    logger.info(f'[mycard_archive] 队列入场：priority={key}，候选 {len(ordered)} 场')
                    continue
                # 同时只发起一次抢占，等连接关闭并释放名额后重新计算。
                if self.preempt and not any(room.preempting for room in self.active):
                    victims = [(score,room) for score,room in zip(keys[len(candidates):],active)
                               if room in self.active and not room.ended.is_set()]
                    if victims:
                        score, victim = max(victims, key=lambda pair: (pair[0],pair[1].sequence))
                        if key[0] < score[0]:
                            victim.preempting = True
                            self.preempt(victim.state)
                            logger.info(f'[mycard_archive] 权重抢占：{-score[0]} → {-key[0]}')
            except Exception as exc:
                logger.warning(f'[mycard_archive] 队列排序失败：{type(exc).__name__}')
                await asyncio.sleep(1)
            try:
                await asyncio.wait_for(self.changed.wait(), self.refresh_interval)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def slot(self, ended, state=None):
        ready = asyncio.get_running_loop().create_future()
        item = WaitingRoom(state, ended, ready, self.sequence)
        self.sequence += 1
        self.waiting.append(item)
        self.changed.set()
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
            if item in self.active:
                self.active.remove(item)
            if ready.done() and not ready.cancelled():
                self.semaphore.release()
            else:
                ready.cancel()
            self.changed.set()

    async def close(self):
        if self.dispatcher:
            self.dispatcher.cancel()
            await asyncio.gather(self.dispatcher, return_exceptions=True)
            self.dispatcher = None
