"""面向两个平台的卡面加载器，显式注入主库及缓存目录。"""
import asyncio
from contextvars import ContextVar
from dataclasses import replace
import json
from pathlib import Path
from urllib.parse import quote
import aiohttp

from .card_image_cache import read_card_image, write_card_image
from .card_image_sources import IMAGE_FULL, ImageSource, image_sources, download_image
from .card_art import _quiet_log, _crop_chinese_card_art, normalize_card_art


image_trace = ContextVar('card_image_trace', default='')


async def resolved_image_id(session, info):
    """编号迁移后仅接受英文名精确匹配的外部身份，不相信图片 HTTP 200。"""
    name = info.get('en_name') or info.get('wiki_en') or info.get('md_en_n')
    if not name:
        return None
    try:
        url = 'https://db.ygoprodeck.com/api/v7/cardinfo.php?name=' + quote(name, safe='')
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3, connect=2)) as response:
            if response.status != 200:
                return None
            cards = json.loads(await response.read()).get('data', [])
        if len(cards) == 1 and cards[0].get('name') == name:
            code = cards[0].get('id')
            if type(code) is int and 0 < code <= 2147483647:
                return code
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
        pass
    return None


class CardMedia:
    def __init__(self, catalog, cache, *, log_message=_quiet_log):
        self.catalog, self.cache = catalog, Path(cache)
        self.log_message = log_message

    async def full(self, card_id):
        code = int(card_id)
        if not 0 < code <= 2147483647:
            return None
        path = self.cache / f"{code}.jpg"
        cached = await asyncio.to_thread(read_card_image, path)
        if cached:
            return cached
        info = await asyncio.to_thread(self.catalog.by_id, code)
        if not info:
            return None
        temporary = info.get('temporary_id')
        migrated = temporary and code in (int(temporary), int(info['id']))

        async def log(message):
            await self.log_message(f'trace={image_trace.get()} code={code} {message}')

        async with aiohttp.ClientSession() as session:
            for source in image_sources(code, half=False, pre_release_code=temporary):
                if migrated and source.name in {'IMAGE_ORIGIN', 'IMAGE_FULL'}:
                    resolved = await resolved_image_id(session, info)
                    if resolved is None:
                        await log('IMAGE_FULL identity_unverified')
                        continue
                    await log(f'IMAGE_FULL verified_id={resolved}')
                    source = ImageSource('IMAGE_FULL', f'{IMAGE_FULL}{resolved}.jpg')
                # 主中文源和历史临时编号也只对超时、429、5xx 重试一次。
                source = replace(source, retries=1)
                data = await download_image(session, source, log_message=log)
                if data:
                    try:
                        await asyncio.to_thread(write_card_image, path, data)
                    except OSError:
                        pass  # 缓存失败不丢弃已经成功获取的图片。
                    await log(f'{source.name} loaded bytes={len(data)}')
                    return data
        await log('all_sources_failed')
        return None

    async def art(self, card_id):
        data = await self.full(card_id)
        if data:
            return await asyncio.to_thread(lambda: normalize_card_art(_crop_chinese_card_art(data)))
        return None
