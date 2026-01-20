import base64
from io import BytesIO
import re
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from hikari_bot.utils.ygocard import *
from hikari_bot.utils.ygodeck import *

ygo_random_card = on_command("随机一卡", priority=5)
@ygo_random_card.handle()
async def _(bot: Bot, event: MessageEvent):
    image = await get_ygopic(random_card(), half=False)
    if not image:
        await ygo_random_card.finish("未找到随机卡片！")
        return
    image_base64 = base64.b64encode(image).decode('utf-8')
    await ygo_random_card.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


ygo_card_pic = on_command("查卡图", aliases={"游戏王卡图", "卡图"}, priority=5)
@ygo_card_pic.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        if is_card_id(input):
            card_id = int(input)
        else:
            if "异画" in input:
                input = input.replace("异画","")
                card_info = await get_card_info(input)
                if card_info:
                    card_id = card_info["id"] + 1
                else:
                    card_id = None
            else:
                card_info = await get_card_info(input)
                if card_info:
                    card_id = card_info["id"]
                else:
                    card_id = None
        
        if not card_id:
            await ygo_card_pic.finish("未找到对应卡片！")
            return

        image = await get_image_by_id(card_id)
        if not image:
            await ygo_card_pic.finish("卡图加载失败！")
            return
            
        image_base64 = base64.b64encode(image).decode('utf-8')
        await ygo_card_pic.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


ygo_card_id = on_command("查卡密", aliases={"游戏王卡密", "卡密"}, priority=5)
@ygo_card_id.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await get_card_info(input)
        
        if not card_info:
            await ygo_card_id.finish("查询失败！")
            return

        await ygo_card_id.finish(str(card_info["id"]))


ygo_card_effect = on_command("查效果", aliases={"游戏王效果", "效果"}, priority=5)
@ygo_card_effect.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await get_card_info(input)

        if not card_info:
            await ygo_card_effect.finish("未找到对应卡片！")
            return

        jp_name = card_info["jp_name"]
        cn_name = card_info["cn_name"]
        type = card_info["text"]["types"]
        p_effect = card_info["text"]["pdesc"]
        effect = card_info["text"]["desc"]

        result = f"{cn_name}（{jp_name}）\n{type}\n"
        if p_effect != "":
            result = result + p_effect + "\n"
        
        result = result + effect

        await ygo_card_effect.finish(result)


ygo_card_faq = on_command("查裁定", aliases={"游戏王裁定", "裁定"}, priority=5)
@ygo_card_faq.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip().split():
        card_info = await get_card_info(input[0])

        if not card_info:
            await ygo_card_faq.finish("未找到对应卡片！")
            return

        faq_ids = card_info["faqs"]

        if len(faq_ids) == 0:
            await ygo_card_faq.finish("暂无相关裁定！")
            return
        
        message_2 = []

        for faq_id in faq_ids:
            question, answer = await get_qa_by_id(faq_id)
            message_1 = []
            if question and answer:
                if len(input) > 1 and not input[1] in question and not input[1] in answer:
                    continue
                message_1.append({"type": "node", "data": {"name": "Q", "uin": event.user_id, "content": question}})
                message_1.append({"type": "node", "data": {"name": "A", "uin": bot.self_id, "content": answer}})
                try:
                    response = await bot.call_api(
                        "send_group_forward_msg",
                        group_id="347041546",
                        messages=message_1
                    )
                    message_id = response["message_id"]
                    message_2.append({"type": "node", "data": {"name": "Q&A", "uin": bot.self_id, "id": message_id}})

                    if len(message_2) == 10:
                        break

                except Exception as e:
                    await ygo_card_faq.finish("查询失败！")
                    return

        if len(message_2) == 0:
            await ygo_card_faq.finish("暂无相关裁定！")
            return
        
        group_id = getattr(event, "group_id", None)
        try:
            if group_id:  # 如果是群消息
                await bot.call_api("send_group_forward_msg", group_id=group_id, messages=message_2)
            else:  # 如果是私聊消息
                await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=message_2)
        except Exception as e:
            print(f"发送失败：{e}")



ygo_update_database = on_command("更新数据库", priority=5)
@ygo_update_database.handle()
async def _(bot: Bot, event: MessageEvent):
    update_db()
    await update_cdb()
    await ygo_update_database.finish("更新完成。")



ygo_metaltronus_calc = on_command("共界计算", priority=5)
@ygo_metaltronus_calc.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        card_info = await get_card_info(input)

        if not card_info:
            await ygo_metaltronus_calc.finish("未找到对应卡片！")
            return
        
        result = metaltronus_calc(card_info["id"])
        if not result:
            await ygo_metaltronus_calc.finish("没有满足条件的卡片！")
            return
        else:
            image = await generate_card_list_image(result)
            if not image:
                await ygo_metaltronus_calc.finish("结果加载失败！")
                return
                
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            await ygo_metaltronus_calc.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))

