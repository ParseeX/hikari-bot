"""共享握手限速和失败冷却；已建立的观战不持有入场锁。"""
import asyncio
from contextlib import asynccontextmanager
import time

from nonebot import logger


class JoinGate:
    def __init__(self, interval=0.5, cooldown=300, max_cooldown=1800):
        self.lock = asyncio.Lock()
        self.interval = interval
        self.cooldown = cooldown
        self.max_cooldown = max_cooldown
        self.failures = 0
        self.next_at = 0.0

    @property
    def remaining(self):
        return max(0.0, self.next_at - time.monotonic())

    @asynccontextmanager
    async def attempt(self, ended):
        acquire = asyncio.create_task(self.lock.acquire())
        finish = asyncio.create_task(ended.wait())
        released = False
        started = False
        accepted = False
        cancelled = False

        def connected():
            nonlocal released, accepted
            if accepted:
                return
            accepted = True
            self.failures = 0
            self.next_at = time.monotonic() + self.interval
            self.lock.release()
            released = True

        try:
            await asyncio.wait((acquire, finish), return_when=asyncio.FIRST_COMPLETED)
            if ended.is_set():
                yield None
                return
            try:
                await asyncio.wait_for(ended.wait(), self.remaining)
            except asyncio.TimeoutError:
                pass
            if ended.is_set():
                yield None
                return
            started = True
            yield connected
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            acquire.cancel()
            finish.cancel()
            await asyncio.gather(acquire, finish, return_exceptions=True)
            if not released and not acquire.cancelled() and acquire.exception() is None and acquire.result():
                if started and not accepted and not cancelled and not ended.is_set():
                    self.failures += 1
                    delay = self.interval
                    if self.failures >= 3:
                        delay = min(self.cooldown * 2 ** min(self.failures - 3, 10), self.max_cooldown)
                        logger.warning(f"[mycard_archive] 连续 {self.failures} 次入场失败，全局冷却 {delay} 秒")
                    self.next_at = time.monotonic() + delay
                self.lock.release()
