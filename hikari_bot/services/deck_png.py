"""无损卡组 PNG 输出，所有图片编码共用最多两个后台线程。"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io


_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deck-png")


def _encode(image, as_base64=False):
    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG", compress_level=3)
        data = buffer.getvalue()
    return base64.b64encode(data).decode("ascii") if as_base64 else data


def _save(image, path):
    image.save(path, format="PNG", compress_level=3)


async def _run(image, operation, *args):
    def work():
        try:
            return operation(image, *args)
        finally:
            image.close()

    try:
        future = asyncio.get_running_loop().run_in_executor(_workers, work)
    except Exception:
        image.close()
        raise
    # 取消调用方时仍让编码完成并关闭图片，避免关闭后台正在读取的像素。
    return await asyncio.shield(future)


async def encode_deck_png(image) -> bytes:
    return await _run(image, _encode)


async def encode_deck_png_base64(image) -> str:
    return await _run(image, _encode, True)


async def save_deck_png(image, path) -> None:
    await _run(image, _save, path)
