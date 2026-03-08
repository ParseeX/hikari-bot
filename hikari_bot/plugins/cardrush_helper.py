import asyncio
import re
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException
from hikari_bot.utils.cardrush import query as query_card_prices, query_all, compare_prices, save_prices
from hikari_bot.utils.ygocard import get_card_info
from hikari_bot.utils.whitelist import message_superusers
from datetime import datetime

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
    """清理卡片名称，去掉所有符号和空格，只保留中文、英文、日文和数字"""
    if not name:
        return name
    # 先处理特定的全角符号
    name = name.replace('＜', '').replace('＞', '')
    
    # 保留中文、英文字母、日文假名/汉字、数字，去掉空格和所有符号
    # \u4e00-\u9fff: 中日韩统一表意文字 (汉字)
    # \u3040-\u309f: 日文平假名
    # \u30a0-\u30fa\u30fc-\u30ff: 片假名(排除\u30fb中点・)
    # a-zA-Z: 英文字母
    # 0-9: 数字
    cleaned = re.sub(r'[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ffa-zA-Z0-9]', '', name)
    return cleaned


card_price = on_command("卡价查询", aliases={"查卡价"}, priority=5)
@card_price.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not (input_text := args.extract_plain_text().strip()):
        await card_price.finish("请输入要查询的卡片名称！")
        return
    
    try:
        # 解析输入参数，支持多种格式
        # 格式1: 卡片名称
        # 格式2: 卡片名称 稀有度
        # 格式3: 卡片名称 稀有度 型号
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
        results = await loop.run_in_executor(None, query_card_prices, name_jp, rarity_jp, model_number)
        
        if not results:
            await card_price.finish(f"没有 {name_jp} 的价格信息！")
            return
        
        # 格式化查询结果
        reply_text = f"【{input_text}】的价格信息："
        
        for i, card in enumerate(results[:10]):  # 限制显示前10个结果
            card_name = card.get("name", "未知")
            card_price_val = card.get("price", "暂无")
            card_rarity_jp = card.get("rarity", "")
            card_model = card.get("model_number", "未知")
            
            card_rarity = translate_rarity_to_english(card_rarity_jp)
            
            reply_text += f"\n{card_name}【{card_model}({card_rarity})】\n"
            reply_text += f"    买取价格：{card_price_val}円"
        
        if len(results) > 10:
            reply_text += f"\n还有 {len(results) - 10} 个结果未显示..."
        
        await card_price.finish(reply_text)
        
    except Exception as e:
        if not isinstance(e, FinishedException):
            await card_price.finish(f"查询失败：{str(e)}")


# 定时价格监控任务
async def check_price_changes():
    """检查卡价变化并通知管理员"""
    try:
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
            await message_superusers(message)
        # else:
        #     await message_superusers("已执行卡价检查，暂无变化。")
        
        # 保存新价格到数据库
        save_prices(new_prices)
        
    except Exception as e:
        await message_superusers(f"卡价监控出错：{str(e)}")


async def schedule_price_monitor():
    """定时价格监控调度器"""
    while True:
        try:
            now = datetime.now()
            if now.minute in [1, 16, 31, 46] and now.second < 60:
                await check_price_changes()
                await asyncio.sleep(600)
            else:
                await asyncio.sleep(30)
        except Exception as e:
            print(f"价格监控调度器错误：{e}")
            await asyncio.sleep(300)  # 出错时等待5分钟


# 手动触发价格检查
price_check = on_command("检查卡价", permission=SUPERUSER)

@price_check.handle()
async def _(bot: Bot, event: MessageEvent):
    await check_price_changes()


# 启动价格监控
driver = get_driver()
@driver.on_bot_connect
async def _start_price_monitor(bot: Bot):
    asyncio.create_task(schedule_price_monitor())

