"""完整卡图的有界下载与语言回退；拒绝空白和未知卡占位图。"""
import asyncio
from collections import OrderedDict
import io
import time

import aiohttp
from PIL import Image
from hikari_bot.services.card_image_cache import valid_card_image

CHINESE = 'https://cdn.233.momobako.com/ygopro/pics/'
ENGLISH = 'https://images.ygoprodeck.com/images/cards/'
_cache = OrderedDict()
_image_tasks = {}
_chinese_tasks = {}
_retry_tasks = {}
_limit = asyncio.Semaphore(6)
_timeout = aiohttp.ClientTimeout(total=8, connect=3)
MAX_BYTES = 4 * 1024 * 1024
RETRY_DELAYS = (5, 30)
RETRY_INTERVAL = 300


def normalize_image(data):
    with Image.open(io.BytesIO(data)) as image:
        if not (100 <= image.width <= 5000 and 140 <= image.height <= 7000
                and .55 <= image.width / image.height <= .8):
            raise ValueError('不是完整卡面')
    valid_card_image(data)
    with Image.open(io.BytesIO(data)) as image:
        image.thumbnail((480, 700))
        out = io.BytesIO()
        image.convert('RGB').save(out, 'JPEG', quality=88)
        return out.getvalue()


async def download_image(session, url):
    async with session.get(url, timeout=_timeout) as response:
        if response.status != 200:
            return None
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(32768):
            chunks.extend(chunk)
            if len(chunks) > MAX_BYTES:
                raise ValueError('图片过大')
        return await asyncio.to_thread(normalize_image, bytes(chunks))


async def fetch_chinese_image(code, info, *, download=download_image):
    async with aiohttp.ClientSession() as session:
        for candidate in dict.fromkeys((code, info.get('id') or code)):
            try:
                image = await download(session, f'{CHINESE}{candidate}.jpg')
                if image:
                    return image
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                pass
    return None


async def fetch_card_image(code, info, *, download=download_image):
    """备用源只按卡号或精确英文名解析，不使用模糊名称猜测卡图。"""
    image = await fetch_chinese_image(code, info, download=download)
    if image:
        return image, 'zh'
    async with aiohttp.ClientSession() as session:
        async def attempt(url):
            try:
                return await download(session, url)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                return None
        image = await attempt(f'{ENGLISH}{code}.jpg')
        if image:
            return image, 'en'
        # 临时卡密和异画编号不能随意换成另一张卡；只使用已确认的身份。
        canonical = info.get('id')
        if canonical and canonical != code:
            image = await attempt(f'{ENGLISH}{canonical}.jpg')
            if image:
                return image, 'en'
        name = info.get('en_name') or info.get('wiki_en')
        if code >= 100000000 and name:
            try:
                async with session.get('https://db.ygoprodeck.com/api/v7/cardinfo.php',
                                       params={'name': name}, timeout=_timeout) as response:
                    if response.status == 200 and (response.content_length or 0) <= 131072:
                        raw = await response.content.read(131073)
                        if len(raw) > 131072:
                            raise ValueError('响应过大')
                        import json
                        cards = json.loads(raw).get('data', [])
                        if len(cards) == 1 and cards[0].get('name') == name:
                            resolved = cards[0].get('id')
                            if type(resolved) is int and 0 < resolved < 100000000:
                                image = await attempt(f'{ENGLISH}{resolved}.jpg')
                                if image:
                                    return image, 'en'
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
                pass
    return None, None


def remember(code, result, refresh):
    _cache[code] = (time.monotonic() + (3600 if result[0] else 60), result, refresh)
    _cache.move_to_end(code)
    while len(_cache) > 256:
        evicted, _ = _cache.popitem(last=False)
        if task := _retry_tasks.get(evicted):
            task.cancel()


async def single_flight(tasks, code, factory):
    task = tasks.get(code)
    if task is None:
        task = asyncio.create_task(factory())
        tasks[code] = task
        task.add_done_callback(lambda done: tasks.pop(code, None))
    return await asyncio.shield(task)


async def retry_chinese(code, info):
    async def download():
        async with _limit:
            return await fetch_chinese_image(code, info)
    return await single_flight(_chinese_tasks, code, download)


async def recover_chinese(code, info):
    # 英文仅作兜底。先较快补试两次，之后每五分钟检查；闲置过期或淘汰后停止。
    delays = iter(RETRY_DELAYS)
    while True:
        await asyncio.sleep(next(delays, RETRY_INTERVAL))
        cached = _cache.get(code)
        if not cached or cached[0] <= time.monotonic() or cached[1][1] == 'zh':
            return
        image = await retry_chinese(code, info)
        current = _cache.get(code)
        if not current:
            return
        if image:
            remember(code, (image, 'zh'), current[2])
            return


def schedule_recovery(code, info):
    if code not in _retry_tasks:
        task = asyncio.create_task(recover_chinese(code, info))
        _retry_tasks[code] = task
        task.add_done_callback(lambda done: _retry_tasks.pop(code, None))


async def full_card_image(code, info, refresh=''):
    async def load():
        cached = _cache.get(code)
        if cached and cached[0] > time.monotonic():
            if not refresh or refresh == cached[2] or cached[1][1] == 'zh':
                _cache.move_to_end(code)
                if cached[1][1] == 'en':
                    schedule_recovery(code, info)
                return cached[1]
        if cached and cached[1][1] == 'en':
            image = await retry_chinese(code, info)
            result = (image, 'zh') if image else cached[1]
        else:
            async with _limit:
                result = await fetch_card_image(code, info)
        remember(code, result, refresh)
        if result[1] == 'en':
            schedule_recovery(code, info)
        return result
    return await single_flight(_image_tasks, code, load)
