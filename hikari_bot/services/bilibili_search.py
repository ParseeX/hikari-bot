"""缺少原链接的小程序卡片按完整标题搜索；仅在隔离解析进程调用外部 API。"""
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    text = ' '.join(text.split())
    return re.sub(r'\s*([【】])\s*', r'\1', text)


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


def related_authors(title, rows, author=''):
    """相近标题只用来找到要检查的 UP 主，不能直接成为下载结果。"""
    owners = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid, owner, name = row.get('mid'), row.get('author'), row.get('title')
        if isinstance(uid, str) and uid.isascii() and uid.isdigit():
            uid = int(uid)
        if (type(uid) is not int or not 0 < uid < 2**53
                or not isinstance(owner, str) or not 1 <= len(owner.strip()) <= 80
                or not isinstance(name, str)):
            continue
        if author and search_text(owner) != search_text(author):
            continue
        score = SequenceMatcher(None, search_text(title), search_text(name, highlighted=True), autojunk=False).ratio()
        if not author and score < 0.8:
            continue
        if uid not in owners or owners[uid][0] < score:
            owners[uid] = (score, owner)
    return [(uid, owner) for uid, (_, owner) in sorted(owners.items(), key=lambda pair: -pair[1][0])[:3]]


async def search_author_uploads(title, rows, *, author='', query=None):
    from bilibili_api import user

    async def live(uid, *, pn, ps):
        return await user.User(uid).get_videos(pn=pn, ps=ps)

    query = query or live
    found = {}
    for uid, owner in related_authors(title, rows, author):
        for page in range(1, 3):
            data = await query(uid, pn=page, ps=50)
            videos = data.get('list', {}).get('vlist') if isinstance(data, dict) else None
            if not isinstance(videos, list):
                raise ValueError('作者投稿列表无效')
            candidates = []
            for row in videos[:50]:
                if not isinstance(row, dict):
                    continue
                # 投稿来源绑定 UID，若行内带 mid 也必须一致；不借用全站结果的作者名。
                if 'mid' in row and str(row['mid']) != str(uid):
                    continue
                candidates.append({**row, 'duration': row.get('length', row.get('duration', ''))})
            for item in exact_videos(title, candidates, author=owner, highlighted=False):
                found.setdefault(item['bvid'], item)
            count = data.get('page', {}).get('count')
            if len(videos) < 50 or (type(count) is int and page * 50 >= count):
                break
    return list(found.values())


async def search_title(title, *, author='', query=None, author_query=None):
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
    related = []
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
        related.extend(rows)
    return await search_author_uploads(wanted_title, related, author=wanted_author, query=author_query)
