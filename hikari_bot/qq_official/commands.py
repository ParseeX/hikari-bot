"""Small read-only command surface; no QQ or production configuration imports."""
from .output_templates import present, Output
import asyncio
from hikari_bot.services.card_text import format_card_effect
from hikari_bot.services.card_catalog import AmbiguousCardName
HELP = '官方 QQ 测试入口：ping、帮助、卡密 <卡名/卡密>、效果 <卡名/卡密>、卡表。长回复未完时发送“继续”。群内请 @机器人。'
DECK_URL = 'https://ygo.xyk.one/deck'

def parse_command(text: str):
    parts = text.strip().removeprefix('/').split(maxsplit=1)
    return (parts[0], parts[1].strip() if len(parts) > 1 else '') if parts else ('', '')

async def reply_for(text: str, catalog=None) -> str | None:
    command, argument = parse_command(text)
    command = {'查卡密': '卡密', '查效果': '效果'}.get(command, command)
    if command in {'帮助', 'help'}:
        base = present('common.help')
        return Output(str(base) + '\n常用命令：卡图、每日一卡、共界计算、卡价、日版价格查询、卡价曲线、卡价日报；绑定、查询绑定、查分、首胜查询、历史、胜率查询；裁定查询；卡组图片、卡组PDF；翻译、中译中或直接发送视频链接。\n订阅目前只保存到新库，通知暂停。比赛暂缓，管理功能尚未授权。QQ客户端验证尚未完成。', base.key, base.mode)
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
        info = await asyncio.wait_for(asyncio.to_thread(catalog.search, argument), timeout=8)
    except AmbiguousCardName as exc:
        return str(exc)
    except Exception:
        return present('common.commands_6', {})
    if not info:
        return present('common.commands_7', {})
    if '卡密' in command:
        return present('common.passcode',{'code':str(info.get('passcode') or info.get('id') or present('common.passcode_missing'))})
    return present('common.effect_plain',{'body':format_card_effect(info)})
