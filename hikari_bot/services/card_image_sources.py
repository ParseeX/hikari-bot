"""卡图与完整卡面共用的源地址、有效性检查和有界重试。"""
import asyncio
from dataclasses import dataclass

import aiohttp

from hikari_bot.services.card_image_cache import valid_card_image


IMAGE_ORIGIN = 'https://images.ygoprodeck.com/images/cards_cropped/'
IMAGE_FULL = 'https://images.ygoprodeck.com/images/cards/'
IMAGE_CHINESE = 'https://cdn.233.momobako.com/ygopro/pics/'
IMAGE_PRE_RELEASE = 'https://cdncf.moecube.com/ygopro-super-pre/data/pics/'
# 三个源加 CDN 的一次重试最多等待 24 秒，为卡图查询的 25 秒上限留余量。
IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=6, connect=2, sock_read=4, ceil_threshold=10)


@dataclass(frozen=True)
class ImageSource:
    name: str
    url: str
    full_card: bool = True
    retries: int = 0


def image_sources(code, *, artwork=False, half=False):
    """保留各入口的原有源顺序，先行卡 CDN 使用原卡密、无缩略图后缀。"""
    chinese = ImageSource('IMAGE_CHINESE', f'{IMAGE_CHINESE}{code}.jpg' + ('!half' if half and not artwork else ''))
    backup = ImageSource('IMAGE_PRE_RELEASE', f'{IMAGE_PRE_RELEASE}{code}.jpg', retries=1)
    if artwork:
        return (ImageSource('IMAGE_ORIGIN', f'{IMAGE_ORIGIN}{code}.jpg', full_card=False), chinese, backup)
    return (chinese, ImageSource('IMAGE_FULL', f'{IMAGE_FULL}{code}.jpg'), backup)


async def download_image(session, source, *, log_message):
    """只重试暂时性网络/服务故障；404 和无效图直接交给下一个源。"""
    for attempt in range(source.retries + 1):
        try:
            async with session.get(source.url, timeout=IMAGE_TIMEOUT) as response:
                if response.status == 200:
                    return await asyncio.to_thread(valid_card_image, await response.read())
                retryable = response.status in (408, 429) or response.status >= 500
                await log_message(f'[card_image] {source.name} status={response.status}')
        except (aiohttp.ClientError, TimeoutError) as error:
            retryable = True
            await log_message(f'[card_image] {source.name} error={type(error).__name__}')
        except (OSError, ValueError) as error:
            retryable = False
            await log_message(f'[card_image] {source.name} invalid={type(error).__name__}')
        if not retryable or attempt == source.retries:
            break
        await asyncio.sleep(0.1)
    return None
