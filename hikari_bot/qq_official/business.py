"""官方命令调用共享查询；旧身份、管理权限与订阅不作推断。"""
import asyncio
from datetime import datetime
import hashlib
import re
from zoneinfo import ZoneInfo

from hikari_bot.services.platform import Result
from hikari_bot.services import mycard


HISTORY = re.compile(r"^(?:(\d{2,4})年)?(?:(1[0-2]|[1-9])月)?历史(?:\s+(.+))?$")


class Queries:
    def __init__(self, catalog, *, image_loader=None, ruling=None, deck_renderer=None, state=None, links=None, price_reports=None, admin=None):
        self.catalog, self.image_loader = catalog, image_loader
        self.ruling, self.deck_renderer = ruling, deck_renderer
        self.state = state
        self.links = links
        self.price_reports = price_reports
        self.admin = admin

    async def handle(self, request, command, argument):
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
                return Result(await asyncio.wait_for(translate(command, argument), timeout=45))
            except Exception:
                return Result('翻译失败，请稍后再试。')
        if command in {"绑定", "查询绑定", "绑定查询", "订阅", "退订", "订阅状态"}:
            if self.state is None:
                return Result("官方入口独立状态库尚未配置，未修改旧记录。")
            if command == "绑定":
                if not argument:
                    return Result("请提供要绑定的用户名！")
                await asyncio.to_thread(self.state.bind, request.actor, argument)
                return Result("绑定成功！此绑定仅用于当前官方身份，旧 QQ 记录保留。")
            if command in {"查询绑定", "绑定查询"}:
                if argument:
                    return Result("官方入口只查询自己的绑定，不能从 MyCard 用户名反查旧 QQ 号。")
                player = await asyncio.to_thread(self.state.binding, request.actor)
                return Result(f"你绑定的 MyCard 用户名为：{player}" if player else "你还未绑定 MyCard 用户名！")
            if request.conversation.kind != "user":
                return Result("群订阅尚未启用；需单独核验目标群和群内授权。未修改订阅。")
            if command == "订阅状态":
                rows = await asyncio.to_thread(self.state.subscriptions, request.conversation)
                return Result("\n".join(f"{player}：暂停，等待主动通知验证" for player, _ in rows) or "当前官方身份暂无订阅。")
            if not argument:
                return Result("请提供 MyCard 用户名！")
            method = self.state.subscribe if command == "订阅" else self.state.unsubscribe
            await asyncio.to_thread(method, request.conversation, argument)
            return Result("已保存订阅，但通知暂停，需完成主动发送验证后启用。" if command == "订阅" else "已取消当前官方身份的这项订阅；旧 QQ 订阅保持不变。")
        if command == "每日一卡":
            if self.catalog is None or self.image_loader is None:
                return Result("每日一卡尚未配置卡片资料库或图片服务。")
            today = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
            seed = int.from_bytes(hashlib.sha256(f"{request.actor.key}_{today}".encode()).digest()[:8], "big")
            code = await asyncio.to_thread(self.catalog.random_id, seed)
            data = await self.image_loader(code)
            return Result(images=(data,)) if data else Result("未找到每日卡片！")
        if command == "共界计算":
            if not argument:
                return Result("用法：共界计算 卡名")
            if self.catalog is None:
                return Result("尚未配置卡片资料库。")
            info = await asyncio.to_thread(self.catalog.search, argument)
            if not info:
                return Result("未找到对应卡片！")
            cards = await asyncio.to_thread(self.catalog.metaltronus, info["id"])
            if not cards:
                return Result("没有满足条件的卡片！")
            if self.deck_renderer:
                try:
                    return Result(images=(await self.deck_renderer.card_list(cards),))
                except ValueError:
                    return Result('部分卡图暂时无法获取，请稍后重试；未发送缺卡的计算结果。')
            return Result("计算结果（图片生成器尚未配置）：\n" + "\n".join(map(str, cards)))
        if command in {"首胜查询", "首赢查询", "查分"}:
            if not argument and self.state:
                argument = await asyncio.to_thread(self.state.binding, request.actor) or ""
            if not argument:
                return Result("请提供 MyCard 用户名；旧绑定尚未经过身份关联。")
            if command == "查分":
                info = await mycard.fetch_player_info(argument)
                if not isinstance(info, dict) or "pt" not in info:
                    return Result("查询失败，请稍后重试")
                return Result(f"玩家：{argument}\n当前分数：{info['pt']}\n当前排名：{info.get('arena_rank', '暂无')}")
            data = await mycard._api_get("arena/firstwin", {"username": argument})
            if not isinstance(data, dict) or "today" not in data:
                return Result("查询失败，请稍后重试")
            return Result(f"{argument}{'已' if str(data['today']) == '1' else '还未'}完成今日首赢！")
        match = HISTORY.fullmatch(request.text.strip().removeprefix("/"))
        if match or command in {"胜率查询", "胜率统计"}:
            from hikari_bot.services.mycard_reports import history_report, winrate_report
            player = match[3] if match else argument
            if not player and self.state:
                player = await asyncio.to_thread(self.state.binding, request.actor)
            if not player:
                return Result("请先提供 MyCard 用户名；旧绑定尚未经过身份关联。")
            if not match:
                return await winrate_report(player)
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            month = int(match[2]) if match[2] else now.month
            year = int(match[1]) if match[1] else now.year - int(month > now.month)
            if year < 100:
                year += 2000
            if (year, month) > (now.year, now.month):
                return Result("不能查询未来月份。")
            return await history_report(player, month, year)
        if command == "裁定查询":
            if not argument:
                return Result("请在“裁定查询”后输入需要裁定的内容。")
            if self.ruling is None:
                return Result("裁定服务尚未为官方入口配置。")
            return Result(await self.ruling(argument) + "\n[非官方裁定，仅供参考]")
        if command in {"生成卡组图片", "卡组图片", "卡组", "卡组PDF"}:
            if not self.deck_renderer:
                return Result("官方入口的卡组媒体服务尚未配置，请使用 https://ygo.xyk.one/deck")
            return await self.deck_renderer.render(command, argument)
        if self.links:
            from hikari_bot.services.link_parser import LINK
            if command == 'ym':
                return Result('当前环境未启用 YouTube 音频下载组件；未将音频命令改成视频发送。')
            if command == 'bm' or LINK.search(request.text):
                return await self.links.parse(request.text)
        if request.attachments:
            return Result("已收到附件事件。当前可用文字或分享链接查询；附件导入尚未启用，未自动下载或写入旧数据。")
        return None
