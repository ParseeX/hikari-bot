"""Cardrush 图片的 QQ 投递前处理。

这里只处理图片压缩和尺寸日志，不依赖 OneBot，因此渲染层和其他发布渠道
可以继续使用原始图片。
"""

import asyncio
from collections.abc import Sequence
from io import BytesIO

from PIL import Image

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq

# 目标值留出传输协议和编码开销，降低 QQ 发送超时的概率。
_TARGET_BYTES = 200_000
# 压缩器无法严格保证目标大小，超过该值时只记录警告，不再次改变画质。
_WARNING_BYTES = 230_000


def _image_info(
    image_bytes: bytes,
) -> tuple[int, int, str]:
    """读取压缩结果的尺寸和格式，不修改图片字节。"""
    with Image.open(BytesIO(image_bytes)) as image:
        return image.width, image.height, image.format or "UNKNOWN"


async def prepare_qq_pages(
    pages: Sequence[bytes],
    target_bytes: int = _TARGET_BYTES,
) -> list[bytes]:
    """按原顺序压缩日报页面，返回只供 QQ 使用的图片副本。"""
    compressed_pages: list[bytes] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        compressed = await asyncio.to_thread(
            compress_for_qq,
            page,
            target_bytes=target_bytes,
        )
        width, height, image_format = _image_info(compressed)
        warning = (
            f", WARNING: above {_WARNING_BYTES} bytes"
            if len(compressed) > _WARNING_BYTES
            else ""
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes, "
            f"{width}x{height} {image_format}{warning}"
        )
        compressed_pages.append(compressed)
    return compressed_pages
