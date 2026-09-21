"""卡价查询的两步交互，状态由 NoneBot 按会话隔离。"""
import asyncio
import time

from nonebot.adapters.onebot.v11 import Message
from nonebot.params import Arg, CommandArg
from nonebot.typing import T_State

from hikari_bot.core.config import settings
from hikari_bot.core.logger import log_message
from hikari_bot.features.card_prices.client import JhsClient, JhsUnavailable
from hikari_bot.features.card_prices.service import (
    ComparisonService, format_comparison, group_rarities, rarity_prompt,
)
from hikari_bot.features.cardrush.parsing import parse_price_query
from hikari_bot.services.ygocard import get_card_info


def register_price_query(matcher, cardrush, *, japanese: bool = False):
    comparison = ComparisonService(JhsClient(
        settings.jihuanshe_bridge_url, settings.jihuanshe_bridge_token,
        settings.jihuanshe_bridge_timeout,
    ), cardrush)

    @matcher.handle()
    async def start(state: T_State, args: Message = CommandArg()):
        text = args.extract_plain_text().strip()
        if not text:
            command = '日版价格查询' if japanese else '卡价'
            await matcher.finish(f"请输入卡片名称或卡密，例如：{command} 原石之皇脉")
        name, rarity, prefix = parse_price_query(text)
        try:
            info = await asyncio.wait_for(get_card_info(name), timeout=15)
            # 查询集换社时必须保留日文原文中的空格、标点和全角字符。
            name_jp = str((info or {}).get("jp_name") or name).strip()
            names_cn = tuple(str(info[k]) for k in ("cn_name", "sc_name", "md_name", "nwbbs_n")
                             if info and info.get(k))
            versions = await comparison.versions(name_jp, rarity, prefix, names_cn)
        except (JhsUnavailable, asyncio.TimeoutError):
            await matcher.finish("暂时无法获取集换社罕贵列表，请稍后重试。")
        except Exception as error:
            await log_message(f"[card_price] versions failed: {type(error).__name__}")
            await matcher.finish("查询罕贵列表失败，请稍后重试。")
        if not versions:
            await matcher.finish(f"集换社暂无【{name_jp}】的匹配版本，请核对日文完整卡名。")
        groups = group_rarities(versions)
        state["price_name_jp"] = name_jp
        state["price_groups"] = groups
        state["price_expires"] = time.monotonic() + 180
        await matcher.send(rarity_prompt(name_jp, groups))

    @matcher.got("price_choice")
    async def selected(state: T_State, choice: Message = Arg("price_choice")):
        value = choice.extract_plain_text().strip()
        if value.casefold() in {"取消", "退出", "cancel"}:
            await matcher.finish("已取消卡价查询。")
        if time.monotonic() > state.get("price_expires", 0):
            await matcher.finish("选择已过期，请重新发送卡价查询。")
        groups = state["price_groups"]
        names = list(groups)
        rarity = value.upper()
        if value.isascii() and value.isdigit():
            index = int(value) - 1
            rarity = names[index] if 0 <= index < len(names) else ""
        if rarity not in groups:
            await matcher.reject(f"请回复 1-{len(names)} 的编号或列表中的罕贵名称，也可回复“取消”。")
        versions = groups[rarity]
        try:
            mode = {'japanese': True} if japanese else {}
            rows = await comparison.compare(state["price_name_jp"], versions, **mode)
            pages = format_comparison(state["price_name_jp"], rarity, rows, **mode)
        except Exception as error:
            await log_message(f"[card_price] comparison failed: {type(error).__name__}")
            await matcher.finish("卡价查询失败，请稍后重试。")
        for page in pages:
            await matcher.send(page)
        await matcher.finish()
