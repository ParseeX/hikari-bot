"""从官方卡片/转发消息中取 B 站地址，不把卡片标题当作命令或搜索词。"""
from html import unescape
import json
import re
from urllib.parse import urlsplit


URL = re.compile(r'https?://[^\s<>"\[\]{}]+', re.I)
BILI_VIDEO = re.compile(r'/(?:video/)?(?:BV[0-9A-Za-z]{10}|av\d+)(?:/|$)')
SOURCES = ('哔哩哔哩', 'bilibili', 'b站')


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


def shared_title(event):
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
            return title.strip()
        children = value(node, 'msg_elements')
        if depth < 4 and isinstance(children, (list, tuple)):
            queue.extend((child, depth + 1) for child in children[:64 - visited - len(queue)])
    return ''


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
            missing |= any(name in source for name in SOURCES)
        url = video_url(value(node, 'content'))
        if url:
            return url
        if depth < 4:
            children = value(node, 'msg_elements')
            if isinstance(children, (list, tuple)):
                queue.extend((child, depth + 1) for child in children[:64 - visited - len(queue)])
    return '' if missing else None
