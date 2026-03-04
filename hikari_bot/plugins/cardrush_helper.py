from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from hikari_bot.utils.cardrush import query

# 稀有度映射表：英文缩写 ↔ 日文名称
RARITY_MAPPING = {
    "SER": "シーク",
    "SR": "スーパー",
}

def translate_rarity_to_japanese(rarity_en):
    """将英文稀有度缩写转换为日文名称（用于API查询）"""
    if not rarity_en:
        return None
    return RARITY_MAPPING.get(rarity_en.upper(), rarity_en)

def translate_rarity_to_english(rarity_jp):
    """将日文稀有度名称转换为英文缩写（用于结果显示）"""
    if not rarity_jp:
        return "未知"
    # 反向查找：通过值找键
    for en, jp in RARITY_MAPPING.items():
        if jp == rarity_jp:
            return en
    return rarity_jp


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
        
        # 将英文稀有度缩写转换为日文（用于API查询）
        rarity_jp = translate_rarity_to_japanese(rarity)
        
        # 调用cardrush查询接口
        results = query(name=name, rarity=rarity_jp, model_number=model_number)
        
        if not results:
            await card_price.finish(f"未找到相关卡片：{input_text}")
            return
        
        # 格式化查询结果
        reply_text = f"【{input_text}】的价格信息：\n\n"
        
        for i, card in enumerate(results[:10]):  # 限制显示前10个结果
            card_name = card.get("name", "未知")
            card_price_val = card.get("price", "暂无")
            card_rarity_jp = card.get("rarity", "")
            card_model = card.get("model_number", "未知")
            
            # 将日文稀有度转换为英文缩写显示
            card_rarity_display = translate_rarity_to_english(card_rarity_jp)
            
            reply_text += f"{i+1}. {card_name}\n"
            reply_text += f"   价格：{card_price_val}円\n"
            reply_text += f"   稀有度：{card_rarity_display}\n"
            reply_text += f"   型号：{card_model}\n\n"
        
        if len(results) > 10:
            reply_text += f"还有 {len(results) - 10} 个结果未显示..."
        
        await card_price.finish(reply_text)
        
    except Exception as e:
        await card_price.finish(f"查询失败：{str(e)}")


# 高级查询指令，支持更精确的参数指定
card_price_advanced = on_command("高级卡价查询", aliases={"精确卡价"}, priority=5)
@card_price_advanced.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not (input_text := args.extract_plain_text().strip()):
        help_msg = """高级卡价查询使用方法：
格式：高级卡价查询 [参数]

支持的参数格式：
1. name:卡片名称
2. rarity:稀有度 (支持SER/SR等英文缩写)
3. model:型号编号

例如：
高级卡价查询 name:增援 rarity:SER
高级卡价查询 model:RC04
高级卡价查询 name:青眼白龙 rarity:SR"""
        
        await card_price_advanced.finish(help_msg)
        return
    
    try:
        # 解析高级参数
        name = None
        rarity = None
        model_number = None
        
        # 支持 name:xxx rarity:xxx model:xxx 格式
        params = input_text.split()
        for param in params:
            if ":" in param:
                key, value = param.split(":", 1)
                if key.lower() == "name":
                    name = value
                elif key.lower() == "rarity":
                    rarity = value
                elif key.lower() == "model":
                    model_number = value
        
        # 如果没有找到参数格式，则将整个输入作为卡片名称
        if not any([name, rarity, model_number]):
            name = input_text
        
        # 将英文稀有度缩写转换为日文（用于API查询）
        rarity_jp = translate_rarity_to_japanese(rarity)
        
        # 调用cardrush查询接口
        results = query(name=name, rarity=rarity_jp, model_number=model_number)
        
        if not results:
            await card_price_advanced.finish(f"未找到相关卡片：{input_text}")
            return
        
        # 格式化查询结果
        query_info = []
        if name:
            query_info.append(f"名称:{name}")
        if rarity:
            query_info.append(f"稀有度:{rarity}")
        if model_number:
            query_info.append(f"型号:{model_number}")
        
        reply_text = f"【{' '.join(query_info)}】的价格信息：\n\n"
        
        for i, card in enumerate(results[:15]):  # 高级查询显示更多结果
            card_name = card.get("name", "未知")
            card_price_val = card.get("price", "暂无")
            card_rarity_jp = card.get("rarity", "")
            card_model = card.get("model_number", "未知")
            
            # 将日文稀有度转换为英文缩写显示
            card_rarity_display = translate_rarity_to_english(card_rarity_jp)
            
            reply_text += f"{i+1}. {card_name}\n"
            reply_text += f"   价格：{card_price_val}円\n"
            reply_text += f"   稀有度：{card_rarity_display}\n"
            reply_text += f"   型号：{card_model}\n\n"
        
        if len(results) > 15:
            reply_text += f"还有 {len(results) - 15} 个结果未显示..."
        
        await card_price_advanced.finish(reply_text)
        
    except Exception as e:
        await card_price_advanced.finish(f"查询失败：{str(e)}")
