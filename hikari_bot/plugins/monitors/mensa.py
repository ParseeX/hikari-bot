from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from nonebot import get_bot, get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers

EXAM_URL = "https://mensa.jp/exam/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


def _normalize_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    line = line.replace("： ", "：").replace(" :", ":")
    return line


def _extract_status(lines: list[str], start: int, end: int) -> str | None:
    for i in range(start, min(end, len(lines))):
        text = lines[i]
        if text in {"申し込む", "満員", "締切"}:
            return text
    return None


def _extract_tokyo_slots_from_text(text: str) -> list[dict[str, str]]:
    """
    从页面纯文本里抓东京场次。
    依赖官网当前文本模式：
    東京都
    5/02
    日時：2026/05/02(土) 13:00~14:00
    場所：東京都港区
    ...
    満員 / 申し込む / 締切
    """
    lines = [_normalize_line(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    slots: list[dict[str, str]] = []

    for i, line in enumerate(lines):
        if line != "東京都":
            continue

        window = lines[i : i + 10]

        date_line = ""
        datetime_line = ""
        place_line = ""
        status_line = ""

        for item in window:
            if re.fullmatch(r"\d{1,2}/\d{1,2}", item):
                date_line = item
            elif item.startswith("日時"):
                datetime_line = item
            elif item.startswith("場所"):
                place_line = item

        status_line = _extract_status(lines, i, i + 10) or "UNKNOWN"

        # 保险：必须真的是东京地点
        if "東京都" not in place_line and "東京都" not in datetime_line:
            continue

        slot = {
            "pref": "東京都",
            "date": date_line,
            "datetime": datetime_line,
            "place": place_line,
            "status": status_line,
        }
        slots.append(slot)

    # 去重
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for slot in slots:
        key = json.dumps(slot, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(slot)

    return unique


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
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    slots = _extract_tokyo_slots_from_text(text)

    if not slots:
        # 页面结构变了，别悄悄当没事发生
        raise RuntimeError("没有从官网页面解析到任何东京场次，可能是页面结构发生变化。")

    return slots


async def check_once(force_notify: bool = False) -> None:
    slots = await fetch_tokyo_slots()
    
    # 检查是否有需要通知的情况
    should_notify = False
    notify_reasons = []
    current_time = datetime.now()
    one_month_later = current_time + timedelta(days=30)
    
    # 检查是否有非满员场次  
    available_slots = [slot for slot in slots if slot["status"] not in {"満員", "締切"}]
    if available_slots:
        should_notify = True
        notify_reasons.append(f"发现{len(available_slots)}个可报名场次！")
    
    # 检查是否有远期场次（大于一个月）
    far_future_slots = []
    for slot in slots:
        datetime_str = slot.get("datetime", "")
        # 解析日期，如日時：2026/05/02(土) 13:00~14:00
        match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", datetime_str)
        if match:
            try:
                year, month, day = map(int, match.groups())
                slot_date = datetime(year, month, day)
                if slot_date > one_month_later:
                    far_future_slots.append(slot)
            except ValueError:
                continue
    
    if far_future_slots:
        should_notify = True
        notify_reasons.append(f"发现{len(far_future_slots)}个远期场次！")
    
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
            await log_message(f"[mensa_monitor] 定时检查失败 (attempt {retry_count}/{max_retries}): {e}")
            if retry_count < max_retries:
                await asyncio.sleep(60)  # 重试前等待60秒
            else:
                await log_message(f"[mensa_monitor] Failed to check mensa after {max_retries} attempts")
                # 监控异常时通知
                await message_superusers(f"MENSA东京考场监控异常\n{type(e).__name__}: {e}")


@scheduler.scheduled_job("interval", minutes=3, id="mensa_tokyo_monitor", misfire_grace_time=1800)
async def _scheduled_mensa_job():
    await scheduled_mensa_check()


mensa_check = on_command("检查门萨", aliases={"门萨检查"}, permission=SUPERUSER)

@mensa_check.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        slots = await fetch_tokyo_slots()
    except Exception as e:
        await log_message(f"[mensa_monitor] Manual check failed: {e}")
        await mensa_check.finish(f"检查失败：{type(e).__name__}: {e}")
        
    message_parts = ["MENSA东京考场当前状态"]
    message_parts.append(f"共{len(slots)}个东京场次：")
    
    for slot in slots:
        message_parts.append(f"{slot['datetime']} - {slot['status']}")
    
    await mensa_check.finish("\n".join(message_parts))


driver = get_driver()
@driver.on_startup
async def _startup_mensa_check():
    await log_message("[mensa_monitor] MENSA monitor started.")
    try:
        await check_once(force_notify=True)
    except Exception as e:
        await log_message(f"[mensa_monitor] Startup check failed: {e}")
        await message_superusers(f"MENSA东京考场监控启动失败\n{type(e).__name__}: {e}")