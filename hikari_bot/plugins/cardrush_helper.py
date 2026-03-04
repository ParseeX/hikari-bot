import asyncio
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from nonebot.exception import FinishedException
from hikari_bot.utils.cardrush import query as query_card_prices
from hikari_bot.utils.ygocard import get_card_info

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

        name_jp = card_info["jp_name"]
        
        rarity_jp = translate_rarity_to_japanese(rarity)
        
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, query_card_prices, name_jp, rarity_jp, model_number)
        
        if not results:
            await card_price.finish(f"没有对应卡片的价格信息！")
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
            reply_text += f"  买取价格：{card_price_val}円"
        
        if len(results) > 10:
            reply_text += f"还有 {len(results) - 10} 个结果未显示..."
        
        await card_price.finish(reply_text)
        
    except Exception as e:
        if not isinstance(e, FinishedException):
            await card_price.finish(f"查询失败：{str(e)}")
