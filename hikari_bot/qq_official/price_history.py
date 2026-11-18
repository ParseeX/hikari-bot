"""按 Cardrush 自身商品选择曲线；目标保存在原会话内存中。"""
from collections import OrderedDict
from dataclasses import dataclass
import secrets
import time

from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import MessageKeyboard

from hikari_bot.features.cardrush.parsing import rarity_jp_to_en
from hikari_bot.services.price_reports import HistoryChoices
from hikari_bot.services.wording import using_wording
from .alias_choices import AliasChoices
from .card_actions import command_button
from .delivery import context
from .output_templates import present


@dataclass(frozen=True)
class Target:
    scope: tuple
    owner: str | None
    expires: float
    kind: str
    items: tuple
    name: str


class HistoryButtons:
    def __init__(self, reports, results, *, clock=time.monotonic):
        self.reports, self.results, self.clock = reports, results, clock
        self.targets = OrderedDict()
        self.pages = AliasChoices(None, clock=clock, page_command='曲线页')

    def bind(self, bot, event, kind, items, name='', *, shared=False):
        key = context(bot, event)
        scope = key[:3] if key[1] == 'group' else key
        owner = None if shared and key[1] == 'group' else event.get_user_id()
        now = self.clock()
        for token in [k for k, v in self.targets.items() if v.expires <= now]:
            self.targets.pop(token, None)
        token = secrets.token_urlsafe(18)
        self.targets[token] = Target(scope, owner, now + 600, kind, tuple(items), name)
        while len(self.targets) > 2048:
            self.targets.popitem(last=False)
        return '卡价曲线图 ' + token

    def quote_keyboard(self, bot, event, quote):
        rows = getattr(quote, 'comparisons', ())
        if (self.reports is None or not rows or quote.mode != 'markdown'
                or any(row.version.game_sub_key == 'sc' for row in rows)):
            return None
        # 仅携带卡名和所选罕贵度，不把集换社版本列表当作 Cardrush 候选。
        command = self.bind(bot, event, 'search', (rows[0].version.rarity,), quote.name, shared=True)
        button = command_button('price_history', '卡价曲线', command, event.get_user_id(), shared=True)
        return MessageSegment.keyboard(MessageKeyboard.model_validate({'content': {'rows': [{'buttons': [button]}]}}))

    async def offer(self, bot, event, items, *, prompt):
        items = tuple(items)
        mixed_rarities = len({rarity_jp_to_en(item.rarity or '') for item in items}) > 1
        details, choices = [], []
        for index, item in enumerate(items, 1):
            rarity = rarity_jp_to_en(item.rarity or '')
            number = item.model_number or '编号未知'
            label = f'{index}. {number}'
            details.append(str(present('price_history.choice_detail', {'index': index,
                'name': item.name, 'rarity': rarity, 'number': number, 'price': f'{item.price:,}',
                'rarity_suffix': f' | {rarity}' if mixed_rarities else ''})))
            command = self.bind(bot, event, 'product', (item,))
            choices.append((label[:24], command))
        await self.pages.offer(bot, event, choices,
            prompt=str(prompt), details=details)

    async def handle(self, bot, event, request, command, argument):
        if command not in {'卡价曲线图', '曲线页'}:
            return False
        if self.reports is None or getattr(bot, 'interaction', None) is None:
            return True
        if command == '曲线页':
            return await self.pages.handle(bot, event, command, argument)
        target = self.targets.get(argument)
        key = context(bot, event)
        scope = key[:3] if key[1] == 'group' else key
        if (target is None or target.expires <= self.clock() or target.scope != scope
                or (target.owner is not None and target.owner != event.get_user_id())):
            return True
        self.reports.sessions.pop(request.session, None)
        with using_wording(present):
            if target.kind == 'search':
                result = await self.reports.select(request, target.name, target.items[0],
                    force_choice=True, exact_rarity=True)
                if isinstance(result, HistoryChoices) and result.text.mode == 'markdown':
                    await self.offer(bot, event, result.candidates, prompt=result.text)
                else:
                    await self.results.send(bot, event, result)
            else:
                await self.results.send(bot, event, await self.reports.draw(target.items[0]))
        return True
