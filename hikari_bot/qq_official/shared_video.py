"""从官方卡片/转发消息中取 B 站地址，不把卡片标题当作命令或搜索词。"""
from html import unescape
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


def shared_video(event):
    """None 为无相关分享；空串为 B 站卡片未提供地址；非空为首个视频地址。

    QQ 官方的 ark_data.fields.jump_url / msg_elements 与普通正文分开。
    只遍历转发内容，忽略引用消息（101），限制深度/数量，不访问封面链接。
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
            url = video_url(value(fields, 'jump_url'))
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
