"""Small read-only command surface; no QQ or production configuration imports."""
from .output_templates import present, Output
import asyncio
import re
from hikari_bot.services.card_catalog import AmbiguousCardName
HELP = '官方 QQ 测试入口：ping、帮助、卡密 <卡名/卡密>、效果 <卡名/卡密>、卡表。长回复未完时发送“继续”。已接入群开启全量消息后可直接发送命令。'
DECK_URL = 'https://ygo.xyk.one/deck'

def parse_command(text: str):
    text = text.strip().removeprefix('/').lstrip()
    # 旧版这两个无参数命令允许直接接文字；其他命令仍要求空格分隔。
    for command in ('每日一卡', '随机一卡'):
        if text.startswith(command):
            return command, text[len(command):].strip()
    parts = text.split(maxsplit=1)
    return (parts[0], parts[1].strip() if len(parts) > 1 else '') if parts else ('', '')


def is_group_request(text: str, *, price_active=False):
    """全量消息只唤起既有命令/链接；闲聊不占去重和续页状态。"""
    from .availability import unavailable_reply
    from .management import Management
    from hikari_bot.services.official_control import CONTROL_COMMANDS
    from hikari_bot.services.link_parser import LINK
    command, argument = parse_command(text)
    known = {
        '帮助', 'help', '继续', '输出预览', '样式预览',
        '卡表', '中文卡表', '简中卡表', '日文卡表', '英文卡表',
        '卡密', '卡密查询', '游戏王卡密', '查卡密',
        '添加别名', '删除别名',
        '效果', '效果查询', '游戏王效果', '查效果',
        '卡图', '卡图查询', '游戏王卡图', '查卡图',
        '卡价', '卡价查询', '价格查询', '日版价格查询', '日版卡价查询',
        '每日一卡', '共界计算', '查分', '首胜查询', '首赢查询',
        '胜率查询', '胜率统计', '裁定查询',
        '生成卡组图片', '卡组图片', '卡组', '卡组PDF',
        '订阅状态', '开启通知', '关闭通知', '通知状态', '测试通知',
    }
    return bool(
        command in known or command.lower() == 'ping'
        or command in CONTROL_COMMANDS or command in Management.COMMANDS
        or re.fullmatch(r'(?:(?:\d{2,4})年)?(?:(?:1[0-2]|[1-9])月)?历史', command)
        or LINK.search(text) or unavailable_reply(command, text) is not None
        or (price_active and not argument and re.fullmatch(r'(?:[0-9]{1,3}|[A-Za-z+]{1,12}|简中[A-Za-z0-9+]{1,12}|取消|退出)', command))
    )

async def reply_for(text: str, catalog=None) -> str | None:
    command, argument = parse_command(text)
    command = {'查卡密': '卡密', '查效果': '效果'}.get(command, command)
    if command in {'帮助', 'help'}:
        base = present('common.help')
        return Output(str(base) + '\n常用命令：卡图、每日一卡、共界计算、卡价、日版价格查询、卡价曲线、卡价日报；绑定、查询绑定、查分、首胜查询、历史、胜率查询；裁定查询；卡组图片、卡组PDF；翻译、中译中或直接发送视频链接。\n通知：订阅、退订、订阅状态、开启通知、关闭通知、通知状态。管理命令需单独核验的管理员私聊身份。比赛暂缓；各项收发状态以实际验证为准。', base.key, base.mode)
    if command.lower() == 'ping':
        return present('common.commands_1', {})
    if command in {'卡表', '中文卡表', '简中卡表', '日文卡表', '英文卡表'}:
        return present('common.commands_2', {'value1': f'{DECK_URL}'})
    if command not in {'卡密', '卡密查询', '游戏王卡密', '效果', '效果查询', '游戏王效果'}:
        return None
    if not argument:
        return present('common.commands_3', {'value1': f'{command}'})
    if len(argument) > 200:
        return present('common.commands_4', {})
    if catalog is None:
        return present('common.commands_5', {})
    try:
        info = await asyncio.wait_for(asyncio.to_thread(catalog.search, argument, fuzzy=True), timeout=8)
    except AmbiguousCardName as exc:
        return str(exc)
    except Exception:
        return present('common.commands_6', {})
    if not info:
        return present('common.commands_7', {})
    if '卡密' in command:
        return present('common.passcode',{'code':str(info.get('passcode') or info.get('id') or present('common.passcode_missing'))})
    from .card_actions import card_text
    return card_text(info)
