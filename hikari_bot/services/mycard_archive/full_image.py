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
_limit = asyncio.Semaphore(6)
_timeout = aiohttp.ClientTimeout(total=8, connect=3)
MAX_BYTES = 4 * 1024 * 1024


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


async def fetch_card_image(code, info, *, download=download_image):
    """备用源只按卡号或精确英文名解析，不使用模糊名称猜测卡图。"""
    async with aiohttp.ClientSession() as session:
        async def attempt(url):
            try:
                return await download(session, url)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                return None
        image = await attempt(f'{CHINESE}{code}.jpg')
        if image:
            return image, 'zh'
        image = await attempt(f'{ENGLISH}{code}.jpg')
        if image:
            return image, 'en'
        # 临时卡密和异画编号不能随意换成另一张卡；只使用已确认的身份。
        canonical = info.get('id')
        if canonical and canonical != code:
            for origin, language in ((CHINESE, 'zh'), (ENGLISH, 'en')):
                image = await attempt(f'{origin}{canonical}.jpg')
                if image:
                    return image, language
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


async def full_card_image(code, info):
    async with _limit:
        cached = _cache.get(code)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(code)
            return cached[1]
        result = await fetch_card_image(code, info)
        _cache[code] = (time.monotonic() + (3600 if result[0] else 60), result)
        _cache.move_to_end(code)
        while len(_cache) > 256:
            _cache.popitem(last=False)
        return result
