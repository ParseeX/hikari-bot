"""面向两个平台的卡面加载器，显式注入主库及缓存目录。"""
import asyncio
from pathlib import Path
import aiohttp

from .card_image_cache import read_card_image, write_card_image
from .card_image_sources import image_sources, download_image
from .card_art import _quiet_log, _crop_chinese_card_art, normalize_card_art


class CardMedia:
    def __init__(self, catalog, cache):
        self.catalog, self.cache = catalog, Path(cache)

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
        migrated = temporary and str(temporary) != str(info.get('passcode') or info.get('id'))
        async with aiohttp.ClientSession() as session:
            for source in image_sources(code, half=False, pre_release_code=temporary):
                # 对改过编号的卡，不接受未核验身份的 YGOPRODeck HTTP 200。
                if migrated and source.name in {'IMAGE_ORIGIN', 'IMAGE_FULL'}:
                    continue
                data = await download_image(session, source, log_message=_quiet_log)
                if data:
                    try:
                        await asyncio.to_thread(write_card_image, path, data)
                    except OSError:
                        pass  # 缓存失败不丢弃已经成功获取的图片。
                    return data
        return None

    async def art(self, card_id):
        data = await self.full(card_id)
        if data:
            return await asyncio.to_thread(lambda: normalize_card_art(_crop_chinese_card_art(data)))
        return None
