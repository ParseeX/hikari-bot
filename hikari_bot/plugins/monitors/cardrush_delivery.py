import asyncio
from collections.abc import Sequence

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq


async def prepare_qq_pages(
    pages: Sequence[bytes],
    max_bytes: int = 1_000_000,
) -> list[bytes]:
    compressed_pages: list[bytes] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        compressed = await asyncio.to_thread(
            compress_for_qq,
            page,
            max_bytes=max_bytes,
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes"
        )
        compressed_pages.append(compressed)
    return compressed_pages
