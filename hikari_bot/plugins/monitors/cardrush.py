"""
cardrush.py — CardRush 买取价格监控与查询插件

功能：
  - 每 5 分钟抓取 CardRush 全站买取价，检测变化并写入本地数据库
  - 卡价查询：按卡名（支持中/英/日）+ 可选稀有度/盒子编号查询当前价格
  - 卡价曲线：绘制指定卡片的历史价格折线图
  - 检查卡价 / 重置卡价数据库：管理员命令
"""

import asyncio
import base64
import re
from datetime import date, datetime
from io import BytesIO

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from nonebot import get_driver, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, RejectedException
from nonebot.params import Arg, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.logger import log_message
from hikari_bot.services.price import (
    get_daily_report_changes,
    get_price_history,
    query_all,
    reset_database,
    save_prices,
    search_local_prices,
)
from hikari_bot.services.ygocard import get_card_info


# ── 稀有度映射 ────────────────────────────────────────────────────────────────
# 日文名称 → 英文缩写。多个日文名可对应同一英文缩写（如各色 UR 变体）。
# 查询时用前缀匹配：搜 "PSER" 可同时命中 PSER 和 PSER-OF。

RARITY_MAPPING = {
    # 基础稀有度
    "ノーマル":                              "N",
    "レア":                                  "R",
    "スーパー":                              "SR",
    "ウルトラ":                              "UR",
    "レリーフ":                              "UTR",
    "コレクターズ":                          "CR",
    "プレミアムゴールド":                    "GR",
    "ホログラフィック":                      "HR",
    "シークレット":                          "SER",
    "エクストラシークレット":                "ESR",
    "プリズマティックシークレット":          "PSER",
    "クォーターセンチュリーシークレット":    "QCSER",
    "20thシークレット":                      "20SER",
    "ゴールドシークレット":                  "GSER",
    "10000シークレット":                     "10000SER",
    # パラレル系
    "ノーマルパラレル":                      "NPR",
    "ウルトラパラレル":                      "UPR",
    "ホログラフィックパラレル":              "HPR",
    "シークレットパラレル":                  "SEPR",
    # その他
    "ウルトラシークレット":                  "USR",
    "KCウルトラ":                            "UKC",
    "シークレットSPECIALREDVer.":           "SER-SRV",
    # UR 変体（色違い・特別版）
    "ウルトラブルー":                        "UR",
    "ウルトラレッド":                        "UR",
    "ウルトラSPECIALPURPLEVer.":           "UR",
    "ウルトラSPECIALILLUSTVer.":           "UR",
    # QCSER 変体
    "クォーターセンチュリーシークレットGREEN Ver.": "QCSER",
    # OF シリーズ
    "OFウルトラ":                            "UR-OF",
    "OFプリズマティックシークレット":        "PSER-OF",
    "グランドマスター":                      "GMR-OF",
}


# ── ユーティリティ ────────────────────────────────────────────────────────────

def rarity_jp_to_en(rarity_jp: str) -> str:
    """日文稀有度名 → 英文缩写，未知则原样返回。"""
    if not rarity_jp:
        return "未知"
    return RARITY_MAPPING.get(rarity_jp, rarity_jp)


def expand_rarity_to_jp_list(rarity_en: str) -> list[str]:
    """
    将英文稀有度缩写展开为所有匹配的日文名列表（前缀匹配、大小写不敏感）。
    例：'PSER' → ['プリズマティックシークレット', 'OFプリズマティックシークレット']
    """
    upper = rarity_en.upper()
    return [jp for jp, en in RARITY_MAPPING.items() if en.upper().startswith(upper)]


def clean_card_name(name: str) -> str:
    """
    清理卡名，去掉标点/空格，只保留：
      汉字、平假名、片假名（不含中点・）、数学符号（∀ 等）、英文字母、数字。
    """
    if not name:
        return name
    name = name.replace("＜", "").replace("＞", "")
    return re.sub(
        r"[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff\u2200-\u22ffa-zA-Z0-9]",
        "",
        name,
    )


def parse_price_query(input_text: str) -> tuple[str, str | None, str | None]:
    """
    从用户输入末尾剥离稀有度/盒子编号过滤词，其余部分作为卡名。

    剥离规则（从末尾向前，至少保留一个 token 作为卡名）：
      1. 大小写不敏感，与已知英文缩写前缀匹配 → 识别为稀有度
      2. 原文全大写，形如 ALIN / RC04 / DUNE → 识别为盒子编号
      3. 否则停止

    返回: (card_name, rarity_en_upper_or_None, model_prefix_or_None)

    示例:
      '青眼白龙'             → ('青眼白龙', None, None)
      '青眼白龙 UR'          → ('青眼白龙', 'UR', None)
      '青眼白龙 ALIN UR'     → ('青眼白龙', 'UR', 'ALIN')
      'Blue-Eyes UR ALIN'    → ('Blue-Eyes', 'UR', 'ALIN')
    """
    tokens = input_text.split()
    rarity_en: str | None = None
    model_prefix: str | None = None

    while len(tokens) > 1:
        last = tokens[-1]
        last_upper = last.upper()

        if rarity_en is None:
            if any(en.upper().startswith(last_upper) for en in RARITY_MAPPING.values()):
                rarity_en = last_upper
                tokens.pop()
                continue

        if model_prefix is None and re.match(r"^[A-Z]{2,6}[0-9]{0,2}$", last):
            model_prefix = last
            tokens.pop()
            continue

        break

    return " ".join(tokens), rarity_en, model_prefix


async def resolve_card_name_jp(name: str) -> str:
    """
    尝试用 YGO 卡片数据库将卡名翻译为日文。
    找不到时直接用原始输入（清理符号后）。
    """
    card_info = await get_card_info(name)
    if card_info:
        return clean_card_name(card_info["jp_name"])
    return clean_card_name(name)


def _draw_price_chart(history: list[dict]) -> bytes:
    """
    将价格历史按真实时间绘制折线图，返回 PNG 字节。
    单点时显示散点 + 虚线，多点时显示阶梯折线（steps-post）。
    """
    dates, prices = [], []
    for record in history:
        try:
            dates.append(datetime.fromisoformat(record["changed_at"]))
            prices.append(record["price"])
        except Exception:
            pass

    if not dates:
        return b""

    fig, ax = plt.subplots(figsize=(8, 4))

    if len(dates) == 1:
        ax.scatter(dates, prices, color="#e74c3c", zorder=5, s=40)
        ax.axhline(y=prices[0], color="#e74c3c", linestyle="--", linewidth=0.8, alpha=0.6)
    else:
        ax.plot(dates, prices, marker="o", linestyle="-", color="#e74c3c",
                linewidth=1.5, markersize=5, drawstyle="steps-post")

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.autofmt_xdate(rotation=30, ha="right")

    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_ylabel("JPY")

    ax.annotate(f"{prices[0]:,}", (dates[0], prices[0]),
                textcoords="offset points", xytext=(6, 6), fontsize=9, color="#333333")
    if len(prices) > 1:
        ax.annotate(f"{prices[-1]:,}", (dates[-1], prices[-1]),
                    textcoords="offset points", xytext=(-6, -14), fontsize=9, color="#333333")

    ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()


# ── 卡价查询 ──────────────────────────────────────────────────────────────────
# 用法：卡价查询 <卡名> [稀有度] [盒子编号]
# 示例：卡价查询 青眼白龙 UR ALIN

card_price = on_cmd("卡价查询", aliases={"卡价"}, priority=5)

@card_price.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    input_text = args.extract_plain_text().strip()
    if not input_text:
        await card_price.finish("请输入要查询的卡片名称！")
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)
        name_jp = await resolve_card_name_jp(name)
        rarity_jp_list = expand_rarity_to_jp_list(rarity_en) if rarity_en else None

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, search_local_prices, name_jp, rarity_jp_list, model_prefix
        )

        if not results:
            await card_price.finish(f"暂无 {name_jp} 的价格信息。")
            return

        lines = [f"【{name_jp}】的价格信息："]
        for card in results[:10]:
            en = rarity_jp_to_en(card.get("rarity") or "")
            box = (card.get("model_number") or "").split("-")[0] or "未知"
            date = (card.get("changed_at") or "")[:10] or "未知"
            lines.append(f"\n{box}-{en}\n    {card['price']}円（{date}）")

        if len(results) == 10:
            lines.append("\n（最多显示10条，可附加稀有度或盒子编号缩小范围）")

        await card_price.finish("".join(lines))

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] card_price error: {e}")
            await card_price.finish(f"查询失败：{e}")


# ── 卡价曲线 ──────────────────────────────────────────────────────────────────
# 用法：卡价曲线 <卡名> [稀有度] [盒子编号]
# 若命中多条结果，会列出候选让用户选择编号。

price_curve = on_cmd("卡价曲线", aliases={"历史卡价", "卡价历史"}, priority=5)


@price_curve.handle()
async def price_curve_start(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
    state: T_State = ...,
):
    input_text = args.extract_plain_text().strip()
    if not input_text:
        await price_curve.finish("请输入卡片名称！例如：卡价曲线 青眼白龙")
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)
        name_jp = await resolve_card_name_jp(name)
        rarity_jp_list = expand_rarity_to_jp_list(rarity_en) if rarity_en else None

        loop = asyncio.get_event_loop()
        # 多取一条用于判断是否超过上限
        results = await loop.run_in_executor(
            None, search_local_prices, name_jp, rarity_jp_list, model_prefix, 11
        )

        if not results:
            await price_curve.finish(f"暂无 {name_jp} 的价格历史记录。")
            return

        if len(results) > 10:
            await price_curve.finish(
                "找到超过10条匹配结果，请附加稀有度（如 UR）或盒子编号（如 ALIN）缩小范围。"
            )
            return

        if len(results) == 1:
            # 只有一条结果，直接进入绘制阶段，预填 _choice 跳过交互
            state["_selected"] = results[0]
            state["_choice"] = "1"
        else:
            state["_candidates"] = results
            lines = "\n".join(
                f"{i + 1}. {r['name']}  {(r['model_number'] or '').split('-')[0]}"
                f"-{rarity_jp_to_en(r['rarity'] or '')}  {r['price']:,}円"
                for i, r in enumerate(results)
            )
            await price_curve.send(f"找到 {len(results)} 条结果，请回复编号：\n{lines}")

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] price_curve_start error: {e}")
            await price_curve.finish(f"查询失败：{e}")


@price_curve.got("_choice")
async def price_curve_draw(
    bot: Bot,
    event: MessageEvent,
    state: T_State = ...,
    choice: Message = Arg("_choice"),
):
    try:
        # 若 state 里没有候选数据，说明上一步已被 finish() 终止，静默丢弃
        if "_selected" not in state and "_candidates" not in state:
            await price_curve.finish()
            return

        if "_selected" in state:
            selected = state["_selected"]
        else:
            candidates = state["_candidates"]
            choice_text = choice.extract_plain_text().strip()
            try:
                idx = int(choice_text) - 1
            except ValueError:
                await price_curve.reject("请输入有效的数字编号：")
                return
            if not (0 <= idx < len(candidates)):
                await price_curve.reject(f"请输入 1-{len(candidates)} 之间的编号：")
                return
            selected = candidates[idx]

        product_id = selected["product_id"]
        name = selected["name"]
        rarity = selected["rarity"] or ""
        model_number = selected["model_number"] or ""

        loop = asyncio.get_event_loop()
        history = await loop.run_in_executor(None, get_price_history, product_id)

        if not history:
            await price_curve.finish("暂无该卡片的历史价格记录。")
            return

        box_code = model_number.split("-")[0] if model_number else "未知"
        display_name = f"{name}  {box_code}-{rarity_jp_to_en(rarity)}"

        img_bytes = _draw_price_chart(history)
        if not img_bytes:
            await price_curve.finish("绘制图表失败。")
            return

        text = (
            f"{display_name}\n"
            f"当前买取价：{history[-1]['price']:,}円"
            f"（共 {len(history)} 条记录）"
        )
        img_b64 = base64.b64encode(img_bytes).decode()
        await price_curve.finish(Message([
            MessageSegment.text(text),
            MessageSegment.image(f"base64://{img_b64}"),
        ]))

    except Exception as e:
        if not isinstance(e, (FinishedException, RejectedException)):
            await log_message(f"[cardrush] price_curve_draw error: {e}")
            await price_curve.finish(f"绘制失败：{e}")


# ── 卡价日报 ──────────────────────────────────────────────────────────────────
# 用法：卡价日报 [M.D]   例：卡价日报 4.27

def _parse_date_arg(arg: str) -> str:
    """
    将 'M.D' 或 'MM.DD' 格式转为 'YYYY-MM-DD'。
    若月日晚于今天则视为上一年。
    """
    m = re.match(r"^(\d{1,2})\.(\d{1,2})$", arg.strip())
    if not m:
        raise ValueError("日期格式不正确，请使用 M.D 格式，如 4.27")
    month, day = int(m.group(1)), int(m.group(2))
    today = date.today()
    try:
        d = date(today.year, month, day)
    except ValueError:
        raise ValueError(f"无效日期：{month}.{day}")
    if d > today:
        d = date(today.year - 1, month, day)
    return d.isoformat()


def _format_daily_report(changes: list[dict], date_str: str) -> list[str]:
    """将日报变化列表格式化为文字消息，每条最多 50 条变化。"""
    if not changes:
        return [f"【卡价日报 {date_str}】\n当日无价格变化记录。"]

    PAGE_SIZE = 50
    total_pages = (len(changes) + PAGE_SIZE - 1) // PAGE_SIZE
    messages = []

    for page_idx in range(total_pages):
        page = changes[page_idx * PAGE_SIZE : (page_idx + 1) * PAGE_SIZE]
        header = f"【卡价日报 {date_str}】共 {len(changes)} 条变化"
        if total_pages > 1:
            header += f"（{page_idx + 1}/{total_pages}）"

        lines = [header]
        for c in page:
            box = (c.get("model_number") or "").split("-")[0] or "?"
            rarity = rarity_jp_to_en(c.get("rarity") or "")
            name = c["name"]
            new_price = c["new_price"]

            if c["change_type"] == "new":
                lines.append(f"[新] {name} {box}-{rarity}：{new_price:,}円")
            else:
                old_price = c["old_price"]
                diff = c["price_diff"]
                pct = c["percent_diff"]
                sign = "+" if diff > 0 else ""
                arrow = "↑" if diff > 0 else "↓"
                pct_str = f" {sign}{pct:.1f}%" if pct is not None else ""
                lines.append(
                    f"{arrow} {name} {box}-{rarity}："
                    f"{old_price:,} → {new_price:,}（{sign}{diff:,}円{pct_str}）"
                )

        messages.append("\n".join(lines))

    return messages


daily_report = on_cmd("卡价日报", priority=5)

@daily_report.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()

    try:
        if arg_text:
            date_str = _parse_date_arg(arg_text)
        else:
            date_str = date.today().isoformat()

        loop = asyncio.get_event_loop()
        changes = await loop.run_in_executor(None, get_daily_report_changes, date_str)
        messages = _format_daily_report(changes, date_str)

        for msg in messages:
            await bot.send(event, msg)
        await daily_report.finish()

    except ValueError as e:
        await daily_report.finish(str(e))
    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] daily_report error: {e}")
            await daily_report.finish(f"查询失败：{e}")


# ── 定时任务：价格监控 ────────────────────────────────────────────────────────

async def check_price_changes():
    """拉取最新价格并保存，返回新增记录数。"""
    new_prices = query_all()
    count = save_prices(new_prices)
    if count > 0:
        await log_message(f"[cardrush_monitor] Finish checking with {count} change(s).")


async def scheduled_price_check():
    """带重试的定时任务入口，最多重试 5 次，间隔 30 秒。"""
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            await check_price_changes()
            return
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(30)
            else:
                await log_message(
                    f"[cardrush_monitor] Failed after {max_retries} attempts: {e}"
                )


@scheduler.scheduled_job("interval", minutes=5, id="cardrush_price_monitor", misfire_grace_time=300)
async def _scheduled_job():
    await scheduled_price_check()


# ── 管理员命令 ────────────────────────────────────────────────────────────────

price_check = on_cmd("检查卡价", permission=SUPERUSER)

@price_check.handle()
async def _(bot: Bot, event: MessageEvent):
    await scheduled_price_check()


reset_db = on_cmd("重置卡价数据库", permission=SUPERUSER)

@reset_db.handle()
async def _(bot: Bot, event: MessageEvent):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, reset_database)
    await bot.send(event, "数据库已清空重建，开始重新抓取全站数据…")
    await scheduled_price_check()
    await bot.send(event, "数据抓取完成。")


# ── 启动钩子 ──────────────────────────────────────────────────────────────────

driver = get_driver()

@driver.on_bot_connect
async def _startup_price_check(bot: Bot):
    await log_message("[cardrush_monitor] CardRush monitor started.")
    await scheduled_price_check()
