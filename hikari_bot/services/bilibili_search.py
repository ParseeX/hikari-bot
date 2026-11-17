"""缺少原链接的小程序卡片按完整标题搜索；仅在隔离解析进程调用外部 API。"""
from dataclasses import dataclass
from html import unescape
import re

from .platform import Result

BV = re.compile(r'BV[0-9A-Za-z]{10}')


@dataclass(frozen=True)
class VideoChoices(Result):
    title: str = ''
    candidates: tuple = ()


def exact_videos(title, rows):
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        bvid, name = row.get('bvid'), row.get('title')
        if not isinstance(bvid, str) or not BV.fullmatch(bvid) or not isinstance(name, str):
            continue
        # 搜索接口用 em 高亮命中词；只去掉标记和外侧空白，不做模糊近似。
        name = unescape(re.sub(r'<[^>]*>', '', name)).strip()
        if name != title.strip():
            continue
        author, duration = row.get('author'), row.get('duration')
        found.setdefault(bvid, dict(bvid=bvid, title=name,
            author=author[:80] if isinstance(author, str) else '',
            duration=duration[:16] if isinstance(duration, str) else ''))
    return list(found.values())


async def search_title(title, *, query=None):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ValueError('标题无效')
    from bilibili_api import search
    query = query or search.search_by_type
    rows = []
    # 有界搜索：前三页，每页 20 条；不把搜索结果唯一声称为全站唯一。
    for page in range(1, 4):
        data = await query(keyword=title, search_type=search.SearchObjectType.VIDEO,
                           page=page, page_size=20)
        if not isinstance(data, dict) or not isinstance(data.get('result'), list):
            raise ValueError('搜索返回无效')
        rows.extend(data['result'][:20])
        pages = data.get('numPages')
        if not data['result'] or (type(pages) is int and page >= pages):
            break
    return exact_videos(title, rows)
