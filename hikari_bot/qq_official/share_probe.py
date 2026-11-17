"""按需启用的卡片结构诊断：只输出字段类型和地址类别，不输出字段值。"""
import json
from pathlib import Path
import time
from urllib.parse import unquote, urlsplit

from .shared_video import URL, video_url


MARKER = Path.home() / '.local/share/srbs/shared-video-probe-until'
KNOWN = frozenset('ark_data ark_type ark_name fields prompt tag tags source title desc preview '
                  'jump_url url raw meta detail_1 news music qqdocurl jumpUrl app appid app_id '
                  'path view config extra content msg_elements message_type'.split())
_samples = 0


def shape(event):
    budget = [80]

    def visit(obj, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 6:
            return {'truncated': True}
        if isinstance(obj, dict):
            # 仅输出固定字段名，防止动态字典键本身含有账号或凭据。
            out = {k: visit(v, depth + 1) for k, v in obj.items() if k in KNOWN}
            out['unknown_fields'] = len(set(obj) - KNOWN)
            return out
        if isinstance(obj, list):
            return [visit(v, depth + 1) for v in obj[:8]]
        if not isinstance(obj, str):
            return {'type': type(obj).__name__}
        out = {'type': 'str', 'nonempty': bool(obj)}
        if len(obj) > 16000:
            return {**out, 'too_long': True}
        out['video_url'] = bool(video_url(obj))
        out['encoded_video_url'] = bool(video_url(unquote(unquote(obj))))
        hosts = set()
        for match in URL.finditer(obj.replace(r'\/', '/')):
            try:
                host = (urlsplit(match[0]).hostname or '').lower()
                hosts.add('bilibili' if host in {'b23.tv', 'bili2233.cn', 'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'}
                          else 'qq' if host == 'qq.com' or host.endswith('.qq.com') else 'other')
            except ValueError:
                hosts.add('invalid')
        out['url_categories'] = sorted(hosts)
        if obj.lstrip().startswith(('{', '[')):
            try:
                out['json_shape'] = visit(json.loads(obj), depth + 1)
            except (ValueError, RecursionError):
                out['invalid_json'] = True
        return out

    # 从根部排除 author、message_scene、附件地址等不需要的身份和授权信息。
    return visit({k: event[k] for k in ('ark_data', 'content', 'msg_elements') if k in event})


def capture_missing_card(event):
    """默认关闭；运维标记最多开启一小时，每次进程最多记录三张失败卡片。"""
    global _samples
    if _samples >= 3:
        return
    try:
        with MARKER.open() as handle:
            expires = float(handle.read(64))
        if not 0 < expires - time.time() <= 3600:
            return
        report = shape(event)
    except (OSError, ValueError, TypeError, RecursionError):
        return
    _samples += 1
    print('SRBS_SHARE_SHAPE ' + json.dumps(report, sort_keys=True), flush=True)
