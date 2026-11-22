"""官方命令调用共享查询；旧身份、管理权限与订阅不作推断。"""
from .output_templates import present, render_outputs
import asyncio
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from hikari_bot.services.platform import Result
from hikari_bot.services import mycard
from hikari_bot.services.card_catalog import AmbiguousCardName


HISTORY = re.compile(r"^(?:(\d{2,4})年)?(?:(1[0-2]|[1-9])月)?历史(?:\s+(.+))?$")


def binding_help(command=None):
    prompt = (present('binding.business_76230727', {'command': command}) if command
              else present('binding.business_9123be18', {}))
    note = present('binding.business_8dfa0c30', {})
    return Result(present('binding.business_body_96c257d5', {'content': prompt + note, 'prompt': prompt, 'migration_note': note}))


class Queries:
    def __init__(self, catalog, *, image_loader=None, ruling=None, deck_renderer=None, state=None, links=None, price_reports=None, admin=None, notifications=None, front_decks=None, daily_cards=None):
        self.catalog, self.image_loader = catalog, image_loader
        from hikari_bot.services.daily_cards import DailyCards
        self.daily_cards = daily_cards if daily_cards is not None else DailyCards(catalog, image_loader)
        self.ruling, self.deck_renderer = ruling, deck_renderer
        self.state = state
        self.links = links
        self.price_reports = price_reports
        self.admin = admin
        self.notifications = notifications
        from hikari_bot.services.mycard_front import FrontDecks
        self.front_decks = front_decks if front_decks is not None else FrontDecks()

    @render_outputs
    async def handle(self, request, command, argument):
        from hikari_bot.services.platform_state import BindingConflict
        try:
            return await self._handle(request, command, argument)
        except AmbiguousCardName as error:
            return Result(str(error))
        except BindingConflict:
            return Result(present('binding.conflict'))

    async def _handle(self, request, command, argument):
        if command == '前排卡组信息':
            from .front_decks import render_front_decks
            try:
                return Result(render_front_decks(await self.front_decks.top()))
            except Exception as error:
                from .preview import report
                report('front_decks_failed', error)
                return Result(present('front_decks.failed'))
        if request.shared_video is not None:
            if self.links is None:
                return None
            if self.notifications and not await asyncio.to_thread(self.notifications.bus.setting, request.actor.key, 'parser'):
                return Result()
            if not request.shared_video:
                if request.shared_video_title:
                    options = {'author': request.shared_video_author} if request.shared_video_author else {}
                    return await self.links.parse(request.shared_video_title, search_title=True, **options)
                return Result(present('links.shared_video_missing'))
            return await self.links.parse(request.shared_video)
        if command == '添加别名':
            from hikari_bot.services.card_aliases import add_alias_reply
            return Result(await add_alias_reply(self.catalog, argument, with_receipt=True))
        if command == '删除别名':
            from hikari_bot.services.card_aliases import delete_alias_reply
            return Result(await delete_alias_reply(self.catalog, argument))
        if self.notifications:
            result = await self.notifications.handle(request, command, argument)
            if result is not None:
                return result
        if self.admin:
            result = await self.admin.handle(request, command, argument)
            if result is not None:
                return result
        if self.price_reports:
            result = await self.price_reports.handle(request, command, argument)
            if result is not None:
                return result
        if command == '翻译' or re.fullmatch('[中繁英日韩法俄德]译[中繁英日韩法俄德]', command):
            from hikari_bot.services.translation import translate
            try:
                return Result(present('translation.business_body_1fa58867', {'content': await asyncio.wait_for(translate(command, argument), timeout=45)}))
            except Exception:
                return Result(present('translation.business_f10cf063', {}))
        if command in {"绑定", "查询绑定", "绑定查询", "订阅", "退订", "订阅状态"}:
            if self.state is None:
                return Result(present('business.business_9d5e49e4', {}))
            if command == "绑定":
                if not argument:
                    return Result(present('binding.business_60feb2f4', {}))
                await asyncio.to_thread(self.state.bind, request.actor, argument)
                return Result(present('binding.business_758d5462', {'player': argument}))
            if command in {"查询绑定", "绑定查询"}:
                if argument:
                    return Result(present('binding.business_fde84002', {}))
                player = await asyncio.to_thread(self.state.binding, request.actor)
                return Result(present('binding.business_4c0fc3fa', {'player': player})) if player else binding_help()
            if request.conversation.kind != "user":
                return Result(present('business.business_8390b6d1', {}))
            if command == "订阅状态":
                rows = await asyncio.to_thread(self.state.subscriptions, request.conversation)
                active = self.notifications and self.notifications.enabled and 'mycard' in await asyncio.to_thread(self.notifications.bus.topics, request.actor)
                label = present('subscriptions.business_e6359e07', {}) if active else present('subscriptions.business_1e88c9da', {})
                return Result(present('subscriptions.business_body_098ef8d9', {'content': "\n".join(present('subscriptions.row', {'player': player, 'label': label}) for player, _ in rows) or present('subscriptions.empty')}))
            if not argument:
                return Result(present('business.business_fd456534', {}))
            method = self.state.subscribe if command == "订阅" else self.state.unsubscribe
            await asyncio.to_thread(method, request.conversation, argument)
            if command == '订阅' and self.notifications and self.notifications.enabled:
                await asyncio.to_thread(self.notifications.bus.allow_topic, request.actor, 'mycard', True)
                return Result(present('subscriptions.business_2a804369', {}))
            return Result(present('business.business_c6e045fb', {}) if command == "订阅" else present('business.business_6b580d7a', {}))
        if command == "每日一卡":
            if self.catalog is None or self.image_loader is None:
                return Result(present('daily_card.business_489fa409', {}))
            data = await self.daily_cards.draw(request)
            return Result(images=(data,)) if data else Result(present('daily_card.business_68880f3d', {}))
        if command == "共界计算":
            if not argument:
                return Result(present('calculation.business_1d688354', {}))
            if self.catalog is None:
                return Result(present('calculation.business_4fde7d2f', {}))
            info = await asyncio.to_thread(self.catalog.search, argument, fuzzy=True)
            if not info:
                return Result(present('calculation.business_7502167d', {}))
            cards = await asyncio.to_thread(self.catalog.metaltronus, info["id"])
            if not cards:
                return Result(present('calculation.business_03a7ca05', {}))
            if self.deck_renderer:
                try:
                    return Result(text=present('common.card_name_hint', plain=True),
                                  images=(await self.deck_renderer.card_list(cards),))
                except ValueError:
                    return Result(present('calculation.business_be309d04', {}))
            heading, codes = present('calculation.business_2a9842bc', {}), "\n".join(map(str, cards))
            return Result(present('calculation.business_body_82946755', {'content': heading + codes, 'heading': heading, 'card_ids': codes}))
        if command in {"首胜查询", "首赢查询", "查分"}:
            if not argument and self.state:
                argument = await asyncio.to_thread(self.state.binding, request.actor) or ""
            if not argument:
                return binding_help(command)
            if command == "查分":
                info = await mycard.fetch_player_info(argument)
                if not isinstance(info, dict) or "pt" not in info:
                    return Result(present('score.business_b98c4ab7', {}))
                return Result(present('score.business_f71c3bbc', {'argument': argument, 'value2': info['pt'], 'value3': info.get('arena_rank', '暂无')}))
            data = await mycard._api_get("arena/firstwin", {"username": argument})
            if not isinstance(data, dict) or "today" not in data:
                return Result(present('score.business_b98c4ab7', {}))
            return Result(present('score.business_53cba006', {'argument': argument, 'value2': '已' if str(data['today']) == '1' else '还未'}))
        match = HISTORY.fullmatch(request.text.strip().removeprefix("/"))
        if match or command in {"胜率查询", "胜率统计"}:
            from hikari_bot.services.mycard_reports import history_report, winrate_report
            player = match[3] if match else argument
            if match and not player and (request.mentioned_members or request.mentions_everyone):
                if request.mentions_everyone or len(request.mentioned_members) != 1:
                    return Result(present('binding.history_one_member'))
                if self.state is None:
                    return Result(present('business.business_9d5e49e4'))
                from hikari_bot.services.platform_state import BindingConflict
                try:
                    player = await asyncio.to_thread(self.state.binding, request.mentioned_members[0])
                except BindingConflict:
                    return Result(present('binding.history_member_conflict'))
                if not player:
                    return Result(present('binding.history_member_unbound'))
            elif not player and self.state:
                player = await asyncio.to_thread(self.state.binding, request.actor)
            if not player:
                return binding_help(command)
            if not match:
                return await winrate_report(player)
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            month = int(match[2]) if match[2] else now.month
            year = int(match[1]) if match[1] else now.year - int(month > now.month)
            if year < 100:
                year += 2000
            if (year, month) > (now.year, now.month):
                return Result(present('winrate.business_1175a83f', {}))
            return await history_report(player, month, year)
        if command == "裁定查询":
            if not argument:
                return Result(present('ruling.business_b834c123', {}))
            if self.ruling is None:
                return Result(present('ruling.business_a3c7e067', {}))
            answer, note = await self.ruling(argument), present('ruling.business_ed09c431', {})
            return Result(present('ruling.business_body_4783ee60', {'content': answer + note, 'answer': answer, 'note': note}))
        if command in {"生成卡组图片", "卡组图片", "卡组", "卡组PDF"}:
            if not self.deck_renderer:
                return Result(present('deck.business_bdca74be', {}))
            return await self.deck_renderer.render(command, argument)
        if self.links:
            from hikari_bot.services.link_parser import LINK
            if command == 'ym':
                return Result(present('links.business_ba9cbce5', {}))
            if command == 'bm' or LINK.search(request.text):
                if self.notifications and not await asyncio.to_thread(self.notifications.bus.setting, request.actor.key, 'parser'):
                    return Result(present('links.business_d2ee8c4a', {})) if command == 'bm' else Result()
                return await self.links.parse(f'bm {argument}' if command == 'bm' else request.text)
        if request.attachments:
            return Result()
        return None
