"""Existing card-art pipeline, independent of production configuration and plugins."""
import asyncio
import io
import aiohttp
from PIL import Image, ImageOps

IMAGE_ORIGIN = "https://images.ygoprodeck.com/images/cards_cropped/"
IMAGE_CHINESE = "https://cdn.233.momobako.com/ygopro/pics/"
IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3, sock_read=6)
_CHINESE_CARD_ART_CROP = (0.1325, 0.1897, 0.87, 0.6983)
CARD_ART_SIZE = (624, 624)

async def _quiet_log(message):
    # Default caller receives None on unavailable artwork; no raw upstream logs.
    pass

def _valid_image(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
    return data


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


async def get_image_by_id(id: int, *, log_message=_quiet_log):
    """根据卡片 ID 下载卡图，主源不可用时回退到中文源。"""
    image_urls = (
        ("IMAGE_ORIGIN", IMAGE_ORIGIN + str(id) + ".jpg"),
        ("IMAGE_CHINESE", IMAGE_CHINESE + str(id) + ".jpg"),
    )
    async with aiohttp.ClientSession() as session:
        for source_name, image_url in image_urls:
            try:
                async with session.get(image_url, timeout=IMAGE_TIMEOUT) as response:
                    if response.status == 200:
                        image_data = await asyncio.to_thread(_valid_image, await response.read())
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


async def get_card_images(catalog, keyword: str, *, image_loader=get_image_by_id, log_message=_quiet_log) -> list[tuple[int, bytes | None]]:
    """有限并发下载全部画面，保留顺序和失败位置供一条消息展示。"""
    ids = await asyncio.to_thread(catalog.image_ids, keyword)
    semaphore = asyncio.Semaphore(3)

    async def download(card_id):
        async with semaphore:
            try:
                data = await asyncio.wait_for(image_loader(card_id), timeout=25)
                if data:
                    return card_id, await asyncio.to_thread(normalize_card_art, data)
            except (TimeoutError, OSError, ValueError):
                await log_message(f'[card_images] 图片加载失败：{card_id}')
            return card_id, None

    return await asyncio.gather(*(download(card_id) for card_id in ids))
