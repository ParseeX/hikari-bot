"""Bot 启动后在后台预缓存卡组库缺失的卡图。"""
from nonebot import get_driver

from hikari_bot.core.config import settings
from hikari_bot.services.mycard_archive.image_cache import CardImageCache
from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.full_image import start_image_recovery, stop_image_recovery


cache = CardImageCache(DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3'))
driver = get_driver()


@driver.on_startup
async def start_cache():
    cache.start()
    await start_image_recovery()


@driver.on_shutdown
async def stop_cache():
    await cache.close()
    await stop_image_recovery()
