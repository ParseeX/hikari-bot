"""只读卡价曲线与日报；复用原来的选择规则、统计和图表。"""
from hikari_bot.services.wording import wording
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import time
from zoneinfo import ZoneInfo

from hikari_bot.features.cardrush.parsing import parse_price_query, expand_rarity_to_jp_list, rarity_jp_to_en
from hikari_bot.features.cardrush.reporting.text import format_daily_report, parse_date_arg
from .platform import Result


@dataclass(frozen=True)
class HistoryChoices(Result):
    candidates: tuple = ()


class PriceReports:
    def __init__(self, reader, catalog, *, clock=time.monotonic, selection_limit=10):
        self.reader, self.catalog, self.clock = reader, catalog, clock
        self.sessions = OrderedDict()
        self.selection_limit = selection_limit

    def active(self, session):
        value = self.sessions.get(session)
        return value is not None and value[0] > self.clock()

    async def exact_version(self, name, version):
        from hikari_bot.features.card_prices.service import matches_version
        from hikari_bot.features.card_prices.identity import cardrush_identity
        if not version.number:
            return []
        _, number = cardrush_identity(name, version)
        records = await self.reader.search_prices('', None, number, 200)
        return [record for record in records if matches_version(record, name, version)]

    async def handle(self, request, command, argument):
        now = self.clock()
        for key in [key for key, (expiry, _) in self.sessions.items() if expiry <= now]:
            del self.sessions[key]
        key = request.session
        if command == '卡价日报':
            today = datetime.now(ZoneInfo('Asia/Tokyo')).date()
            try:
                date = parse_date_arg(argument, today=today) if argument else today.isoformat()
            except ValueError as error:
                return Result(wording('price_daily.price_reports_body_d0bac120', '{content}', {'content': str(error)}))
            changes = await self.reader.get_daily_changes(date, exclude_prefixes=['RD/'])
            return Result(media=tuple(format_daily_report(changes, date)))
        if command in {'卡价曲线', '历史卡价', '卡价历史'}:
            self.sessions.pop(key, None)
            if not argument:
                return Result(wording('price_history.price_reports_9c855e85', '请输入卡片名称！例如：卡价曲线 青眼白龙', {}))
            name, rarity, prefix = parse_price_query(argument)
            if self.catalog:
                import asyncio
                info = await asyncio.to_thread(self.catalog.search, name)
                name = (info or {}).get('jp_name') or name
            return await self.select(request, name, rarity, prefix)
        if key not in self.sessions:
            return None
        if command in {'取消', '退出', 'cancel'}:
            del self.sessions[key]
            return Result(wording('price_history.price_reports_f3bd1277', '已取消卡价曲线查询。', {}))
        if not argument and command.isascii() and command.isdigit() and len(command) <= 3:
            candidates = self.sessions[key][1]
            index = int(command) - 1
            if not 0 <= index < len(candidates):
                return Result(wording('price_history.price_reports_13b68db8', '请输入 1-{value1} 之间的编号：', {'value1': len(candidates)}))
            del self.sessions[key]
            return await self.draw(candidates[index])
        # 新命令结束旧的曲线选择，避免截获其他业务的编号。
        self.sessions.pop(key, None)
        return None

    async def select(self, request, name, rarity=None, prefix=None, *, force_choice=False, exact_rarity=False):
        """直接查询 Cardrush 候选；不依赖其他站点的商品列表或版本编号。"""
        from hikari_bot.features.cardrush.parsing import RARITY_MAPPING
        key, now = request.session, self.clock()
        self.sessions.pop(key, None)
        rarities = expand_rarity_to_jp_list(rarity) if rarity else None
        if rarity and exact_rarity:
            rarities = [jp for jp, en in RARITY_MAPPING.items() if en.upper() == rarity.upper()]
        candidates = await self.reader.search_prices(name, rarities, prefix, self.selection_limit + 1)
        if not candidates:
            return Result(wording('price_history.price_reports_4c833bfa', '暂无 {name} 的价格历史记录。', {'name': name}))
        if len(candidates) > self.selection_limit:
            return Result(wording('price_history.price_reports_96e6379b', '找到超过{limit}条匹配结果，请附加稀有度（如 UR）或盒子编号（如 ALIN）缩小范围。', {'limit': self.selection_limit}))
        if len(candidates) == 1 and not force_choice:
            return await self.draw(candidates[0])
        self.sessions[key] = (now + 180, candidates)
        while len(self.sessions) > 128:
            self.sessions.popitem(last=False)
        lines = '\n'.join(wording('price_history.candidate', '{index}. {name}  {box}-{rarity}  {price}円',
            {'index': i, 'name': item.name, 'box': (item.model_number or '').split('-')[0],
             'rarity': rarity_jp_to_en(item.rarity or ''), 'price': f'{item.price:,}'}) for i, item in enumerate(candidates, 1))
        known_rarities = set(rarity_jp_to_en(item.rarity or '') for item in candidates)
        rarity_heading = ' | '+next(iter(known_rarities)) if len(known_rarities) == 1 else ''
        return HistoryChoices(wording('price_history.price_reports_47b24a67', '找到 {value1} 条结果，请回复编号：\n{lines}', {'value1': len(candidates), 'lines': lines, 'name': name, 'rarity_heading': rarity_heading}), candidates=tuple(candidates))

    async def draw(self, selected):
        from hikari_bot.features.cardrush.reporting.chart import draw_price_chart
        history = await self.reader.get_price_history(selected.product_id)
        if not history:
            return Result(wording('price_history.price_reports_4ce37f7d', '暂无该卡片的历史价格记录。', {}))
        image = draw_price_chart(history)
        if not image:
            return Result(wording('price_history.price_reports_3c320fec', '绘制图表失败。', {}))
        box = selected.model_number.split('-')[0] if selected.model_number else '未知'
        text = (wording('price_history.price_reports_8de07ef2', '{value1}  {box}-{value3}\n当前买取价：{value4}円（共 {value5} 条记录）', {'value1': selected.name, 'box': box, 'version': selected.model_number or '编号未知', 'value3': rarity_jp_to_en(selected.rarity or ''), 'value4': f'{history[-1].price:,}', 'value5': len(history)}))
        return Result(wording('price_history.price_reports_body_372ea08c', '{content}', {'content': text}), (image,))
