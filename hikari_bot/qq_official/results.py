"""事件绑定的混合媒体分页；失败不自动重发或改成主动消息。"""
from .output_templates import present, Output
from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import time

from nonebot.adapters.qq import Message, MessageSegment
from .delivery import context, chunk_end, TEXT_CHARS
from .markdown_image import MarkdownImage, MarkdownGallery, GALLERY_MARKER


@dataclass
class PendingResult:
    parts: list
    expires: float
    size: int = 0


def payload_size(part):
    if isinstance(part, MarkdownGallery):
        return len(part.template.encode('utf-8')) + sum(
            len(value) if isinstance(value, bytes) else len(value.encode('utf-8'))
            for value in part.parts)
    if isinstance(part, MarkdownImage):
        return len(part.text.encode('utf-8')) + len(part.image)
    if isinstance(part, str):
        return len(part.encode('utf-8'))
    if isinstance(part, Message):
        return sum(payload_size(segment) for segment in part)
    content = part.data.get('content', part.data.get('text', ''))
    return len(content.encode('utf-8')) if isinstance(content, str) else len(content)


class ResultDelivery:
    def __init__(self, pager, *, clock=time.monotonic):
        self.pager, self.clock = pager, clock
        self.pending = OrderedDict()
        self.lock = asyncio.Lock()

    def has_pending(self, bot, event):
        value = self.pending.get(context(bot, event))
        return value is not None and value.expires > self.clock()

    async def send(self, bot, event, result=None, *, markdown_images=False):
        key = context(bot, event)
        if key is None:
            return
        async with self.lock:
            now = self.clock()
            for old in [k for k, v in self.pending.items() if v.expires <= now]:
                del self.pending[old]
            if result is not None:
                self.pager.pending.pop(key, None)
                self.pending.pop(key, None)
                parts, offset = [], 0
                while offset < len(result.text):
                    end = chunk_end(result.text, offset, TEXT_CHARS)
                    part = result.text[offset:end]
                    parts.append(Output(part, result.text.key, result.text.mode) if isinstance(result.text, Output) else part)
                    offset = end
                if markdown_images and result.images:
                    # 每日/随机一卡复用卡图素材与尺寸语法；续发保留同一种格式。
                    parts.append(MarkdownGallery(GALLERY_MARKER, result.images))
                else:
                    parts.extend(MessageSegment.file_image(data) for data in result.images)
                if (isinstance(result.text, Output) and result.text.mode == 'markdown'
                        and result.text.key == 'history.mycard_reports_body_2fe0148e'
                        and len(result.images) == 1):
                    # 历史曲线作为 Markdown 素材引用；保留整份用户正文。
                    parts = [MarkdownImage(str(result.text), result.images[0])]
                if not markdown_images and result.text and len(result.text) <= TEXT_CHARS and len(result.images) == 1 and getattr(result.text, 'mode', 'text') == 'text':
                    # 已在官方 C2C 客户端验证普通图文合发；QQ 自行决定图片/文字布局。
                    parts = [Message(result.text) + MessageSegment.file_image(result.images[0])]
                if result.file is not None:
                    parts.append(MessageSegment.file_file(result.file, file_name=result.filename))
                for media in result.media:
                    if isinstance(media, str):
                        offset = 0
                        while offset < len(media):
                            end = chunk_end(media, offset, TEXT_CHARS)
                            part = media[offset:end]
                            parts.append(Output(part, media.key, media.mode) if isinstance(media, Output) else part)
                            offset = end
                    else:
                        parts.append(getattr(MessageSegment, 'file_' + media.kind)(media.data, file_name=media.name or None))
                size = sum(payload_size(part) for part in parts)
                if size > 100 * 1024 * 1024:
                    parts, size = [present('paging.media_size')], 0
                elif len(self.pending) >= 128 or size + sum(p.size for p in self.pending.values()) > 256 * 1024 * 1024:
                    parts, size = [present('paging.media_busy')], 0
                self.pending[key] = PendingResult(parts, now + 600, size)
            pending = self.pending.get(key)
            if pending is None:
                return
            while pending.parts:
                capacity = self.pager.capacity(event, key[1])
                if capacity == 0:
                    return
                if capacity == 1 and len(pending.parts) > 1:
                    await bot.send(event, present('paging.results_23325cb3', {}))
                    return
                from .media import send_formatted
                if not await asyncio.wait_for(send_formatted(bot, event, pending.parts[0], self.pager), timeout=90):
                    return
                part = pending.parts.pop(0)
                pending.size = max(0, pending.size - payload_size(part))
                pending.expires = self.clock() + 600
            self.pending.pop(key, None)
