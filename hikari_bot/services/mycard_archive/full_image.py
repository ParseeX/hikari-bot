"""完整卡图的有界下载与语言回退；拒绝空白和未知卡占位图。"""
import asyncio
from collections import OrderedDict
import io
import json
import logging
import threading
from pathlib import Path
import time

import aiohttp
from PIL import Image
from hikari_bot.core.constants import DATA_DIR
from hikari_bot.services.card_image_cache import valid_card_image, read_card_image, write_card_image

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
RETRY_INTERVAL = 86400
IMAGE_DIR = Path(DATA_DIR) / "pics"
_disk_cache = OrderedDict()
_disk_lock = threading.Lock()


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


async def fetch_card_image(code, info, *, download=download_image, try_chinese=True):
    """备用源只按卡号或精确英文名解析，不使用模糊名称猜测卡图。"""
    image = await fetch_chinese_image(code, info, download=download) if try_chinese else None
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


def disk_image(path):
    """内存只作加速；淘汰内存条目不会删除或过期磁盘图片。"""
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with _disk_lock:
            cached = _disk_cache.get(path)
            if cached and cached[0] == signature:
                _disk_cache.move_to_end(path)
                return cached[1]
        data = read_card_image(path)
        if data:
            with _disk_lock:
                _disk_cache[path] = (signature, data)
                while len(_disk_cache) > 256:
                    _disk_cache.popitem(last=False)
        return data
    except OSError:
        return None


def resize_thumbnail(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.width <= 200 and image.height <= 290:
            return data
        image.thumbnail((200, 290))
        out = io.BytesIO()
        image.convert('RGB').save(out, 'JPEG', quality=85)
        return out.getvalue()


def load_disk_image(code, size='full', *, chinese_only=False):
    for language in ('zh',) if chinese_only else ('zh', 'en'):
        sub = Path() if language == 'zh' else Path('fallback')
        if size == 'thumb':
            candidates = [IMAGE_DIR / 'web-thumbs' / sub / f'{code}.jpg',
                          IMAGE_DIR / sub / f'{code}.jpg',
                          IMAGE_DIR / 'full' / sub / f'{code}.jpg']
        else:
            candidates = [IMAGE_DIR / 'full' / sub / f'{code}.jpg']
        for path in candidates:
            data = disk_image(path)
            if not data:
                continue
            if size == 'thumb':
                small = resize_thumbnail(data)
                if small != data:
                    write_card_image(IMAGE_DIR / 'web-thumbs' / sub / f'{code}.jpg', small)
                data = small
            return data, language
    return None, None


def state_path(code):
    return IMAGE_DIR / 'web-recovery' / f'{code}.json'


def read_state(code):
    try:
        state = json.loads(state_path(code).read_text())
        if isinstance(state.get('attempts'), int) and isinstance(state.get('next_check'), (int, float)):
            return state
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def write_state(code, state):
    # 与卡图一样采用原子写入，避免重启留下半份检查进度。
    write_card_image(state_path(code), json.dumps(state).encode())


def record_failure(code, info):
    previous = read_state(code) or {}
    attempts = min(3, previous.get('attempts', 0) + 1)
    delay = RETRY_DELAYS[attempts - 1] if attempts <= len(RETRY_DELAYS) else RETRY_INTERVAL
    state = {'attempts': attempts, 'next_check': time.time() + delay,
             'info': {key: info[key] for key in ('id', 'en_name', 'wiki_en') if info.get(key)}}
    write_state(code, state)
    return state


def persist_image(code, result, info):
    data, language = result
    if not data:
        return
    sub = Path() if language == 'zh' else Path('fallback')
    write_card_image(IMAGE_DIR / 'full' / sub / f'{code}.jpg', data)
    write_card_image(IMAGE_DIR / 'web-thumbs' / sub / f'{code}.jpg', resize_thumbnail(data))
    if language == 'zh':
        write_state(code, {'attempts': 0, 'next_check': 0, 'complete': True, 'info': {}})
    elif not read_state(code):
        record_failure(code, info)


def next_chinese_check(code):
    state = read_state(code)
    return state['next_check'] if state and not state.get('complete') else None


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
            existing, _ = await asyncio.to_thread(load_disk_image, code, 'full', chinese_only=True)
            if existing:
                return existing
            state = await asyncio.to_thread(read_state, code)
            if state and not state.get('complete') and state['next_check'] > time.time():
                return None
            # 先持久化下一次时间，进程中断也不会在重启时密集重试。
            await asyncio.to_thread(record_failure, code, info)
            image = await fetch_chinese_image(code, info)
            if image:
                await asyncio.to_thread(persist_image, code, (image, 'zh'), info)
            return image
    return await single_flight(_chinese_tasks, code, download)


async def recover_chinese(code, info):
    while True:
        state = await asyncio.to_thread(read_state, code)
        if not state or state.get('complete'):
            return
        await asyncio.sleep(max(0, state['next_check'] - time.time()))
        try:
            await retry_chinese(code, info)
            existing, _ = await asyncio.to_thread(load_disk_image, code, 'full', chinese_only=True)
            if existing:
                return
        except Exception:
            logging.getLogger(__name__).exception('卡图后台恢复失败 code=%s', code)
            await asyncio.sleep(60)


def schedule_recovery(code, info):
    if code not in _retry_tasks:
        task = asyncio.create_task(recover_chinese(code, info))
        _retry_tasks[code] = task
        task.add_done_callback(lambda done: _retry_tasks.pop(code, None))


async def ensure_recovery(code, info):
    if not await asyncio.to_thread(read_state, code):
        await asyncio.to_thread(record_failure, code, info)
    schedule_recovery(code, info)


async def start_image_recovery():
    def pending():
        directory = IMAGE_DIR / 'web-recovery'
        return [(int(p.stem), read_state(int(p.stem))) for p in directory.glob('*.json') if p.stem.isdecimal()]
    for code, state in await asyncio.to_thread(pending):
        if state and not state.get('complete'):
            schedule_recovery(code, state.get('info', {}))


async def stop_image_recovery():
    tasks = set(_retry_tasks.values()) | set(_image_tasks.values()) | set(_chinese_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _retry_tasks.clear(); _image_tasks.clear(); _chinese_tasks.clear()


async def full_card_image(code, info, refresh='', size='full'):
    existing = await asyncio.to_thread(load_disk_image, code, size)
    if existing[0]:
        if existing[1] == 'en':
            await ensure_recovery(code, info)
        return existing
    async def load():
        # 列表与大图请求共用同一次下载，图片永久落盘。
        current = await asyncio.to_thread(load_disk_image, code)
        if current[0]:
            return current
        cached = _cache.get(code)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        image = await retry_chinese(code, info)
        if image:
            return image, 'zh'
        async with _limit:
            result = await fetch_card_image(code, info, try_chinese=False)
        if result[0]:
            await asyncio.to_thread(persist_image, code, result, info)
        else:
            _cache[code] = (time.monotonic() + 60, result)
            while len(_cache) > 256:
                _cache.popitem(last=False)
        return result
    result = await single_flight(_image_tasks, code, load)
    if result[1] == 'en':
        await ensure_recovery(code, info)
    if size == 'thumb' and result[0]:
        return await asyncio.to_thread(load_disk_image, code, size)
    return result


async def check_chinese_image(code, info, size='full'):
    image, _ = await asyncio.to_thread(load_disk_image, code, size, chinese_only=True)
    if image:
        return image
    image = await retry_chinese(code, info)
    if not image:
        schedule_recovery(code, info)
        return None
    if size == 'thumb':
        return (await asyncio.to_thread(load_disk_image, code, size, chinese_only=True))[0]
    return image
