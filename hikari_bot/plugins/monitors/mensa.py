import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from nonebot import get_bot, get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.feature_flags import get_mensa_enabled, set_mensa_enabled
from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers

EXAM_URL = "https://mensa.jp/exam/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


def _extract_tokyo_slots_from_html(html: str) -> list[dict[str, str]]:
    """从HTML结构中解析东京场次信息"""
    soup = BeautifulSoup(html, "html.parser")
    slots: list[dict[str, str]] = []
    
    # 查找所有考试场次列表
    exam_lists = soup.find_all('ul', class_='list')
    
    for ul in exam_lists:
        li_elements = ul.find_all('li')
        if len(li_elements) < 3:
            continue
            
        # 第一个li包含地区信息
        pref_li = li_elements[0]
        if not pref_li.get_text(strip=True).startswith('東京都'):
            continue  # 跳过非东京场次
            
        # 第二个li包含日期时间信息
        date_li = li_elements[1]
        # 直接从 HTML 解析，而不是转换为纯文本
        html_content = str(date_li)
        
        # 使用更精确的正则表达式匹配
        datetime_match = re.search(r'日時\s*：\s*([^<]+)', html_content)
        place_match = re.search(r'場所\s*：\s*([^<]+)', html_content)
        
        datetime_str = datetime_match.group(1).strip() if datetime_match else ""
        place_str = place_match.group(1).strip() if place_match else ""
        
        # 从日期时间字符串中提取简短日期
        date_short_match = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', datetime_str)
        date_short = f"{date_short_match.group(2)}/{date_short_match.group(3)}" if date_short_match else ""
        
        # 第三个li包含状态信息（图片alt属性）
        status_li = li_elements[2]
        status = "UNKNOWN"
        
        # 查找图片的alt属性
        img = status_li.find('img')
        if img and img.get('alt'):
            status = img.get('alt')
            
        slot = {
            "pref": "東京都",
            "date": date_short,
            "datetime": datetime_str,
            "place": place_str,
            "status": status,
        }
        slots.append(slot)
    
    return slots


async def fetch_tokyo_slots() -> list[dict[str, str]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = await client.get(EXAM_URL)
        resp.raise_for_status()

    html = resp.text
    slots = _extract_tokyo_slots_from_html(html)

    if not slots:
        # 页面结构变了，别悄悄当没事发生
        raise RuntimeError("没有从官网页面解析到任何东京场次，可能是页面结构发生变化。")

    return slots


async def check_once(force_notify: bool = False) -> None:
    slots = await fetch_tokyo_slots()
    
    # 检查是否有需要通知的情况
    should_notify = False
    notify_reasons = []
    
    # 检查是否有非满员场次  
    available_slots = [slot for slot in slots if slot["status"] not in {"満員", "締切"}]
    if available_slots:
        should_notify = True
        notify_reasons.append(f"发现{len(available_slots)}个可报名场次！")
    
    # 只在有需要通知的情况时发送消息
    if should_notify or force_notify:
        message_parts = []
        if force_notify:
            message_parts.append("MENSA东京考场检查结果")
        else:
            message_parts.extend(notify_reasons)
        
        message_parts.append(f"\n共{len(slots)}个东京场次：")
        for slot in slots:
            message_parts.append(f"{slot['datetime']} - {slot['status']}")
        
        await message_superusers("\n".join(message_parts))


async def scheduled_mensa_check() -> None:
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            await check_once(force_notify=False)
            break
        except Exception as e:
            retry_count += 1
            await log_message(f"[mensa_monitor] Failed to check mensa (attempt {retry_count}/{max_retries}): {e}")
            if retry_count < max_retries:
                await asyncio.sleep(60)  # 重试前等待60秒
            else:
                await log_message(f"[mensa_monitor] Failed to check mensa after {max_retries} attempts")
                # 监控异常时通知
                await message_superusers(f"MENSA东京考场监控异常\n{type(e).__name__}: {e}")


@scheduler.scheduled_job("interval", minutes=3, id="mensa_tokyo_monitor", misfire_grace_time=1800)
async def _scheduled_mensa_job():
    await scheduled_mensa_check()


mensa_toggle = on_command("切换门萨", aliases={"切换mensa"}, permission=SUPERUSER)

@mensa_toggle.handle()
async def _(bot: Bot, event: MessageEvent):
    current = await get_mensa_enabled()
    new_value = not current
    await set_mensa_enabled(new_value)
    if new_value:
        # 重新添加定时任务（若已被移除）
        if not scheduler.get_job("mensa_tokyo_monitor"):
            scheduler.add_job(
                scheduled_mensa_check,
                "interval",
                minutes=3,
                id="mensa_tokyo_monitor",
                misfire_grace_time=1800,
            )
        await bot.send(event, "MENSA监控已开启")
        await log_message("[mensa_monitor] MENSA monitor enabled.")
    else:
        if scheduler.get_job("mensa_tokyo_monitor"):
            scheduler.remove_job("mensa_tokyo_monitor")
        await bot.send(event, "MENSA监控已关闭")
        await log_message("[mensa_monitor] MENSA monitor disabled.")


mensa_check = on_command("门萨", aliases={"mensa"}, permission=SUPERUSER)

@mensa_check.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        await check_once(force_notify=True)
    except Exception as e:
        await log_message(f"[mensa_monitor] Manual check failed: {e}")
        await message_superusers(f"MENSA东京考场监控手动检查失败\n{type(e).__name__}: {e}")


driver = get_driver()
@driver.on_bot_connect
async def _startup_mensa_check(bot: Bot):
    if not await get_mensa_enabled():
        scheduler.remove_job("mensa_tokyo_monitor")
        return
    await log_message("[mensa_monitor] MENSA monitor started.")
    try:
        await check_once(force_notify=True)
    except Exception as e:
        await log_message(f"[mensa_monitor] Startup check failed: {e}")
        await message_superusers(f"MENSA东京考场监控启动失败\n{type(e).__name__}: {e}")