"""
ygocard_query.py — 游戏王卡片查询插件

功能：
  - 随机一卡、每日一卡（按用户 ID + 日期生成确定种子）
  - 卡图、卡密、效果文本查询
  - 本地卡片数据库更新
  - 共界计算器（Metaltronus）
"""

import asyncio
import base64
import io
import re
from datetime import datetime

from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.logger import log_message
from hikari_bot.services import ygocard as card_service
from hikari_bot.services.ygodeck import generate_card_list_image


# ── 随机 / 每日卡片 ────────────────────────────────────────────────────────────────────

ygo_random_card = on_command("随机一卡", priority=5, permission=SUPERUSER)
@ygo_random_card.handle()
async def _(bot: Bot, event: MessageEvent):
    image = await card_service.get_ygopic(card_service.random_card(), half=False)
    if not image:
        await log_message(f"[ygo_random_card] Ramdom card image not found.")
        await ygo_random_card.finish("未找到随机卡片！")
        return
    image_base64 = base64.b64encode(image).decode('utf-8')
    await ygo_random_card.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))

ygo_daily_card = on_command("每日一卡", priority=5)
@ygo_daily_card.handle()
async def _(bot: Bot, event: MessageEvent):
    today = datetime.now().strftime("%Y-%m-%d")
    seed_str = f"{event.get_user_id()}_{today}"
    seed = hash(seed_str) % (2**31 - 1)
    image = await card_service.get_ygopic(card_service.random_card(seed), half=False)
    if not image:
        await log_message(f"[ygo_daily_card] Daily card image not found.")
        await ygo_daily_card.finish("未找到每日卡片！")
        return
    image_base64 = base64.b64encode(image).decode('utf-8')
    await ygo_daily_card.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


ygo_card_pic = on_cmd("卡图查询", aliases={"游戏王卡图", "卡图"}, priority=5)
@ygo_card_pic.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        if card_service.is_card_id(input):
            card_id = int(input)
        else:
            if "异画" in input:
                match = re.search(r'异画(\d+)', input)
                if match:
                    offset = int(match.group(1))
                    input = input.replace(match.group(0), "")
                else:
                    offset = 1
                    input = input.replace("异画", "")
                
                card_info = await card_service.get_card_info(input)
                if card_info:
                    card_id = card_info["id"] + offset
                else:
                    card_id = None
            else:
                card_info = await card_service.get_card_info(input)
                if card_info:
                    card_id = card_info["id"]
                else:
                    card_id = None
        
        if not card_id:
            await ygo_card_pic.finish("未找到对应卡片！")
            return

        image = await card_service.get_image_by_id(card_id)
        if not image:
            await log_message(f"[ygo_card_pic] Card image not found for card ID: {card_id}")
            await ygo_card_pic.finish("卡图加载失败！")
            return

        image_base64 = base64.b64encode(image).decode('utf-8')
        await ygo_card_pic.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


ygo_card_id = on_cmd("卡密查询", aliases={"游戏王卡密", "卡密"}, priority=5)
@ygo_card_id.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await card_service.get_card_info(input)
        
        if not card_info:
            await ygo_card_id.finish("查询失败！")
            return

        await ygo_card_id.finish(str(card_info["id"]))


ygo_card_effect = on_cmd("效果查询", aliases={"游戏王效果", "效果"}, priority=5)
@ygo_card_effect.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await card_service.get_card_info(input)

        if not card_info:
            await ygo_card_effect.finish("未找到对应卡片！")
            return

        official_name = card_info.get("jp_name") or card_info.get("en_name", "")
        cn_name = card_info.get("cn_name", "")
        type = card_info["text"]["types"]
        p_effect = card_info["text"]["pdesc"]
        effect = card_info["text"]["desc"]

        if cn_name:
            result = f"{cn_name}（{official_name}）\n{type}\n"
        else:
            result = f"{official_name}\n{type}\n"
        if p_effect != "":
            result = result + p_effect + "\n---------------\n"
        
        if effect == "":
            effect = "※公式のデュエルでは使用できません。"
        result = result + effect

        await ygo_card_effect.finish(result)


# ── 数据库维护 ─────────────────────────────────────────────────────────────────────────
# 用法：更新数据库

ygo_update_database = on_cmd("更新数据库", priority=5)
@ygo_update_database.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        await card_service.update_cdb()
        await ygo_update_database.finish("更新完成。")
    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[ygo_update_database] Failed to update database: {e}")
            await ygo_update_database.finish("更新失败！")


@scheduler.scheduled_job("cron", hour=3, minute=0, id="ygo_update_database_daily", misfire_grace_time=1800)
async def ygo_update_database_daily_job():
    try:
        await card_service.update_cdb()
        await log_message("[ygo_update_database_daily] Updated successfully.")
    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[ygo_update_database_daily] Failed: {e}")


# ── 共界计算 ─────────────────────────────────────────────────────────────────────────
# 用法：共界计算 卡名

ygo_metaltronus_calc = on_cmd("共界计算", priority=5)
@ygo_metaltronus_calc.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await card_service.get_card_info(input)

        if not card_info:
            await ygo_metaltronus_calc.finish("未找到对应卡片！")
            return
        
        # 使用 asyncio.to_thread 将同步的计算操作放到线程中执行，避免阻塞事件循环
        try:
            result = await asyncio.to_thread(card_service.metaltronus_calc, card_info["id"])
        except Exception as e:
            await log_message(f"[ygo_metaltronus_calc] Error during calculation for card ID {card_info['id']}: {e}")
            await ygo_metaltronus_calc.finish(f"计算过程中出现错误：{str(e)}")
            return
            
        if not result:
            await ygo_metaltronus_calc.finish("没有满足条件的卡片！")
            return
        
        image = await generate_card_list_image(result)
        if not image:
            await ygo_metaltronus_calc.finish("结果加载失败！")
            return
            
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
        await ygo_metaltronus_calc.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))

