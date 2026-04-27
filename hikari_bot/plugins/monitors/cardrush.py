import asyncio
import base64
import re
from datetime import datetime
from io import BytesIO

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from nonebot import get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.params import Arg, CommandArg
from nonebot.typing import T_State
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers
from hikari_bot.services.price import (
    compare_prices,
    get_price_history,
    query_all,
    reset_database,
    save_prices,
)
from hikari_bot.services.price import search_local_prices
from hikari_bot.services.ygocard import get_card_info

# 稀有度映射表：日文名称 → 英文缩写 (支持多个日文对应同一个英文)
RARITY_MAPPING = {
    "ノーマル": "N",
    "レア": "R", 
    "スーパー": "SR",
    "ウルトラ": "UR",
    "レリーフ": "UTR",
    "コレクターズ": "CR",
    "プレミアムゴールド": "GR",
    "ホログラフィック": "HR",
    "シークレット": "SER",
    "エクストラシークレット": "ESR",
    "プリズマティックシークレット": "PSER",
    "クォーターセンチュリーシークレット": "QCSER",
    "20thシークレット": "20SER",
    "ゴールドシークレット": "GSER",
    "10000シークレット": "10000SER",

    "ノーマルパラレル": "NPR",
    "ウルトラパラレル": "UPR",
    "ホログラフィックパラレル": "HPR",
    "シークレットパラレル": "SEPR",

    "ウルトラシークレット": "USR",
    "KCウルトラ": "UKC",

    "シークレットSPECIALREDVer.": "SER-SRV",

    "ウルトラブルー": "UR",
    "ウルトラレッド": "UR",
    "ウルトラSPECIALPURPLEVer.": "UR",
    "ウルトラSPECIALILLUSTVer.": "UR",

    "クォーターセンチュリーシークレットGREEN Ver.": "QCSER",

    "OFウルトラ": "UR-OF",
    "OFプリズマティックシークレット": "PSER-OF",
    "グランドマスター": "GMR-OF",
}

def translate_rarity_to_japanese(rarity_en):
    """将英文稀有度缩写转换为日文名称（用于API查询）"""
    if not rarity_en:
        return None
    rarity_upper = rarity_en.upper()
    for jp, en in RARITY_MAPPING.items():
        if en == rarity_upper:
            return jp
    return rarity_en

def translate_rarity_to_english(rarity_jp):
    """将日文稀有度名称转换为英文缩写（用于结果显示）"""
    if not rarity_jp:
        return "未知"
    return RARITY_MAPPING.get(rarity_jp, rarity_jp)


def parse_price_query(input_text: str) -> tuple[str, str | None, str | None]:
    """
    从用户输入末尾剥离稀有度/盒子编号过滤词，其余部分原样作为卡名。

    剥离规则（从末尾向前，至少保留一个 token 作为卡名）：
    - 匹配已知稀有度缩写（大小写不敏感）→ 稀有度过滤词
    - 原文全大写且形如盒子编号（2-6 字母 + 可选 0-2 位数字）→ 盒子编号过滤词
    - 否则停止剥离

    示例：
      青眼白龙              → name=青眼白龙
      青眼白龙 UR           → name=青眼白龙,  rarity=UR
      青眼白龙 ALIN         → name=青眼白龙,  model=ALIN
      青眼白龙 UR ALIN      → name=青眼白龙,  rarity=UR, model=ALIN
      青眼白龙 ALIN UR      → name=青眼白龙,  rarity=UR, model=ALIN
      Blue-Eyes White Dragon UR ALIN → name=Blue-Eyes White Dragon, rarity=UR, model=ALIN
    """
    tokens = input_text.split()
    rarity_en: str | None = None
    model_prefix: str | None = None

    while len(tokens) > 1:
        last = tokens[-1]
        last_upper = last.upper()

        # 稀有度：大小写不敏感，只要有任意一个已知英文缩写以此为前缀即视为稀有度词
        if rarity_en is None:
            if any(en.upper().startswith(last_upper) for en in RARITY_MAPPING.values()):
                rarity_en = last_upper
                tokens.pop()
                continue

        # 盒子编号：原文必须全大写，形如 ALIN / RC04 / DUNE
        if model_prefix is None and re.match(r'^[A-Z]{2,6}[0-9]{0,2}$', last):
            model_prefix = last
            tokens.pop()
            continue

        break

    return ' '.join(tokens), rarity_en, model_prefix


def _draw_price_chart(history: list[dict]) -> bytes:
    """将价格历史列表按真实时间间距绘制成折线图，返回 PNG 字节。"""
    dates = []
    prices = []
    for record in history:
        try:
            dt = datetime.fromisoformat(record["changed_at"])
            dates.append(dt)
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

    # X 轴：按实际时间，自动选合适的刻度粒度
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.autofmt_xdate(rotation=30, ha="right")

    # Y 轴千分位
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_ylabel("JPY")

    # 标注首尾价格
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

def clean_card_name(name):
    """清理卡片名称，去掉所有符号和空格，只保留中文、英文、日文、数学符号和数字"""
    if not name:
        return name
    # 先处理特定的全角符号
    name = name.replace('＜', '').replace('＞', '')
    
    # 保留中文、英文字母、日文假名/汉字、数字，以及特定的数学符号如∀
    # \u4e00-\u9fff: 中日韩统一表意文字 (汉字)
    # \u3040-\u309f: 日文平假名
    # \u30a0-\u30fa\u30fc-\u30ff: 片假名(排除\u30fb中点・)
    # \u2200-\u22ff: 数学符号块（包含∀等符号）
    # a-zA-Z: 英文字母
    # 0-9: 数字
    cleaned = re.sub(r'[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff\u2200-\u22ffa-zA-Z0-9]', '', name)
    return cleaned


card_price = on_command("卡价查询", aliases={"查卡价"}, priority=5)
@card_price.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not (input_text := args.extract_plain_text().strip()):
        await card_price.finish("请输入要查询的卡片名称！")
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)

        card_info = await get_card_info(name)
        if card_info:
            name_jp = clean_card_name(card_info["jp_name"])
        else:
            name_jp = name
        rarity_jp_list = (
            [jp for jp, en in RARITY_MAPPING.items() if en.upper().startswith(rarity_en)]
            if rarity_en else None
        )

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, search_local_prices, name_jp, rarity_jp_list, model_prefix
        )

        if not results:
            await card_price.finish(f"暂无 {name_jp} 的价格信息。")
            return

        reply_text = f"【{name_jp}】的价格信息："
        for card in results[:10]:
            card_rarity = translate_rarity_to_english(card.get("rarity") or "")
            raw_model = card.get("model_number") or ""
            card_model = raw_model.split("-")[0] if raw_model else "未知"
            changed_at = card.get("changed_at") or ""
            date_str = changed_at[:10] if changed_at else "未知"
            reply_text += f"\n{card_model}-{card_rarity}\n"
            reply_text += f"    {card['price']}円（{date_str}）"

        if len(results) == 10:
            reply_text += "\n（最多显示10条，可附加稀有度或盒子编号缩小范围）"

        await card_price.finish(reply_text)

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] Exception occurred in card_price: {e}")
            await card_price.finish(f"查询失败：{str(e)}")


# ── 卡价曲线 ──────────────────────────────────────────────────────────────────

price_curve = on_command("卡价曲线", aliases={"历史卡价", "卡价历史"}, priority=5)

@price_curve.handle()
async def price_curve_start(
    bot: Bot, event: MessageEvent,
    args: Message = CommandArg(),
    state: T_State = ...,
):
    if not (input_text := args.extract_plain_text().strip()):
        await price_curve.finish("请输入卡片名称！例如：卡价曲线 青眼白龙")
        return

    try:
        name, rarity_en, model_prefix = parse_price_query(input_text)

        card_info = await get_card_info(name)
        if card_info:
            name_jp = clean_card_name(card_info["jp_name"])
        else:
            name_jp = name
        rarity_jp_list = (
            [jp for jp, en in RARITY_MAPPING.items() if en.upper().startswith(rarity_en)]
            if rarity_en else None
        )

        loop = asyncio.get_event_loop()
        # 多查一条，用于判断是否"过多"
        results = await loop.run_in_executor(
            None, search_local_prices, name_jp, rarity_jp_list, model_prefix, 11
        )

        if not results:
            await price_curve.finish(f"暂无 {name_jp} 的价格历史记录。")
            return

        if len(results) > 10:
            await price_curve.finish(
                f"找到超过10条匹配结果，请附加稀有度（如 UR）或盒子编号（如 ALIN）缩小范围。"
            )
            return

        if len(results) == 1:
            # 直接绘制，预填 _choice 跳过交互
            state["_selected"] = results[0]
            state["_choice"] = "1"
        else:
            state["_candidates"] = results
            list_text = "\n".join(
                f"{i + 1}. {r['name']}  {(r['model_number'] or '').split('-')[0]}"
                f"-{translate_rarity_to_english(r['rarity'] or '')}  {r['price']:,}円"
                for i, r in enumerate(results)
            )
            await price_curve.send(f"找到 {len(results)} 条结果，请回复编号：\n{list_text}")

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] price_curve_start error: {e}")
            await price_curve.finish(f"查询失败：{str(e)}")


@price_curve.got("_choice")
async def price_curve_draw(
    bot: Bot, event: MessageEvent,
    state: T_State = ...,
    choice: Message = Arg("_choice"),
):
    try:
        if "_selected" in state:
            selected = state["_selected"]
        else:
            candidates = state.get("_candidates", [])
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
        rarity_en = translate_rarity_to_english(rarity)
        display_name = f"{name}  {box_code}-{rarity_en}"

        img_bytes = _draw_price_chart(history)
        if not img_bytes:
            await price_curve.finish("绘制图表失败。")
            return

        current_price = history[-1]["price"]
        text = (
            f"{display_name}\n"
            f"当前买取价：{current_price:,}円"
            f"（共 {len(history)} 条记录）"
        )
        img_b64 = base64.b64encode(img_bytes).decode()
        await price_curve.finish(Message([
            MessageSegment.text(text),
            MessageSegment.image(f"base64://{img_b64}"),
        ]))

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] price_curve_draw error: {e}")
            await price_curve.finish(f"绘制失败：{str(e)}")


async def check_price_changes():
    """检查卡价变化并通知管理员"""
    # 获取最新价格
    new_prices = query_all()    
    # 比较价格变化
    changes = compare_prices(new_prices)

    if changes:
        message = "🔔卡价变化通知：\n"
        
        for change in changes[:100]:  # 限制显示前100个变化
            name = change["name"]
            rarity = change["rarity"] or "未知"
            model_number = change["model_number"] or "未知"
            
            if change["change_type"] == "new":
                message += f"🆕{name}【{model_number}({rarity})】\n"
                message += f"   0円 → {change['new_price']}円\n"
            elif change["change_type"] == "changed":
                old_price = change["old_price"]
                new_price = change["new_price"]
                diff = change["price_diff"]
                
                if diff > 0:
                    emoji = "📈"
                else:
                    emoji = "📉"
                
                message += f"{emoji}{name}【{model_number}({rarity})】\n"
                message += f"  {old_price}円 → {new_price}円\n"
            elif change["change_type"] == "deleted":
                message += f"🗑️{name}【{model_number}({rarity})】\n"
                message += f"  {change['old_price']}円 → 0円\n"
        
        if len(changes) > 100:
            message += f"还有 {len(changes) - 100} 个变化未显示..."
        
        # 发送通知给管理员
        # await message_superusers(message)
        # 保存新价格到数据库
        save_prices(new_prices)

    await log_message("[cardrush_monitor] Finish checking with %d change(s)." % len(changes))


async def scheduled_price_check():
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            await check_price_changes()
            break
        except Exception as e:
            retry_count += 1
            #await log_message(f"[cardrush_monitor] check_price_changes error (attempt {retry_count}/{max_retries}): {str(e)}")
            if retry_count < max_retries:
                await asyncio.sleep(60)
            else:
                await log_message(f"[cardrush_monitor] Failed to check price changes after {max_retries} attempts")


@scheduler.scheduled_job("interval", minutes=5, id="cardrush_price_monitor", misfire_grace_time=300)
async def _scheduled_job():
    await scheduled_price_check()


price_check = on_command("检查卡价", permission=SUPERUSER)
@price_check.handle()
async def _(bot: Bot, event: MessageEvent):
    await scheduled_price_check()


reset_db = on_command("重置卡价数据库", permission=SUPERUSER)
@reset_db.handle()
async def _(bot: Bot, event: MessageEvent):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, reset_database)
    await bot.send(event, "数据库已清空重建，开始重新抓取全站数据…")
    await scheduled_price_check()
    await bot.send(event, "数据抓取完成。")

driver = get_driver()
@driver.on_bot_connect
async def _startup_price_check(bot: Bot):
    await log_message("[cardrush_monitor] CardRush monitor started.")
    await scheduled_price_check()