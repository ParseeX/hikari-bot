import asyncio
import re
from datetime import datetime

from nonebot import get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers
from hikari_bot.services.price import compare_prices, query_all, save_prices, reset_database
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
        parts = input_text.split()
        name = parts[0]
        rarity = parts[1] if len(parts) > 1 else None
        model_number = parts[2] if len(parts) > 2 else None

        card_info = await get_card_info(name)

        if not card_info:
            await card_price.finish("未找到对应卡片！")
            return

        name_jp = clean_card_name(card_info["jp_name"])
        rarity_jp = translate_rarity_to_japanese(rarity)

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, search_local_prices, name_jp, rarity_jp, model_number
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
            reply_text += "\n（最多显示10条，可附加稀有度或型号缩小范围）"

        await card_price.finish(reply_text)

    except Exception as e:
        if not isinstance(e, FinishedException):
            await log_message(f"[cardrush] Exception occurred in card_price: {e}")
            await card_price.finish(f"查询失败：{str(e)}")


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