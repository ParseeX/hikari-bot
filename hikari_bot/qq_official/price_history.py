"""报价到同版本曲线，以及曲线候选按钮；目标保存在原会话内存中。"""
from collections import OrderedDict
from dataclasses import dataclass
import secrets
import time

from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import MessageKeyboard

from hikari_bot.features.cardrush.parsing import rarity_jp_to_en
from hikari_bot.services.platform import Result
from hikari_bot.services.wording import using_wording
from .alias_choices import AliasChoices
from .card_actions import command_button
from .delivery import context
from .output_templates import present, escape


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
        if self.reports is None or not rows or quote.mode != 'markdown':
            return None
        command = self.bind(bot, event, 'versions', [row.version for row in rows], quote.name, shared=True)
        button = command_button('price_history', '卡价曲线', command, event.get_user_id(), shared=True)
        return MessageSegment.keyboard(MessageKeyboard.model_validate({'content': {'rows': [{'buttons': [button]}]}}))

    async def offer(self, bot, event, items, *, versions=False, name='', prompt=None):
        items = tuple(items)
        labels, details, choices = [], [], []
        for index, item in enumerate(items, 1):
            rarity = item.rarity if versions else rarity_jp_to_en(item.rarity or '')
            number = item.number if versions else item.model_number
            label = f'{rarity} · {number or "编号未知"}'
            labels.append(label)
            detail = f'{index}. {name if versions else item.name} · {label}'
            if not versions:
                detail += f' · {item.price:,} 円'
            details.append(escape(detail))
            command = self.bind(bot, event, 'versions' if versions else 'product', (item,), name)
            choices.append((label[:24], command))
        # 同名版本或过长标签保留完整详情，避免看不出按钮对应哪一项。
        show_details = (len(set(labels)) != len(labels) or any(len(label) > 24 for label in labels)
                        or (not versions and len({item.name for item in items}) > 1))
        await self.pages.offer(bot, event, choices,
            prompt=str(prompt or present('price_history.version_prompt', {'name': name or items[0].name})),
            details=details if show_details else ())

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
            if target.kind == 'versions':
                if len(target.items) > 1:
                    await self.offer(bot, event, target.items, versions=True, name=target.name)
                    return True
                products = await self.reports.exact_version(target.name, target.items[0])
                if not products:
                    await self.results.send(bot, event, Result(present('price_history.version_missing')))
                    return True
                if len(products) > 1:
                    await self.offer(bot, event, products, name=target.name)
                    return True
                selected = products[0]
            else:
                selected = target.items[0]
            await self.results.send(bot, event, await self.reports.draw(selected))
        return True
