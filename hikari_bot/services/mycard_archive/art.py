"""与卡图命令共享裁切资源；有限缓存和下载并发，不写入卡片或归档数据库。"""
import asyncio
from collections import OrderedDict
import io
import time

from PIL import Image, ImageOps


_cache = OrderedDict()
_limit = asyncio.Semaphore(3)


def square_card_art(data):
    """输入已是插画裁切源；等比裁为方图，不拉伸整卡，也不补白边。"""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        square = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'), (624, 624),
                              method=Image.Resampling.LANCZOS)
        square.info.clear()
        output = io.BytesIO()
        square.save(output, format='JPEG', quality=95)
        return output.getvalue()


async def card_art(code):
    async with _limit:
        cached = _cache.get(code)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(code)
            return cached[1]
        from hikari_bot.services.ygocard import get_image_by_id
        try:
            data = await asyncio.wait_for(get_image_by_id(code), timeout=25)
            if data:
                data = await asyncio.to_thread(square_card_art, data)
        except (TimeoutError, OSError, ValueError):
            data = None
        _cache[code] = (time.monotonic() + (3600 if data else 60), data)
        _cache.move_to_end(code)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return data
