"""单次链接解析：不启动 NoneBot driver、不连接 QQ、不运行 scheduler。"""
import asyncio
import json
import re
from pathlib import Path
import sys
from types import SimpleNamespace


async def run(directory):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import nonebot
    nonebot.logger.remove()
    nonebot.init(_env_file=[directory / "absent.env"], driver="~aiohttp", log_level="ERROR",
                 localstore_cache_dir=directory / "cache", localstore_config_dir=directory / "config",
                 localstore_data_dir=directory / "data", parser_need_forward_contents=False,
                 parser_max_size=90, parser_duration_maximum=480)
    if nonebot.load_plugin("nonebot_plugin_parser") is None:
        raise RuntimeError("解析组件不可用")
    from nonebot_plugin_parser.parsers import BaseParser, VideoContent, BilibiliParser
    from nonebot_plugin_parser.download import StreamDownloader
    from nonebot_plugin_parser.renders.base import ImageRenderer
    from nonebot_plugin_parser.renders import get_renderer
    from nonebot_plugin_parser.helper import Text, Reference, Image, Video, Voice, File
    from nonebot.matcher import current_bot
    from hikari_bot.services.parser_compat import install_compat
    install_compat(StreamDownloader, ImageRenderer, VideoContent)
    # 合并转发节点只在工作进程展开为顺序消息；不会伪造真人身份发送。
    current_bot.set(SimpleNamespace(self_id="link-worker"))
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    text = request['text']
    if request.get('search_title') is True:
        from hikari_bot.services.bilibili_search import search_title
        try:
            candidates = await asyncio.wait_for(search_title(text, author=request.get('author', '')), timeout=30)
        except Exception:
            candidates = None
        if not candidates or len(candidates) > 1:
            rows = ([{'kind': 'video_choices', 'title': text, 'author': request.get('author', ''), 'candidates': candidates}]
                    if candidates else [{'kind': 'text', 'code': 'bili_search_failed' if candidates is None else 'bili_title_missing'}])
            (directory / 'result.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
            return
        text = 'https://www.bilibili.com/video/' + candidates[0]['bvid']
    rows, total = [], 0

    def append(message):
        nonlocal total
        for segment in message:
            if isinstance(segment, Text):
                rows.append({"kind": "text", "text": str(segment)})
            elif isinstance(segment, Reference):
                for node in segment.children:
                    append(node.content)
            elif isinstance(segment, (Image, Video, Voice, File)):
                kind = "image" if isinstance(segment, Image) else "video" if isinstance(segment, Video) else "audio" if isinstance(segment, Voice) else "file"
                source = Path(segment.path).resolve() if segment.path else None
                if source is not None and not source.is_relative_to(directory):
                    raise ValueError("工作目录之外的媒体")
                raw = segment.raw_bytes if segment.raw else source.read_bytes() if source else None
                if not raw:
                    raise ValueError("媒体内容缺失")
                total += len(raw)
                if total > 100 * 1024 * 1024:
                    raise ValueError("媒体总量超限")
                target = directory / f"part-{len(rows)}"
                target.write_bytes(raw)
                rows.append({"kind": kind, "path": target.name,
                             "name": segment.name or (source.name if source else "media")})
            else:
                raise ValueError("当前官方入口尚未支持的解析结果")
            if len(rows) > 40:
                raise ValueError("媒体数量超限")

    if text.strip().removeprefix('/').split(maxsplit=1)[0] == 'bm':
        match = re.fullmatch(r'/?bm\s+(BV[A-Za-z0-9]{10})(?:\s+([1-9]\d{0,2}))?\s*', text.strip())
        if not match:
            rows.append({'kind': 'text', 'code': 'audio_usage'})
        else:
            parser = BilibiliParser()
            index = int(match[2] or '1') - 1
            _, url = await parser.extract_download_urls(bvid=match[1], page_index=index)
            if not url:
                rows.append({'kind': 'text', 'code': 'audio_missing'})
            else:
                path = await parser.downloader.download_audio(url, audio_name=f'{match[1]}-{index}.mp3', ext_headers=parser.headers)
                append([Voice(path=path)])
        (directory / 'result.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        return

    for parser_type in BaseParser.get_all_subclass():
        for keyword, pattern in parser_type._key_patterns:
            if keyword in text and (match := pattern.search(text)):
                result = await parser_type().parse(keyword, match)
                try:
                    async for message in get_renderer(result.platform.name)(result).render_messages():
                        append(message)
                except Exception:
                    if not rows:
                        raise
                    rows.append({"kind": "text", "code": "partial"})
                if not rows:
                    rows.append({'kind': 'text', 'code': 'empty_result'})
                (directory / "result.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                return
    (directory / "result.json").write_text(json.dumps([
        {"kind": "text", "code": "unsupported"}], ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]).resolve()))
