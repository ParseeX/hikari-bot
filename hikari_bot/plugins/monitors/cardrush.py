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
import functools
import html as html_mod
import os
import shutil
import re
from datetime import date, datetime
from io import BytesIO

import aiohttp
from playwright.async_api import async_playwright

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
from hikari_bot.core.constants import DATA_DIR, RESOURCES_DIR
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


# ── 卡价日报（图片版 HTML 生成） ──────────────────────────────────────────────
# 用法：卡价图报 [M.D]   例：卡价图报 4.27

_CARD_IMAGE_URL = "https://files.cardrush.media/yugioh/ocha_products/{product_id}.webp"


def _load_bg_image_b64() -> str | None:
    """尝试加载 resources/bg_daily_report.{jpg/png/webp}，返回 CSS data URL，不存在则返回 None。"""
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = os.path.join(RESOURCES_DIR, f"bg_daily_report.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            return f"data:{mime};base64,{data}"
    return None


def _build_html_css(bg_url: str | None) -> str:
    """生成页面 CSS，bg_url 为背景图 data URL 或 None（使用渐变背景）。"""
    if bg_url:
        bg_css = f"background-image: url('{bg_url}'); background-size: auto; background-repeat: repeat;"
    else:
        bg_css = "background: linear-gradient(160deg, #07091a 0%, #0c1528 40%, #07091a 100%);"
    return f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: "Noto Sans CJK JP", "Source Han Sans JP", "Yu Gothic", "Meiryo",
                 "Microsoft YaHei", sans-serif;
    {bg_css}
    color: #e0e0e0;
    padding: 24px 24px 54px;
    min-width: 1300px;
    position: relative;
}}
body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background: rgba(6, 8, 22, 0.74);
    z-index: 0;
    pointer-events: none;
}}
.content-wrap {{
    position: relative;
    z-index: 1;
}}
/* ── 标题区域 ── */
.header {{
    text-align: center;
    margin-bottom: 22px;
    padding: 18px 0 16px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
}}
.header-eyebrow {{
    font-size: 11px;
    letter-spacing: 5px;
    color: #6a8aaa;
    text-transform: uppercase;
    margin-bottom: 8px;
}}
.header-title {{
    font-size: 26px;
    font-weight: 900;
    letter-spacing: 2px;
    background: linear-gradient(90deg, #90bcff 0%, #ffffff 45%, #ffd060 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 10px;
    line-height: 1.2;
}}
.header-rule {{
    width: 70px;
    height: 2px;
    background: linear-gradient(90deg, transparent, #4a9eff, transparent);
    margin: 0 auto 10px;
}}
.header-date {{
    font-size: 14px;
    color: #b8cce4;
    letter-spacing: 2px;
    margin-bottom: 10px;
}}
.header-stats {{
    display: inline-flex;
    gap: 18px;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 5px 20px;
    font-size: 13px;
}}
.stat-up   {{ color: #ff8080; }}
.stat-down {{ color: #60b8f8; }}
.stat-new  {{ color: #5cf0a0; }}
.stat-sep  {{ color: #3a4a5a; }}
/* ── 卡片网格 ── */
.grid {{
    display: grid;
    grid-template-columns: repeat(10, 1fr);
    gap: 8px;
}}
.card {{
    border-radius: 8px;
    overflow: hidden;
    position: relative;
    aspect-ratio: 3 / 4;
    border: 2px solid #3a6090;
    box-shadow: 0 2px 10px rgba(0,0,0,0.7);
    background: #080c18;
}}
.up   {{ border-color: #ff5252; box-shadow: 0 0 10px rgba(255,82,82,0.3), 0 2px 8px rgba(0,0,0,0.7); }}
.down {{ border-color: #3aacf0; box-shadow: 0 0 10px rgba(58,172,240,0.3), 0 2px 8px rgba(0,0,0,0.7); }}
.new  {{ border-color: #00e676; box-shadow: 0 0 10px rgba(0,230,118,0.3), 0 2px 8px rgba(0,0,0,0.7); }}
.card-img {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top;
    display: block;
    background: #0c1428;
}}
/* 渐变遮罩 + 文字叠加在卡图下半部 */
.card-overlay {{
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    background: linear-gradient(
        to bottom,
        transparent 0%,
        rgba(4, 6, 18, 0.58) 20%,
        rgba(4, 6, 18, 0.88) 48%,
        rgba(4, 6, 18, 0.97) 100%
    );
    padding: 28px 7px 8px;
}}
.card-name {{
    font-size: 11px;
    font-weight: bold;
    color: #f5f5f5;
    word-break: break-all;
    white-space: normal;
    line-height: 1.35;
    margin-bottom: 3px;
    text-shadow: 0 1px 5px rgba(0,0,0,0.95);
}}
.card-meta {{
    font-size: 10px;
    color: #a8bcd8;
    word-break: break-all;
    white-space: normal;
    line-height: 1.3;
    margin-bottom: 5px;
    text-shadow: 0 1px 3px rgba(0,0,0,0.9);
}}
.price-row {{
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 3px;
}}
.price-block {{ flex: 1; min-width: 0; }}
.new-price {{
    font-size: 13px;
    font-weight: bold;
    white-space: nowrap;
    text-shadow: 0 1px 5px rgba(0,0,0,0.95);
}}
.old-price {{
    font-size: 10px;
    color: #7a8fa0;
    text-decoration: line-through;
    white-space: nowrap;
    margin-top: 2px;
    text-shadow: 0 1px 3px rgba(0,0,0,0.9);
}}
.badge {{
    font-size: 11px;
    font-weight: bold;
    padding: 2px 5px;
    border-radius: 3px;
    flex-shrink: 0;
    line-height: 1.4;
}}
.up   .new-price {{ color: #ff8080; }}
.down .new-price {{ color: #60b8f8; }}
.new  .new-price {{ color: #5cf0a0; }}
.up   .badge {{ background: rgba(255,82,82,0.2); color: #ff8080; border: 1px solid rgba(255,82,82,0.45); }}
.down .badge {{ background: rgba(58,172,240,0.2); color: #60b8f8; border: 1px solid rgba(58,172,240,0.45); }}
.new  .badge {{ background: rgba(0,230,118,0.2); color: #5cf0a0; border: 1px solid rgba(0,230,118,0.45); }}
/* ── 水印 ── */
.watermark {{
    text-align: right;
    font-size: 13px;
    font-weight: bold;
    color: rgba(255,255,255,0.75);
    letter-spacing: 1.5px;
    margin-top: 14px;
    padding-right: 4px;
    text-shadow:
        -1px -1px 0 rgba(0,0,0,0.8),
         1px -1px 0 rgba(0,0,0,0.8),
        -1px  1px 0 rgba(0,0,0,0.8),
         1px  1px 0 rgba(0,0,0,0.8),
         0    0   6px rgba(0,0,0,0.9);
}}
"""

async def _fetch_card_images(
    changes: list[dict],
    img_dir: str,
    concurrency: int = 20,
    retries: int = 3,
    timeout: int = 10,
) -> dict[int, str]:
    """
    并发下载所有卡图到 img_dir 目录，返回 {product_id: file:///... URL} 映射。
    下载失败的卡使用空字符串（会回退到原始远程 URL）。
    """
    os.makedirs(img_dir, exist_ok=True)
    product_ids = list({c["product_id"] for c in changes if c.get("product_id")})
    result: dict[int, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async def fetch_one(session: aiohttp.ClientSession, pid: int) -> None:
        dest = os.path.join(img_dir, f"{pid}.webp")
        # 已有缓存则直接用
        if os.path.exists(dest):
            result[pid] = "file:///" + dest.replace("\\", "/")
            return
        url = _CARD_IMAGE_URL.format(product_id=pid)
        for attempt in range(retries):
            try:
                async with sem:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            with open(dest, "wb") as f:
                                f.write(data)
                            result[pid] = "file:///" + dest.replace("\\", "/")
                            return
            except Exception:
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        result[pid] = ""  # 全部重试失败，回退到原始 URL

    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(*[fetch_one(session, pid) for pid in product_ids])

    return result


def _render_daily_report_html(
    changes: list[dict],
    date_str: str,
    image_map: dict[int, str] | None = None,
) -> list[str]:
    """
    将卡价变化列表渲染为 HTML 页面列表，每页 50 张卡（10×5）。
    返回 HTML 字符串列表，每个元素对应一页。
    """
    if not changes:
        return []

    bg_url     = _load_bg_image_b64()
    css        = _build_html_css(bg_url)
    image_map  = image_map or {}

    PAGE_SIZE  = 50
    up_count   = sum(1 for c in changes if c["change_type"] == "changed" and (c["price_diff"] or 0) > 0)
    down_count = sum(1 for c in changes if c["change_type"] == "changed" and (c["price_diff"] or 0) <= 0)
    new_count  = sum(1 for c in changes if c["change_type"] == "new")

    total_pages = (len(changes) + PAGE_SIZE - 1) // PAGE_SIZE
    pages = []

    for page_idx in range(total_pages):
        page       = changes[page_idx * PAGE_SIZE : (page_idx + 1) * PAGE_SIZE]
        page_label = f"&nbsp;&nbsp;<span style='font-size:12px;color:#556677'>（{page_idx + 1}/{total_pages}）</span>" if total_pages > 1 else ""

        cards_html_parts = []
        for c in page:
            product_id  = c.get("product_id", "")
            name        = html_mod.escape(c["name"])
            model_no    = html_mod.escape(c.get("model_number") or "")
            rarity_en   = html_mod.escape(rarity_jp_to_en(c.get("rarity") or ""))
            new_price   = c["new_price"]
            old_price   = c.get("old_price")
            change_type = c["change_type"]
            price_diff  = c.get("price_diff") or 0

            img_url = image_map.get(product_id) or _CARD_IMAGE_URL.format(product_id=product_id)

            if change_type == "new":
                css_cls  = "new"
                badge    = "NEW"
                new_str  = f"{new_price:,}円"
                old_html = ""
            elif price_diff > 0:
                css_cls  = "up"
                badge    = "↑"
                new_str  = f"{new_price:,}円"
                old_html = f'<div class="old-price">{old_price:,}円</div>'
            else:
                css_cls  = "down"
                badge    = "↓"
                new_str  = "0" if new_price == 0 else f"{new_price:,}円"
                old_html = f'<div class="old-price">{old_price:,}円</div>' if old_price else ""

            cards_html_parts.append(f"""
    <div class="card {css_cls}">
      <img class="card-img" src="{img_url}" loading="lazy">
      <div class="card-overlay">
        <div class="card-name">{name}</div>
        <div class="card-meta">{model_no} {rarity_en}</div>
        <div class="price-row">
          <div class="price-block">
            <div class="new-price">{new_str}</div>
            {old_html}
          </div>
          <span class="badge">{badge}</span>
        </div>
      </div>
    </div>""")

        cards_html = "".join(cards_html_parts)

        html_page = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<style>{css}</style>
</head>
<body>
<div class="content-wrap">
  <div class="header">
    <div class="header-eyebrow">CardRush · Daily Report</div>
    <div class="header-title">买取価格変動日報{page_label}</div>
    <div class="header-rule"></div>
    <div class="header-date">{date_str}</div>
    <div class="header-stats">
      <span class="stat-up">↑ 涨价 {up_count}</span>
      <span class="stat-sep">·</span>
      <span class="stat-down">↓ 降价 {down_count}</span>
      <span class="stat-sep">·</span>
      <span class="stat-new">★ 新增 {new_count}</span>
    </div>
  </div>
  <div class="grid">{cards_html}
  </div>
  <div class="watermark">Generated by 神人不神</div>
</div>
</body>
</html>"""
        pages.append(html_page)

    return pages


daily_report_html = on_cmd("卡价图报", priority=5, permission=SUPERUSER)

@daily_report_html.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()

    try:
        date_str = _parse_date_arg(arg_text) if arg_text else date.today().isoformat()

        loop = asyncio.get_event_loop()
        _query = functools.partial(get_daily_report_changes, date_str, exclude_prefixes=["RD/"])
        changes = await loop.run_in_executor(None, _query)

        if not changes:
            await daily_report_html.finish(f"【{date_str}】当日无价格变化记录。")
            return

        total_cards = len(changes)
        await bot.send(event, f"正在下载 {total_cards} 张卡图，请稍候…")
        img_dir = os.path.join(DATA_DIR, "card_images")
        image_map = await _fetch_card_images(changes, img_dir)

        pages = _render_daily_report_html(changes, date_str, image_map=image_map)
        out_dir = os.path.join(DATA_DIR, "daily_report_html")
        os.makedirs(out_dir, exist_ok=True)

        total = len(pages)
        await bot.send(event, f"下载完毕，正在渲染 {total} 页图报…")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            for i, page_html in enumerate(pages, 1):
                filename = f"cardrush_{date_str}_p{i}.html"
                filepath = os.path.join(out_dir, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(page_html)

                bpage = await browser.new_page(
                    viewport={"width": 1340, "height": 900}
                )
                bpage.set_default_timeout(120_000)
                file_url = "file:///" + filepath.replace("\\", "/")
                await bpage.goto(file_url, wait_until="domcontentloaded")
                # 等待字体就绪，避免 screenshot 内部超时
                await bpage.evaluate("document.fonts.ready")

                screenshot_bytes = await bpage.screenshot(
                    full_page=True,
                    animations="disabled",
                    timeout=120_000,
                )
                await bpage.close()

                b64 = base64.b64encode(screenshot_bytes).decode()
                await bot.send(event, MessageSegment.image(f"base64://{b64}"))

            await browser.close()

        # 渲染完毕，删除临时卡图缓存
        if os.path.isdir(img_dir):
            shutil.rmtree(img_dir, ignore_errors=True)

        await daily_report_html.finish(f"图报发送完毕（共 {total} 页）。")

    except ValueError as e:
        await daily_report_html.finish(str(e))
    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] daily_report_html error: {e}")
            await daily_report_html.finish(f"生成失败：{e}")


# ── 卡价日报（文字版） ─────────────────────────────────────────────────────────
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
    """将日报变化列表分三类（涨价/降价/新增）格式化为文字消息，每类最多 50 条。"""
    if not changes:
        return [f"【卡价日报 {date_str}】\n当日无价格变化记录。"]

    up:      list[dict] = []
    down:    list[dict] = []
    new:     list[dict] = []

    for c in changes:
        if c["change_type"] == "new":
            new.append(c)
        elif c["price_diff"] > 0:
            up.append(c)
        else:
            down.append(c)

    def _card_line(c: dict, kind: str) -> str:
        box    = (c.get("model_number") or "").split("-")[0] or "?"
        rarity = rarity_jp_to_en(c.get("rarity") or "")
        name   = c["name"]
        new_p  = c["new_price"]

        if kind == "new":
            return f"[新] {name} {box}-{rarity}：{new_p:,}円"

        old_p = c["old_price"]
        arrow = "↑" if c["price_diff"] > 0 else "↓"
        new_str = "0" if new_p == 0 else f"{new_p:,}円"
        return f"{arrow} {name} {box}-{rarity}：{old_p:,}円 → {new_str}"

    PAGE_SIZE = 50
    messages: list[str] = []

    total = len(changes)
    summary = f"【卡价日报 {date_str}】共 {total} 条变化（涨价 {len(up)} / 降价 {len(down)} / 新增 {len(new)}）"

    sections = [
        ("📈 涨价", up,   "up"),
        ("📉 降价/停收", down, "down"),
        ("🆕 新增", new,  "new"),
    ]

    for section_title, items, kind in sections:
        if not items:
            continue
        for page_idx in range(0, len(items), PAGE_SIZE):
            page = items[page_idx : page_idx + PAGE_SIZE]
            header_parts = [summary, f"\n{section_title}（{len(items)} 条）"]
            if len(items) > PAGE_SIZE:
                current = page_idx // PAGE_SIZE + 1
                total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
                header_parts.append(f"（{current}/{total_pages}）")
            lines = ["".join(header_parts)]
            lines.extend(_card_line(c, kind) for c in page)
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
        _query = functools.partial(get_daily_report_changes, date_str, exclude_prefixes=["RD/"])
        changes = await loop.run_in_executor(None, _query)
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
