"""缺少原链接的小程序卡片按完整标题搜索；仅在隔离解析进程调用外部 API。"""
from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from .platform import Result

BV = re.compile(r'BV[0-9A-Za-z]{10}')


@dataclass(frozen=True)
class VideoChoices(Result):
    title: str = ''
    candidates: tuple = ()
    author: str = ''


def search_text(text, *, highlighted=False):
    """仅移除搜索接口的 em 高亮，保留标题里的字面尖括号和其他符号。"""
    if highlighted:
        text = re.sub(r'</?em(?:\s+class=[\"\']keyword[\"\'])?\s*>', '', text, flags=re.I)
    text = unicodedata.normalize('NFKC', unescape(text))
    text = text.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'", '\u200b': '', '\ufeff': ''}))
    return ' '.join(text.split())


def exact_videos(title, rows, *, author='', highlighted=True):
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        bvid, name = row.get('bvid'), row.get('title')
        if not isinstance(bvid, str) or not BV.fullmatch(bvid) or not isinstance(name, str):
            continue
        name = search_text(name, highlighted=highlighted)
        if name != search_text(title):
            continue
        owner, duration = row.get('author'), row.get('duration')
        if author and (not isinstance(owner, str) or search_text(owner) != search_text(author)):
            continue
        found.setdefault(bvid, dict(bvid=bvid, title=name,
            author=owner[:80] if isinstance(owner, str) else '',
            duration=duration[:16] if isinstance(duration, str) else ''))
    return list(found.values())


async def search_title(title, *, author='', query=None):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ValueError('标题无效')
    if not isinstance(author, str) or len(author) > 80:
        raise ValueError('作者无效')
    from bilibili_api import search
    query = query or search.search_by_type
    wanted_title, wanted_author = title, author
    title, author = search_text(title), search_text(author)
    keyword = ' '.join(part for part in (author, title) if part)
    # 符号仅从备用搜索词去除，最终标题与作者核对不放宽。
    words = ' '.join(''.join(c if c.isalnum() else ' ' for c in title).split())
    alternate = ' '.join(part for part in (author, words) if part)
    keywords = [keyword] + ([alternate] if words and alternate != keyword else [])
    for keyword in keywords:
        rows = []
        # 每种搜索词最多三页；搜索结果唯一不等于全站唯一。
        for page in range(1, 4):
            data = await query(keyword=keyword, search_type=search.SearchObjectType.VIDEO,
                               page=page, page_size=20)
            if not isinstance(data, dict) or not isinstance(data.get('result'), list):
                raise ValueError('搜索返回无效')
            rows.extend(data['result'][:20])
            pages = data.get('numPages')
            if not data['result'] or (type(pages) is int and page >= pages):
                break
        found = exact_videos(wanted_title, rows, author=wanted_author)
        if found:
            return found
    return []
