"""核对 NapCat 合并转发的发送回执异常，不通过重发猜测是否成功。"""
import json
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


def confirmed_in_history(messages, self_id, resource, since):
    for message in messages:
        if not isinstance(message, dict):
            continue
        try:
            # real_seq 是 QQ 群/私聊的消息序号，message_seq 在 NapCat 中可能只是本地短 ID。
            if (str(message.get('sender', {}).get('user_id')) != str(self_id)
                    or int(message.get('real_seq', 0)) <= 0
                    or float(message.get('time', 0)) < since - 5):
                continue
            for segment in message.get('message', []):
                if not isinstance(segment, dict) or segment.get('type') != 'json':
                    continue
                card = json.loads(segment.get('data', {}).get('data', ''))
                if (card.get('app') == 'com.tencent.multimsg'
                        and card.get('meta', {}).get('detail', {}).get('resid') == resource):
                    return True
        except (TypeError, ValueError, AttributeError):
            continue
    return False


async def confirm_forward(bot, *, resource, since, group_id=None, user_id=None):
    try:
        if group_id is not None:
            result = await bot.call_api('get_group_msg_history', group_id=group_id, count=50)
        else:
            result = await bot.call_api('get_friend_msg_history', user_id=user_id, count=50)
        return confirmed_in_history(result.get('messages', []), bot.self_id, resource, since)
    except Exception:
        return False
