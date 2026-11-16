"""官方 Markdown 图片素材；不使用公网图床，不降级为分条发送。"""
import asyncio
from dataclasses import dataclass
import hashlib
from io import BytesIO
import logging
import re
from urllib.parse import urlsplit
from uuid import uuid4

from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import MessageMarkdown
from nonebot.drivers import Request
from PIL import Image


class ImageDeliveryError(Exception):
    """只包含可向用户展示的固定原因，不包含签名链接或接口原文。"""


class VerifiedMarkdown(MessageMarkdown):
    # adapter 1.7.4 尚未声明此官方字段；保留 SDK 的鉴权、回复序号与序列化。
    force_verify_image_resource: bool = True


async def api(bot, method, *path, **kwargs):
    return await bot._request(Request(method, bot.adapter.get_api_base().joinpath(
        'v2', 'media-images', *path), timeout=20, **kwargs))


async def release_owned(bot, image_id, filename):
    """只释放本次创建且名称核验一致的素材；不清理后台已有素材。"""
    cursor, seen = '', set()
    for _ in range(100):
        page = await api(bot, 'GET', params={'cursor': cursor} if cursor else {})
        for item in page.get('images', []):
            if item.get('image_id') == image_id:
                if item.get('file_name') == filename:
                    await api(bot, 'DELETE', image_id, 'delete')
                return
        cursor = page.get('next_cursor', '')
        if not cursor or cursor in seen:
            return
        seen.add(cursor)


async def upload_image(bot, data, label, owned):
    if len(data) > 10_000_000:
        raise ImageDeliveryError('图片超过官方素材的 10 MB 上限')
    with Image.open(BytesIO(data)) as picture:
        kind, (width, height) = picture.format, picture.size
        picture.verify()
    if kind not in {'PNG', 'JPEG', 'GIF', 'WEBP'}:
        raise ImageDeliveryError('图片格式不支持 Markdown 内嵌')
    mime = 'image/' + kind.lower()
    filename = f'hikari-markdown-{uuid4().hex}.{kind.lower()}'
    md5 = hashlib.md5(data).hexdigest()
    prepared = await api(bot, 'POST', 'prepare', json={
        'file_name': filename, 'file_size': len(data), 'content_type': mime,
        'md5': md5, 'sha1': hashlib.sha1(data).hexdigest()})
    url = prepared['presigned_url']
    target = urlsplit(url)
    if (target.scheme != 'https' or not (target.hostname or '').endswith('.myqcloud.com')
            or target.username or target.password or target.port not in {None, 443}):
        raise ImageDeliveryError('平台返回了无法使用的图片上传地址')
    # 预签名 PUT 不得走 Bot._request，避免附带机器人鉴权头。
    response = await bot.adapter.request(Request('PUT', url, content=data,
        headers={'Content-Type': mime}, timeout=20))
    etag = next((v for k, v in response.headers.items() if k.lower() == 'etag'), '')
    if not 200 <= response.status_code < 300 or etag.strip('"').lower() != md5:
        raise ImageDeliveryError('图片上传校验未通过')
    completed = await api(bot, 'POST', 'complete', json={
        'upload_id': prepared['upload_id'], 'md5': etag.strip('"')})
    image_id = completed['image_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', image_id):
        raise ImageDeliveryError('平台返回了无效的图片素材')
    owned.append((image_id, filename))
    reference = completed['url']
    if not re.fullmatch(r'qqbot-media://image/v1/[A-Za-z0-9_.~-]+', reference):
        raise ImageDeliveryError('平台返回了无效的图片素材引用')
    return f'![{label} #{width}px #{height}px]({reference})'


async def send_images(bot, event, parts, render, *, history=False):
    """所有上传成功后只发一次；上传失败不发不完整图集。"""
    owned = []
    safe_release = True
    stage = '图片上传未完成'
    semaphore = asyncio.Semaphore(3)

    async def upload(index, part):
        if isinstance(part, str):
            return part
        async with semaphore:
            return await upload_image(bot, part, '历史曲线' if history else f'第 {index} 张卡图', owned)

    async def prepare_and_send():
        nonlocal stage, safe_release
        images = [part for part in parts if isinstance(part, bytes)]
        if len(images) > 100 or sum(map(len, images)) > 100 * 1024 * 1024:
            raise ImageDeliveryError('整组图片超过素材数量或大小上限')
        tasks = [asyncio.create_task(upload(i, part)) for i, part in enumerate(parts, 1)]
        try:
            rendered = await asyncio.gather(*tasks)
        finally:
            # gather 首个失败后不会自动取消其他上传，必须收拢后才能清理素材。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        content = render(rendered)
        from .delivery import context, ReplyPager
        key = context(bot, event)
        if not key or not ReplyPager().capacity(event, key[1]):
            raise ImageDeliveryError('图片上传后本条消息的回复时效或额度已用完')
        stage = '发送结果未确认，重新查询可能重复收到回复'
        safe_release = False
        await bot.send(event, MessageSegment.markdown(VerifiedMarkdown(content=content)))
        safe_release = True

    async def cleanup(image_id, filename):
        async with semaphore:
            await release_owned(bot, image_id, filename)

    try:
        await asyncio.wait_for(prepare_and_send(), timeout=80)
    except ImageDeliveryError:
        raise
    except Exception as exc:
        from nonebot.adapters.qq.exception import ActionFailed
        if isinstance(exc, ActionFailed):
            safe_release = exc.status_code in {400, 401, 403, 404, 413, 429}
            code = exc.code
            if code == 40035007:
                raise ImageDeliveryError('官方图片素材配额已满') from None
            if type(code) is int and 0 <= code <= 99999999:
                raise ImageDeliveryError(f'{stage}（平台错误码 {code}）') from None
        raise ImageDeliveryError(stage) from None
    finally:
        if owned and safe_release:
            try:
                # 已发送消息保留平台转存副本；只释放本次生成且名称核验一致的素材。
                results = await asyncio.wait_for(asyncio.gather(*(cleanup(*item) for item in owned),
                                                                return_exceptions=True), timeout=8)
                if any(isinstance(result, BaseException) for result in results):
                    logging.getLogger(__name__).warning('本次 Markdown 临时素材释放未完成')
            except Exception:
                logging.getLogger(__name__).warning('本次 Markdown 临时素材释放未完成')


@dataclass
class MarkdownImage:
    text: str
    image: bytes

    async def send(self, bot, event):
        await send_images(bot, event, (self.image,),
                          lambda parts: self.text + '\n\n' + parts[0], history=True)


GALLERY_MARKER = 'HIKARIGALLERYIMAGESPLACEHOLDER'


@dataclass
class MarkdownGallery:
    template: str
    parts: tuple

    async def send(self, bot, event):
        if self.template.count(GALLERY_MARKER) != 1:
            raise ImageDeliveryError('整组卡图文案必须包含一次图片字段')
        await send_images(bot, event, self.parts,
                          lambda parts: self.template.replace(GALLERY_MARKER, '\n\n'.join(parts)))
