import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from io import BytesIO

from PIL import Image

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq

_TARGET_BYTES = 200_000
_WARNING_BYTES = 230_000


def _image_info(
    image_bytes: bytes,
) -> tuple[int, int, str]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.width, image.height, image.format or "UNKNOWN"


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


async def send_qq_pages(
    pages: Sequence[bytes],
    send_page: Callable[[str], Awaitable[object]],
    *,
    log_prefix: str,
) -> list[int]:
    timed_out_pages: list[int] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        try:
            encoded = base64.b64encode(page).decode()
            await send_page(f"base64://{encoded}")
        except Exception as error:
            info = getattr(error, "info", None)
            retcode = (
                info.get("retcode")
                if isinstance(info, Mapping)
                else getattr(error, "retcode", None)
            )
            if retcode != 1200:
                raise
            timed_out_pages.append(index)
            await log_message(
                f"{log_prefix} QQ send page {index}/{total}: "
                "retcode=1200 ignored; continuing."
            )
    return timed_out_pages
