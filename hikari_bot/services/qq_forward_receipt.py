"""识别 NapCat 已上传资源但未确认消息发送的异常。"""
import re


def forward_resource(error):
    info = getattr(error, 'info', {})
    if not isinstance(info, dict):
        return None
    for field in ('message', 'wording'):
        match = re.search(r'发送转发消息（res_id[：:]([A-Za-z0-9+/=_-]+)\s+失败', str(info.get(field, '')))
        if match:
            return match.group(1)
    return None
