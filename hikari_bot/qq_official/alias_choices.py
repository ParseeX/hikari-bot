"""同名别名的查询选择；只生成只读查询按钮，不修改别名或业务状态。"""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import secrets
import time

from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.models import MessageKeyboard

from .card_actions import command_button, stable_code
from .delivery import context
from .preview import report


PROMPT = '该别名对应多张卡片，请通过下方按钮进行选择。'
PAGE_SIZE = 8
COMMANDS = {
    **dict.fromkeys(('效果', '效果查询', '游戏王效果', '查效果'), '效果'),
    **dict.fromkeys(('卡图', '卡图查询', '游戏王卡图', '查卡图'), '卡图'),
    **dict.fromkeys(('卡密', '卡密查询', '游戏王卡密', '查卡密'), '卡密'),
    **dict.fromkeys(('卡价', '卡价查询', '价格查询'), '卡价'),
    **dict.fromkeys(('日版价格查询', '日版卡价查询'), '日版价格查询'),
    '共界计算': '共界计算',
}


@dataclass
class Selection:
    session: tuple
    choices: tuple
    expires: float


class AliasChoices:
    def __init__(self, catalog, *, clock=time.monotonic):
        self.catalog, self.clock = catalog, clock
        self.pages = OrderedDict()

    async def handle(self, bot, event, command, argument):
        now = self.clock()
        for key in [k for k, v in self.pages.items() if v.expires <= now]:
            self.pages.pop(key, None)
        if command == '候选页':
            parts = argument.split()
            selection = self.pages.get(parts[0]) if len(parts) == 2 else None
            if (selection is None or selection.session != context(bot, event)
                    or not parts[1].isascii() or not parts[1].isdigit()):
                await bot.send(event, '选择按钮已失效，请重新查询。')
                return True
            index = int(parts[1])
            if index * PAGE_SIZE >= len(selection.choices):
                await bot.send(event, '选择按钮已失效，请重新查询。')
                return True
            await self.send(bot, event, parts[0], selection, index)
            return True
        canonical = COMMANDS.get(command)
        if not canonical or self.catalog is None or not argument or len(argument) > 200:
            return False
        query, suffix = argument, ''
        if canonical in {'卡价', '日版价格查询'}:
            from hikari_bot.features.cardrush.parsing import parse_price_query
            query, rarity, prefix = parse_price_query(argument)
            suffix = ''.join(' '+value for value in (rarity, prefix) if value)
        cards = await asyncio.wait_for(asyncio.to_thread(self.catalog.alias_targets, query), timeout=8)
        if len(cards) < 2:
            return False
        choices = []
        for card in cards:
            code = stable_code(card)
            if code is None:
                await bot.send(event, '候选卡片缺少有效卡密，暂时无法生成选择按钮。')
                return True
            name = card.get('cn_name') or card.get('jp_name') or card.get('en_name') or code
            choices.append((name, f'{canonical} {code}{suffix}'))
        key = secrets.token_urlsafe(18)
        selection = Selection(context(bot, event), tuple(choices), now + 600)
        self.pages[key] = selection
        while len(self.pages) > 128:
            self.pages.popitem(last=False)
        await self.send(bot, event, key, selection, 0)
        return True

    async def send(self, bot, event, key, selection, index):
        start = index * PAGE_SIZE
        buttons = [command_button(f'alias_{start+i}', name, command, event.get_user_id())
                   for i, (name, command) in enumerate(selection.choices[start:start+PAGE_SIZE])]
        rows = [{'buttons': buttons[i:i+2]} for i in range(0, len(buttons), 2)]
        navigation = []
        for available, step, label in ((index > 0, -1, '上一页'),
                                       (start+PAGE_SIZE < len(selection.choices), 1, '下一页')):
            if available:
                navigation.append(command_button(f'alias_page_{index+step}', label,
                    f'候选页 {key} {index+step}', event.get_user_id()))
        if navigation:
            rows.append({'buttons': navigation})
        message = Message(MessageSegment.markdown(PROMPT))
        message += MessageSegment.keyboard(MessageKeyboard.model_validate({'content': {'rows': rows}}))
        try:
            await asyncio.wait_for(bot.send(event, message), timeout=20)
        except Exception as error:
            report('alias_buttons_failed', error)
            await bot.send(event, '选择按钮发送未成功，请重新查询。')
