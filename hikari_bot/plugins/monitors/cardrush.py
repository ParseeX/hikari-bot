"""Cardrush commands, schedules, and OneBot delivery adapters."""

import asyncio
import base64
import re
from datetime import date

from nonebot import get_bot, get_driver, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException, RejectedException
from nonebot.params import Arg, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.constants import ADMIN
from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush import get_default_cardrush_service
from hikari_bot.features.cardrush.parsing import (
    expand_rarity_to_jp_list,
    parse_price_query,
    rarity_jp_to_en,
    resolve_card_name_jp,
)
from hikari_bot.features.cardrush.reporting import (
    DailyReportRenderer,
    DailyReportWorkflow,
    draw_price_chart,
    format_daily_report,
    parse_date_arg,
)
from hikari_bot.services.bilibili import post_article_with_images

service = get_default_cardrush_service()
report_renderer = DailyReportRenderer()
report_workflow = DailyReportWorkflow(service, report_renderer)

card_price = on_cmd("卡价查询", aliases={"卡价"}, priority=5)

@card_price.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    input_text = args.extract_plain_text().strip()
    if not input_text:
        await card_price.finish("请输入要查询的卡片名称！")
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)
        name_jp = await resolve_card_name_jp(name)
        rarities = (
            expand_rarity_to_jp_list(rarity_en) if rarity_en else None
        )
        results = await service.search_prices(
            name_jp,
            rarities,
            model_prefix,
        )
        if not results:
            await card_price.finish(f"暂无 {name_jp} 的价格信息。")
            return

        lines = [f"【{name_jp}】的价格信息："]
        for card in results[:10]:
            rarity = rarity_jp_to_en(card.rarity or "")
            box = (card.model_number or "").split("-")[0] or "未知"
            changed_date = (card.changed_at or "")[:10] or "未知"
            lines.append(
                f"\n{box}-{rarity}\n"
                f"    {card.price}円（{changed_date}）"
            )
        if len(results) == 10:
            lines.append(
                "\n（最多显示10条，可附加稀有度或盒子编号缩小范围）"
            )
        await card_price.finish("".join(lines))
    except Exception as error:
        if not isinstance(error, FinishedException):
            await log_message(f"[cardrush] card_price error: {error}")
            await card_price.finish(f"查询失败：{error}")


price_curve = on_cmd(
    "卡价曲线",
    aliases={"历史卡价", "卡价历史"},
    priority=5,
)

@price_curve.handle()
async def price_curve_start(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
    state: T_State = ...,
):
    input_text = args.extract_plain_text().strip()
    if not input_text:
        await price_curve.finish(
            "请输入卡片名称！例如：卡价曲线 青眼白龙"
        )
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)
        name_jp = await resolve_card_name_jp(name)
        rarities = (
            expand_rarity_to_jp_list(rarity_en) if rarity_en else None
        )
        results = await service.search_prices(
            name_jp,
            rarities,
            model_prefix,
            11,
        )
        if not results:
            await price_curve.finish(
                f"暂无 {name_jp} 的价格历史记录。"
            )
            return
        if len(results) > 10:
            await price_curve.finish(
                "找到超过10条匹配结果，请附加稀有度（如 UR）"
                "或盒子编号（如 ALIN）缩小范围。"
            )
            return

        if len(results) == 1:
            state["_selected"] = results[0]
            state["_choice"] = "1"
        else:
            state["_candidates"] = results
            lines = "\n".join(
                f"{index + 1}. {result.name}  "
                f"{(result.model_number or '').split('-')[0]}"
                f"-{rarity_jp_to_en(result.rarity or '')}  "
                f"{result.price:,}円"
                for index, result in enumerate(results)
            )
            await price_curve.send(
                f"找到 {len(results)} 条结果，请回复编号：\n{lines}"
            )
    except Exception as error:
        if not isinstance(error, FinishedException):
            await log_message(
                f"[cardrush] price_curve_start error: {error}"
            )
            await price_curve.finish(f"查询失败：{error}")


@price_curve.got("_choice")
async def price_curve_draw(
    bot: Bot,
    event: MessageEvent,
    state: T_State = ...,
    choice: Message = Arg("_choice"),
):
    try:
        if "_selected" not in state and "_candidates" not in state:
            await price_curve.finish()
            return

        if "_selected" in state:
            selected = state["_selected"]
        else:
            candidates = state["_candidates"]
            try:
                index = int(choice.extract_plain_text().strip()) - 1
            except ValueError:
                await price_curve.reject("请输入有效的数字编号：")
                return
            if not 0 <= index < len(candidates):
                await price_curve.reject(
                    f"请输入 1-{len(candidates)} 之间的编号："
                )
                return
            selected = candidates[index]

        history = await service.get_price_history(selected.product_id)
        if not history:
            await price_curve.finish("暂无该卡片的历史价格记录。")
            return

        box = (
            selected.model_number.split("-")[0]
            if selected.model_number
            else "未知"
        )
        display_name = (
            f"{selected.name}  "
            f"{box}-{rarity_jp_to_en(selected.rarity or '')}"
        )
        image = draw_price_chart(history)
        if not image:
            await price_curve.finish("绘制图表失败。")
            return

        text = (
            f"{display_name}\n"
            f"当前买取价：{history[-1].price:,}円"
            f"（共 {len(history)} 条记录）"
        )
        encoded = base64.b64encode(image).decode()
        await price_curve.finish(
            Message(
                [
                    MessageSegment.text(text),
                    MessageSegment.image(f"base64://{encoded}"),
                ]
            )
        )
    except Exception as error:
        if not isinstance(
            error,
            (FinishedException, RejectedException),
        ):
            await log_message(
                f"[cardrush] price_curve_draw error: {error}"
            )
            await price_curve.finish(f"绘制失败：{error}")


def _report_date(argument: str) -> str:
    if not argument:
        return date.today().isoformat()
    for part in argument.split():
        if not re.match(r"^\d{1,2}\.\d{1,2}$", part):
            raise ValueError(
                f"无法识别的参数：{part}，支持格式：日期(4.27)"
            )
        parsed = parse_date_arg(part)
    return parsed


daily_report_html = on_cmd(
    "卡价图报",
    priority=5,
    permission=SUPERUSER,
)

@daily_report_html.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    try:
        date_str = _report_date(args.extract_plain_text().strip())
        changes = await service.get_daily_changes(
            date_str,
            exclude_prefixes=["RD/"],
        )
        if not changes:
            await daily_report_html.finish(
                f"【{date_str}】当日无价格变化记录。"
            )
            return

        await bot.send(
            event,
            f"正在下载 {len(changes)} 张卡图，请稍候…",
        )
        pages = await report_workflow.render_for_date(date_str)
        await bot.send(
            event,
            f"下载完毕，正在发送 {len(pages)} 页图报…",
        )
        for page in pages:
            encoded = base64.b64encode(page).decode()
            await bot.send(
                event,
                MessageSegment.image(f"base64://{encoded}"),
            )
        await daily_report_html.finish(
            f"图报发送完毕（共 {len(pages)} 页）。"
        )
    except ValueError as error:
        await daily_report_html.finish(str(error))
    except Exception as error:
        if not isinstance(error, FinishedException):
            await log_message(
                f"[cardrush] daily_report_html error: {error}"
            )
            await daily_report_html.finish(f"生成失败：{error}")


daily_report = on_cmd("卡价日报", priority=5)

@daily_report.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    try:
        argument = args.extract_plain_text().strip()
        date_str = (
            parse_date_arg(argument)
            if argument
            else date.today().isoformat()
        )
        changes = await service.get_daily_changes(
            date_str,
            exclude_prefixes=["RD/"],
        )
        for message in format_daily_report(changes, date_str):
            await bot.send(event, message)
        await daily_report.finish()
    except ValueError as error:
        await daily_report.finish(str(error))
    except Exception as error:
        if not isinstance(error, FinishedException):
            await log_message(
                f"[cardrush] daily_report error: {error}"
            )
            await daily_report.finish(f"查询失败：{error}")


async def check_price_changes():
    count = await service.refresh_prices()
    if count > 0:
        await log_message(
            f"[cardrush_monitor] Finish checking with {count} change(s)."
        )


async def scheduled_price_check():
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            await check_price_changes()
            return
        except Exception as error:
            if attempt < max_retries:
                await asyncio.sleep(30)
            else:
                await log_message(
                    "[cardrush_monitor] Failed after "
                    f"{max_retries} attempts: {error}"
                )


@scheduler.scheduled_job("interval", minutes=15, id="cardrush_price_monitor", misfire_grace_time=300)
async def _scheduled_job():
    await scheduled_price_check()


async def _auto_send_daily_report():
    date_str = date.today().isoformat()
    try:
        screenshots = await report_workflow.render_for_date(date_str)
        if not screenshots:
            await log_message(
                f"[cardrush_auto] No price changes on {date_str}, "
                "skipping report."
            )
            return

        bot = get_bot()
        for user_id in ADMIN:
            for screenshot in screenshots:
                encoded = base64.b64encode(screenshot).decode()
                await bot.send_private_msg(
                    user_id=int(user_id),
                    message=MessageSegment.image(
                        f"base64://{encoded}"
                    ),
                )
        await log_message(
            f"[cardrush_auto] Report sent to {ADMIN} "
            f"({len(screenshots)} page(s))."
        )
        await post_article_with_images(screenshots, date_str)
    except Exception as error:
        await log_message(
            f"[cardrush_auto] Auto report failed: {error}"
        )


@scheduler.scheduled_job("cron", hour=22, minute=20, timezone="Asia/Tokyo", id="cardrush_daily_report_auto", misfire_grace_time=600)
async def _auto_report_job():
    await _auto_send_daily_report()


reset_db = on_cmd("重置卡价数据库", permission=SUPERUSER)

@reset_db.handle()
async def _(bot: Bot, event: MessageEvent):
    await service.reset_database()
    await bot.send(event, "数据库已清空重建。")


bili_post = on_cmd("发布B站动态", permission=SUPERUSER)

@bili_post.handle()
async def _(bot: Bot, event: MessageEvent):
    date_str = date.today().isoformat()
    await bot.send(
        event,
        f"开始生成并发布 {date_str} 日报到 B 站，请稍候…",
    )
    try:
        screenshots = await report_workflow.render_for_date(date_str)
        if not screenshots:
            await bot.send(
                event,
                "今日无价格变动数据，无法生成日报。",
            )
            return
        succeeded = await post_article_with_images(
            screenshots,
            date_str,
        )
        if succeeded:
            await bot.send(
                event,
                "B 站动态已提交，将在定时时间发布。",
            )
        else:
            await bot.send(
                event,
                "B 站动态发布失败，请查看日志。",
            )
    except Exception as error:
        await bot.send(event, f"发布失败：{error}")


driver = get_driver()

@driver.on_bot_connect
async def _startup_price_check(bot: Bot):
    await log_message("[cardrush_monitor] CardRush monitor started.")
