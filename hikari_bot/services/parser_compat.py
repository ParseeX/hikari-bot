"""Small, repository-owned adapters for nonebot-plugin-parser 2.6.6.

Keep upstream parsing, commands, caching, media limits and rendering unchanged
except for direct Bilibili video cards and bilivideo CDN request headers.
"""

from functools import wraps
import re
from urllib.parse import urlsplit


def media_headers(url, headers=None):
    """Range is required by the affected Bilibili CDN; never change other hosts."""
    host = (urlsplit(url).hostname or "").lower()
    if not (host == "bilivideo.com" or host.endswith(".bilivideo.com")):
        return headers
    result = dict(headers or {})
    if not any(key.lower() == "range" for key in result):
        result["Range"] = "bytes=0-"
    return result


def is_direct_bili_video(result, video_type):
    if result.platform.name != "bilibili" or result.repost is not None:
        return False
    url = urlsplit(result.url or "")
    if (url.hostname or "").lower() not in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        return False
    return bool(
        re.fullmatch(r"/(?:video/)?(?:BV[A-Za-z0-9]+|av\d+)/?", url.path)
        and any(isinstance(content, video_type) for content in result.contents)
    )


def install_compat(downloader_type, renderer_type, video_type):
    """Wrap two narrow upstream extension points, once per process."""
    original_download = downloader_type._download_file
    if not getattr(original_download, "_hikari_bili_compat", False):
        @wraps(original_download)
        async def download(self, url, **kwargs):
            kwargs["ext_headers"] = media_headers(url, kwargs.get("ext_headers"))
            return await original_download(self, url, **kwargs)

        download._hikari_bili_compat = True
        downloader_type._download_file = download

    original_render = renderer_type.render_messages
    if not getattr(original_render, "_hikari_bili_compat", False):
        @wraps(original_render)
        async def render(self):
            if is_direct_bili_video(self.result, video_type):
                # Do not render a card and then discard it. Preserve upstream
                # media ordering, thumbnails, failure notices and exceptions.
                async for message in self.render_contents():
                    yield message
            else:
                async for message in original_render(self):
                    yield message

        render._hikari_bili_compat = True
        renderer_type.render_messages = render
