import asyncio
import io
import os
import sys

import aiohttp
from PIL import Image, ImageOps

from hikari_bot.core.config import PROJECT_ROOT, settings
from hikari_bot.core.constants import DATA_DIR
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.core.logger import log_message

IMAGE_ORIGIN = "https://images.ygoprodeck.com/images/cards_cropped/"
IMAGE_CHINESE = "https://cdn.233.momobako.com/ygopro/pics/"

_CHINESE_CARD_ART_CROP = (0.1325, 0.1897, 0.87, 0.6983)
CARD_ART_SIZE = (624, 624)

CARD_PICS = os.path.join(DATA_DIR, 'pics')
catalog = CardCatalog(settings.card_catalog_path)
_update_lock = asyncio.Lock()


async def update_cdb():
    """保留原命令入口，更新统一主库；下载/校验失败不覆盖已有数据。"""
    async with _update_lock:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(PROJECT_ROOT / 'scripts/card_catalog/catalog.py'),
            '--db', str(catalog.path), 'sync-base',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=300)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            # 同步脚本的错误可能包含外部响应，不把原文转发到群聊。
            raise RuntimeError(f'卡片主库同步失败（退出码 {process.returncode}）')
        await asyncio.to_thread(catalog.validate)


# ==================== 图片处理 ====================

async def get_unknown_card():
    """获取未知卡片的默认图片"""
    local_path = os.path.join(CARD_PICS, "unknown.jpg")
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read()
    
    url = "https://cdn.233.momobako.com/ygopro/textures/unknown.jpg"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    os.makedirs(CARD_PICS, exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(data)
                    return data
                else:
                    await log_message(f"[get_unknown_card] Image not found: {url}")
                    return None
    except Exception as e:
        await log_message(f"[get_unknown_card] Error loading image {url}: {e}")
        return None


async def get_ygopic(id: int, half: bool = True):
    """根据卡片ID获取卡片图片"""
    if half:
        local_path = os.path.join(CARD_PICS, f"{id}.jpg")
        if os.path.exists(local_path):
            with open(local_path, "rb") as f:
                return f.read()

    # 本地没有则下载
    url = f"{IMAGE_CHINESE}{id}.jpg{'!half' if half else ''}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if half:
                        os.makedirs(CARD_PICS, exist_ok=True)
                        with open(local_path, "wb") as f:
                            f.write(data)
                    return data
                else:
                    await log_message(f"[get_ygopic] Image not found: {url}")
                    return await get_unknown_card()
    except Exception as e:
        await log_message(f"[get_ygopic] Error loading image {url}: {e}")
        return await get_unknown_card()


def _crop_chinese_card_art(image_data: bytes) -> bytes:
    """裁去中文卡图模板的文字和边框，仅保留中间插画。"""
    with Image.open(io.BytesIO(image_data)) as image:
        width, height = image.size
        left_ratio, top_ratio, right_ratio, bottom_ratio = _CHINESE_CARD_ART_CROP
        crop_box = (
            round(width * left_ratio),
            round(height * top_ratio),
            round(width * right_ratio),
            round(height * bottom_ratio),
        )
        cropped = image.crop(crop_box).convert("RGB")

        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()


async def get_image_by_id(id: int):
    """根据卡片 ID 下载卡图，主源不可用时回退到中文源。"""
    image_urls = (
        ("IMAGE_ORIGIN", IMAGE_ORIGIN + str(id) + ".jpg"),
        ("IMAGE_CHINESE", IMAGE_CHINESE + str(id) + ".jpg"),
    )
    async with aiohttp.ClientSession() as session:
        for source_name, image_url in image_urls:
            try:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        if source_name == "IMAGE_CHINESE":
                            return _crop_chinese_card_art(image_data)
                        return image_data

                    await log_message(
                        f"[get_image_by_id] Failed to download image from "
                        f"{source_name}: {response.status}"
                    )
            except Exception as e:
                await log_message(
                    f"[get_image_by_id] Exception occurred while downloading "
                    f"image from {source_name}: {e}"
                )

    return None


# ==================== 卡片信息获取 ====================

async def get_card_info_by_id(id: str):
    """按正式卡密、历史临时卡密或已登记的异画编号读取主库。"""
    return await asyncio.to_thread(catalog.by_id, id)


async def get_card_info(keyword: str):
    """通过主库的所有语言卡名及别名搜索。"""
    return await asyncio.to_thread(catalog.search, keyword)


def normalize_card_art(data: bytes) -> bytes:
    """统一画面大小并清除内嵌缩略图，避免发送端误读 JPEG 尺寸。"""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image = ImageOps.pad(ImageOps.exif_transpose(image).convert('RGB'), CARD_ART_SIZE,
                             method=Image.Resampling.LANCZOS, color='white')
        # 同尺寸也重新编码；源文件的 EXIF 中可能含有 120×120 的 JPEG。
        image.info.clear()
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=95)
        return buffer.getvalue()


async def get_card_images(keyword: str) -> list[tuple[int, bytes | None]]:
    """有限并发下载全部画面，保留顺序和失败位置供一条消息展示。"""
    ids = await asyncio.to_thread(catalog.image_ids, keyword)
    semaphore = asyncio.Semaphore(3)

    async def download(card_id):
        async with semaphore:
            try:
                data = await asyncio.wait_for(get_image_by_id(card_id), timeout=25)
                if data:
                    return card_id, await asyncio.to_thread(normalize_card_art, data)
            except (TimeoutError, OSError, ValueError):
                await log_message(f'[card_images] 图片加载失败：{card_id}')
            return card_id, None

    return await asyncio.gather(*(download(card_id) for card_id in ids))


# ==================== 工具函数 ====================

def is_card_id(keyword: str):
    """卡图允许直接指定卡密及图片编号，包括临时编号和前导零。"""
    return keyword.isascii() and keyword.isdigit() and 0 < int(keyword) <= 2147483647


def keyword_in_card(card, keyword: str):
    """递归检查卡片信息中是否包含指定关键词"""
    if isinstance(card, dict):
        for key, value in card.items():
            if keyword_in_card(value, keyword):
                return True
    elif isinstance(card, list):
        for item in card:
            if keyword_in_card(item, keyword):
                return True
    elif isinstance(card, str):
        if keyword.lower() in card.lower():
            return True
    return False

def random_card(seed: int = None, *, include_artworks: bool = False):
    """从主库抽卡，可让每个已登记异画也独立参与抽取。"""
    return catalog.random_id(seed, include_artworks=include_artworks)


def metaltronus_calc(id: int):
    """在主库中查找攻击力、种族、属性至少两项相同的其他怪兽。"""
    return catalog.metaltronus(id)
