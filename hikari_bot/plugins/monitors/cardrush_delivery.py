import asyncio
from collections.abc import Sequence
from io import BytesIO

from PIL import Image

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq

_TARGET_BYTES = 350_000
_WARNING_BYTES = 450_000


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.size


async def prepare_qq_pages(
    pages: Sequence[bytes],
    target_bytes: int = _TARGET_BYTES,
) -> list[bytes]:
    compressed_pages: list[bytes] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        compressed = await asyncio.to_thread(
            compress_for_qq,
            page,
            target_bytes=target_bytes,
        )
        width, height = _image_size(compressed)
        warning = (
            f", WARNING: above {_WARNING_BYTES} bytes"
            if len(compressed) > _WARNING_BYTES
            else ""
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes, "
            f"{width}x{height}{warning}"
        )
        compressed_pages.append(compressed)
    return compressed_pages
