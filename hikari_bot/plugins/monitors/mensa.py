"""
mensa.py — MENSA 东京考场监控插件

功能：
  - 定时（每3分钟）爬取 MENSA 官网，检测东京场次空位
  - 发现可报名场次时主动通知管理员
  - 支持运行时开启/关闭监控、手动触发一次性检查
"""

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from nonebot import get_driver, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.feature_flags import get_mensa_enabled, set_mensa_enabled
from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers


# ── 常量 ──────────────────────────────────────────────────────────────────────────────

EXAM_URL = "https://mensa.jp/exam/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


# ── HTML 解析 ─────────────────────────────────────────────────────────────────────────

def _extract_tokyo_slots_from_html(html: str) -> list[dict[str, str]]:
    """从 HTML 结构中解析东京场次信息。"""
    soup = BeautifulSoup(html, "html.parser")
    slots: list[dict[str, str]] = []

    for ul in soup.find_all("ul", class_="list"):
        li_elements = ul.find_all("li")
        if len(li_elements) < 3:
            continue

        pref_li = li_elements[0]
        if not pref_li.get_text(strip=True).startswith("東京都"):
            continue

        html_content = str(li_elements[1])
        datetime_match = re.search(r"日時\s*：\s*([^<]+)", html_content)
        place_match    = re.search(r"場所\s*：\s*([^<]+)", html_content)

        datetime_str = datetime_match.group(1).strip() if datetime_match else ""
        place_str    = place_match.group(1).strip()    if place_match    else ""

        date_short_match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", datetime_str)
        date_short = (
            f"{date_short_match.group(2)}/{date_short_match.group(3)}"
            if date_short_match else ""
        )

        img = li_elements[2].find("img")
        status = img.get("alt", "UNKNOWN") if img else "UNKNOWN"

        slots.append({
            "pref":     "東京都",
            "date":     date_short,
            "datetime": datetime_str,
            "place":    place_str,
            "status":   status,
        })

    return slots


# ── 数据获取 ──────────────────────────────────────────────────────────────────────────

async def fetch_tokyo_slots() -> list[dict[str, str]]:
    """从 MENSA 官网抓取并解析东京场次列表。"""
    headers = {
        "User-Agent":      USER_AGENT,
        "Accept-Language": "ja,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20.0, connect=10.0), headers=headers) as client:
        resp = await client.get(EXAM_URL)
        resp.raise_for_status()

    slots = _extract_tokyo_slots_from_html(resp.text)
    if not slots:
        raise RuntimeError("没有从官网页面解析到任何东京场次，可能是页面结构发生变化。")
    return slots


# ── 检查逻辑 ──────────────────────────────────────────────────────────────────────────

async def check_once(force_notify: bool = False) -> None:
    """检查一次场次状态，有空位或 force_notify=True 时通知管理员。"""
    slots = await fetch_tokyo_slots()
    available = [s for s in slots if s["status"] not in {"満員", "締切"}]

    if not available and not force_notify:
        return

    header = "MENSA东京考场检查结果" if force_notify else f"发现{len(available)}个可报名场次！"
    lines = [header, f"\n共{len(slots)}个东京场次："]
    lines += [f"{s['datetime']} - {s['status']}" for s in slots]
    await message_superusers("\n".join(lines))


# ── 定时任务 ──────────────────────────────────────────────────────────────────────────

async def scheduled_mensa_check() -> None:
    """带重试的定时检查任务（最多重试5次，每次间隔60秒）。"""
    for attempt in range(1, 6):
        try:
            await check_once(force_notify=False)
            return
        except Exception as e:
            await log_message(f"[mensa_monitor] 检查失败 ({attempt}/5): {e}")
            if attempt < 5:
                await asyncio.sleep(60)
    await message_superusers(f"MENSA东京考场监控持续异常，已重试5次，请检查。")


@scheduler.scheduled_job("interval", minutes=3, id="mensa_tokyo_monitor", misfire_grace_time=1800)
async def _scheduled_mensa_job():
    await scheduled_mensa_check()


# ── 管理员命令 ────────────────────────────────────────────────────────────────────────
# 用法：切换门萨 | 门萨

mensa_toggle = on_cmd("切换门萨", aliases={"切换mensa"}, permission=SUPERUSER)

@mensa_toggle.handle()
async def _(bot: Bot, event: MessageEvent):
    current = await get_mensa_enabled()
    new_value = not current
    await set_mensa_enabled(new_value)
    if new_value:
        if not scheduler.get_job("mensa_tokyo_monitor"):
            scheduler.add_job(
                scheduled_mensa_check,
                "interval",
                minutes=3,
                id="mensa_tokyo_monitor",
                misfire_grace_time=1800,
            )
        await bot.send(event, "MENSA监控已开启。")
        await log_message("[mensa_monitor] MENSA monitor enabled.")
    else:
        if scheduler.get_job("mensa_tokyo_monitor"):
            scheduler.remove_job("mensa_tokyo_monitor")
        await bot.send(event, "MENSA监控已关闭。")
        await log_message("[mensa_monitor] MENSA monitor disabled.")


mensa_check = on_cmd("门萨", aliases={"mensa"}, permission=SUPERUSER)

@mensa_check.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        await check_once(force_notify=True)
    except Exception as e:
        await log_message(f"[mensa_monitor] Manual check failed: {e}")
        await message_superusers(f"MENSA东京考场监控手动检查失败\n{type(e).__name__}: {e}")


# ── 启动钩子 ──────────────────────────────────────────────────────────────────────────

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


