import asyncio
import base64
import io
import threading

import pytest
from PIL import Image

from hikari_bot.services import deck_png
from hikari_bot.services.deck_message_image import encode_deck_message_image, encode_deck_message_base64


def test_qq_uses_fixed_63_percent_jpeg_and_website_keeps_original_png():
    async def run():
        original = Image.new('RGBA', (2115, 3035), (255, 0, 0, 128))
        qq = await encode_deck_message_image(original.copy())
        text = await encode_deck_message_base64(original.copy())
        png = await deck_png.encode_deck_png(original.copy())
        assert base64.b64decode(text) == qq
        with Image.open(io.BytesIO(qq)) as image:
            assert image.format == 'JPEG'
            assert image.size == (1332, 1911)
            assert image.getpixel((0, 0))[1] > 100  # 透明区域按白底合成。
        with Image.open(io.BytesIO(png)) as image:
            assert image.format == 'PNG' and image.size == original.size
            assert image.tobytes() == original.tobytes()
        original.close()
    asyncio.run(run())


def test_large_qq_image_keeps_fixed_dimensions_without_byte_cap():
    async def run():
        source = Image.frombytes('RGB', (4000, 4000), __import__('random').Random(1).randbytes(4000*4000*3))
        data = await encode_deck_message_image(source)
        assert len(data) > 1_000_000
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (2520, 2520)
    asyncio.run(run())


def test_cancelled_encoding_closes_image_after_worker_finishes():
    async def run():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        image = Image.new('RGB', (2, 2))
        def encode(value):
            started.set()
            assert release.wait(3)
            value.getpixel((0, 0))
            finished.set()
        task = asyncio.create_task(deck_png._run(image, encode))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        # 在同一个池中等到之前任务的 finally 已执行。
        await deck_png.encode_deck_png(Image.new('RGB', (1, 1)))
        with pytest.raises(ValueError):
            image.getpixel((0, 0))
    asyncio.run(run())


def test_disabled_local_chat_is_not_discovered():
    from nonebot.plugin.manager import PluginManager
    modules = set(PluginManager(search_path=['hikari_bot/plugins']).controlled_modules.values())
    assert 'hikari_bot.plugins.local_llm_chat' not in modules
    assert 'hikari_bot.plugins._local_llm_chat' not in modules
    assert 'hikari_bot.plugins.local_llm_admin' in modules
