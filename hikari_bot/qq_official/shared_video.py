"""从官方卡片提取视频身份；缺少地址时仅将来源明确的标题/作者交给搜索。"""
from html import unescape
import json
import re
from urllib.parse import urlsplit, unquote, parse_qs


URL = re.compile(r'https?://[^\s<>"\[\]{}]+', re.I)
BILI_VIDEO = re.compile(r'/(?:video/)?(?:BV[0-9A-Za-z]{10}|av\d+)(?:/|$)')
SOURCES = ('哔哩哔哩', 'bilibili', 'b站')
BV = re.compile(r'BV[0-9A-Za-z]{10}')


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def video_url(text):
    if not isinstance(text, str) or len(text) > 16000:
        return None
    text = unescape(text.replace(r'\/', '/'))
    for match in URL.finditer(text):
        url = match[0].rstrip(').,，。；;！!）')
        try:
            parts = urlsplit(url)
            if parts.username or parts.password or parts.port not in (None, 80, 443):
                continue
        except ValueError:
            continue
        host = (parts.hostname or '').lower()
        if host in {'b23.tv', 'bili2233.cn'} and re.fullmatch(r'/[\w-]+/?', parts.path):
            return url
        if host in {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'} and BILI_VIDEO.match(parts.path):
            return url
    return None


def legacy_video(card):
    """复用旧 parser 2.6.6 的 meta 取址规则，不加载旧入口的事件/定时任务。

    只有载荷实际保留 meta/raw 时才有效；不从标题搜索，也不伪造缺失字段。
    """
    for container in (card, value(card, 'fields', {})):
        candidates = [container]
        raw = value(container, 'raw')
        if isinstance(raw, str) and len(raw) <= 16000:
            try:
                candidates.append(json.loads(raw))
            except (ValueError, RecursionError):
                pass
        for candidate in candidates:
            meta = value(candidate, 'meta', {})
            for section, key in (('detail_1', 'qqdocurl'), ('news', 'jumpUrl'), ('music', 'jumpUrl')):
                detail = value(meta, section)
                if detail:
                    # 与旧插件一致，优先使用最先出现的卡片类型。
                    url = video_url(value(detail, key))
                    if url:
                        return url
                    break
    return None


def card_nodes(card):
    """只展开卡片元数据；不访问聊天发送者，不扫描封面图片或任意正文。"""
    queue = [(card, 0)]
    visited = 0
    while queue and visited < 64:
        node, depth = queue.pop(0)
        visited += 1
        if isinstance(node, str):
            if len(node) > 16000:
                continue
            try:
                node = json.loads(node)
            except (ValueError, RecursionError):
                continue
        if not isinstance(node, dict):
            if hasattr(node, 'model_dump'):
                node = node.model_dump()
            else:
                continue
        yield node
        if depth < 4:
            for key in ('fields', 'raw', 'meta', 'detail_1', 'news', 'music', 'extra', 'data'):
                child = node.get(key)
                if isinstance(child, (str, dict)) and len(queue) + visited < 64:
                    queue.append((child, depth + 1))


def card_video(card):
    for node in card_nodes(card):
        for key in ('bvid', 'bv_id', 'video_id'):
            bvid = node.get(key)
            if isinstance(bvid, str) and BV.fullmatch(bvid):
                return 'https://www.bilibili.com/video/' + bvid
        for key in ('jump_url', 'url', 'qqdocurl', 'jumpUrl', 'path', 'pagepath', 'page_path'):
            raw = node.get(key)
            if not isinstance(raw, str) or len(raw) > 16000:
                continue
            text = unquote(unquote(unescape(raw)))
            if url := video_url(text):
                return url
            # 小程序内部路径可携带 bvid；外部网站 query 中的 BV 不作为来源。
            if key in {'path', 'pagepath', 'page_path'} and re.match(r'^/?pages/', text):
                values = parse_qs(urlsplit(text).query).get('bvid', [])
                if len(values) == 1 and BV.fullmatch(values[0]):
                    return 'https://www.bilibili.com/video/' + values[0]
    return None


def card_author(card):
    for node in card_nodes(card):
        for key in ('author_name', 'up_name', 'upname', 'uploader', 'author', 'owner'):
            author = node.get(key)
            if isinstance(author, dict):
                author = author.get('name') or author.get('uname')
            if isinstance(author, str) and 1 <= len(author.strip()) <= 80:
                return unescape(author).strip()
    return ''


def shared_metadata(event):
    """只取 B 站来源卡片的标题，普通正文和其他应用不触发标题搜索。"""
    queue = [(event, 0)]
    visited = 0
    while queue and visited < 64:
        node, depth = queue.pop(0)
        visited += 1
        if value(node, 'message_type') == 101:
            continue
        fields = value(value(node, 'ark_data'), 'fields', {})
        source = ' '.join(str(value(fields, k, ''))[:256] for k in ('tag', 'tags', 'source')).lower()
        title = value(fields, 'title')
        if any(name in source for name in SOURCES) and isinstance(title, str) and 1 <= len(title.strip()) <= 200:
            return title.strip(), card_author(value(node, 'ark_data'))
        children = value(node, 'msg_elements')
        if depth < 4 and isinstance(children, (list, tuple)):
            queue.extend((child, depth + 1) for child in children[:64 - visited - len(queue)])
    return '', ''


def shared_title(event):
    return shared_metadata(event)[0]


def shared_author(event):
    return shared_metadata(event)[1]


def shared_video(event):
    """None 为无相关分享；空串为 B 站卡片未提供地址；非空为首个视频地址。

    QQ 官方的 ark_data.fields.jump_url / msg_elements 与普通正文分开。
    只提取卡片/消息元素中的视频地址，不展开并行消息（101），限制深度/数量。
    """
    top_card = value(event, 'ark_data')
    if not top_card and value(event, 'message_type') not in {3, 102, 103}:
        return None
    queue = [(event, 0)]
    missing = False
    visited = 0
    while queue and visited < 64:
        node, depth = queue.pop(0)
        visited += 1
        if value(node, 'message_type') == 101:
            continue
        card = value(node, 'ark_data')
        if card:
            fields = value(card, 'fields', {})
            url = video_url(value(fields, 'jump_url')) or legacy_video(card)
            if url:
                return url
            # 不凭标题猜视频；来源标签仅用于解释缺失的地址。
            source = ' '.join(str(value(fields, k, ''))[:256] for k in ('tag', 'tags', 'source')).lower()
            if any(name in source for name in SOURCES):
                if url := card_video(card):
                    return url
                missing = True
        url = video_url(value(node, 'content'))
        if url:
            return url
        if depth < 4:
            children = value(node, 'msg_elements')
            if isinstance(children, (list, tuple)):
                queue.extend((child, depth + 1) for child in children[:64 - visited - len(queue)])
    return '' if missing else None
