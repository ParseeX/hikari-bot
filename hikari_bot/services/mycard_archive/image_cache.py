"""提前缓存已归档卡组的单卡图片，队列可从卡组库和磁盘缓存重建。"""
import asyncio
from pathlib import Path
import time

from nonebot import logger
from PIL import Image

from hikari_bot.services.ygocard import CARD_PICS, get_ygopic


class CardImageCache:
    def __init__(self, library, *, directory=None, download=None, scan_interval=30,
                 download_interval=0.25, retry_interval=300, clock=time.monotonic):
        self.library = library
        self.directory = Path(directory or CARD_PICS)
        self.download = download or get_ygopic
        self.scan_interval = scan_interval
        self.download_interval = download_interval
        self.retry_interval = retry_interval
        self.clock = clock
        self.queue = asyncio.Queue()
        self.pending = set()
        self.retries = {}
        self.validated = {}
        self.tasks = []
        self.downloaded = 0
        self.last_status = None

    def cached(self, code):
        path = self.directory / f'{code}.jpg'
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self.validated.get(code) == signature:
                return True
            with Image.open(path) as image:
                image.load()
            self.validated[code] = signature
            return True
        except (OSError, ValueError):
            self.validated.pop(code, None)
            return False

    def inventory(self):
        if not self.library.path.is_file():
            return [], []
        cards = self.library.card_ids()
        return cards, [code for code in cards if not self.cached(code)]

    async def scan_once(self):
        cards, missing = await asyncio.to_thread(self.inventory)
        now = self.clock()
        for code in missing:
            if code not in self.pending and self.retries.get(code, (0, 0))[1] <= now:
                self.pending.add(code)
                self.queue.put_nowait(code)
        status = (len(cards), len(cards) - len(missing), len(self.pending))
        if status != self.last_status:
            logger.info(f'[mycard_image_cache] 卡片 {status[0]}，已缓存 {status[1]}，待下载 {status[2]}')
            self.last_status = status
        return status

    async def scan(self):
        while True:
            try:
                await self.scan_once()
            except Exception as exc:
                logger.warning(f'[mycard_image_cache] 扫描失败：{type(exc).__name__}')
            await asyncio.sleep(self.scan_interval)

    async def worker(self):
        while True:
            code = await self.queue.get()
            try:
                # 排队期间查询可能已经下载过；不重复发起请求。
                if not await asyncio.to_thread(self.cached, code):
                    await self.download(code)
                    if not await asyncio.to_thread(self.cached, code):
                        # 备用语言和占位图不算中文卡图缓存成功。
                        raise ValueError('中文卡图未缓存')
                    self.downloaded += 1
                self.retries.pop(code, None)
            except Exception as exc:
                attempts = self.retries.get(code, (0, 0))[0] + 1
                delay = min(3600, self.retry_interval * 2 ** min(attempts - 1, 4))
                self.retries[code] = (attempts, self.clock() + delay)
                logger.warning(f'[mycard_image_cache] 卡图 {code} 延后重试：{type(exc).__name__}')
            finally:
                self.pending.discard(code)
                self.queue.task_done()
            await asyncio.sleep(self.download_interval)

    def start(self):
        if self.tasks:
            return
        self.tasks = [asyncio.create_task(self.scan(), name='mycard-image-cache-scan')]
        self.tasks.extend(asyncio.create_task(self.worker(), name=f'mycard-image-cache-{i}') for i in range(2))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.pending.clear()
