import asyncio
from types import SimpleNamespace

import pytest

from hikari_bot.services.parser_compat import (
    install_compat,
    is_direct_bili_video,
    media_headers,
)


class Video:
    pass


def result(platform="bilibili", url="https://bilibili.com/BV1234567890?p=2", contents=None, repost=None):
    return SimpleNamespace(platform=SimpleNamespace(name=platform), url=url,
                           contents=[Video()] if contents is None else contents, repost=repost)


@pytest.mark.parametrize("host", ["upos-sz-mirrorcosov.bilivideo.com", "bilivideo.com", "A.BILIVIDEO.COM"])
def test_range_is_added_only_to_cdn(host):
    original = {"Referer": "https://www.bilibili.com"}
    assert media_headers(f"https://{host}/video", original) == {**original, "Range": "bytes=0-"}
    assert "Range" not in original


@pytest.mark.parametrize("url", ["https://bilivideo.com.evil.test/a", "https://example.com/a", "https://i0.hdslb.com/cover"])
def test_other_platforms_and_covers_unchanged(url):
    headers = {"Accept": "*/*"}
    assert media_headers(url, headers) is headers


def test_existing_range_preserved():
    assert media_headers("https://cdn.bilivideo.com/a", {"range": "bytes=12-99"}) == {"range": "bytes=12-99"}


@pytest.mark.parametrize("value", [
    result(platform="douyin"),
    result(url="https://t.bilibili.com/123"),
    result(url="https://www.bilibili.com/opus/123"),
    result(contents=[]),
    result(repost=object()),
    result(url="https://bilibili.com.evil.test/BV1234567890"),
])
def test_non_direct_video_keeps_card(value):
    assert not is_direct_bili_video(value, Video)


def test_scoped_wrappers_and_idempotence():
    class Downloader:
        async def _download_file(self, url, **kwargs):
            return url, kwargs

    class Renderer:
        def __init__(self, value, fail=False):
            self.result, self.fail = value, fail

        async def render_contents(self):
            if self.fail:
                yield "1 media download failed"
                raise RuntimeError("upstream error")
            yield "video"

        async def render_messages(self):
            yield "card"
            async for item in self.render_contents():
                yield item

    async def check():
        install_compat(Downloader, Renderer, Video)
        wrapped = Downloader._download_file
        install_compat(Downloader, Renderer, Video)
        assert Downloader._download_file is wrapped
        assert [x async for x in Renderer(result()).render_messages()] == ["video"]
        assert [x async for x in Renderer(result(platform="douyin")).render_messages()] == ["card", "video"]
        url, kwargs = await Downloader()._download_file("https://cdn.bilivideo.com/a", file_name="a.mp4")
        assert kwargs == {"file_name": "a.mp4", "ext_headers": {"Range": "bytes=0-"}}
        got = []
        with pytest.raises(RuntimeError, match="upstream error"):
            async for item in Renderer(result(), fail=True).render_messages():
                got.append(item)
        assert got == ["1 media download failed"]

    asyncio.run(check())
