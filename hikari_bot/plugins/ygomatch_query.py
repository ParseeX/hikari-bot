import base64
import html
import io
import os

import aiohttp
from bs4 import BeautifulSoup

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

from hikari_bot.core.constants import DECK_DIR
from hikari_bot.services.ygodeck import (
    get_deck_text_from_url,
    is_deck_code,
    is_deck_url,
    save_deck_text_as_ydk,
)
from hikari_bot.services.ygomatch import *

ygomatch_search = on_command("比赛查询", priority=5)
@ygomatch_search.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if "神人杯" in args.extract_plain_text().strip():
        match_state = get_match_state()
        if not match_state:
            await ygomatch_search.finish("比赛信息获取失败！")
        match_id = match_state["match_id"]
        contestants = await get_contestants(match_id)

        await ygomatch_search.finish(f"""赛事名称：{match_state["match_name"]}
比赛码：{match_state["match_code"]}""")

#已签到人数：{len(match_state["checked_in"])}/{len(contestants)}

    elif keywords:=args.extract_plain_text().strip().split():
        search_result = await search_by_keyword(keywords[0])
        if not search_result:
            await ygomatch_search.finish("未查找到相关比赛！")
        for match in search_result:
            if all(keyword.lower() in match["name"].lower() for keyword in keywords):
                detail = await get_match_detail(match["id"])
                if not detail: return

                name = detail["basic_info"]["name"]
                start_time = detail["basic_info"]["start_at"]
                total_player = detail["player"]["player_count"]
                signed_player = detail["player"]["sign_count"]
                join_condition = detail["desc_info"]["join_condition"]
                prize = detail["desc_info"]["prize_desc"]
                prize_soup = BeautifulSoup(prize, 'html.parser')
                parsed_prize = "\n".join(line.strip() for line in prize_soup.stripped_strings)

                await ygomatch_search.finish(f"""赛事名称：{name}
开始时间：{start_time}
报名人数：{signed_player}/{total_player}
参赛条件：{join_condition}
奖励说明：
{parsed_prize}""")



ygomatch_avatar = on_command("头像压缩", priority=5)
@ygomatch_avatar.handle()
async def _(bot: Bot, event: MessageEvent):
    async def fetch_image(url: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                return None
    
    # 获取消息中的图片文件
    msg = event.get_message()
    image_file = None
    for seg in msg:
        if seg.type == "image":
            image_file = seg.data["file"]
            break

    if not image_file:
        # 如果没有图片，尝试获取用户头像
        user_id = event.get_user_id()
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        image_bytes = await fetch_image(avatar_url)
        if image_bytes is None:
            await ygomatch_avatar.finish("请发送一张正方形图片！")
    else:
        # 调用 get_file API 获取图片信息
        file_info = await bot.call_api("get_file", file=image_file)

        # 提取 Base64 数据
        base64_data = file_info.get("base64")
        if not base64_data:
            await ygomatch_avatar.finish("未找到图片数据，请重试！")
        image_bytes = base64.b64decode(base64_data)

    # 使用 Pillow 加载图片
    image = Image.open(io.BytesIO(image_bytes))

    # 检查是否是正方形
    if image.width != image.height:
        await ygomatch_avatar.finish("请发送一张正方形图片！")
 
    # 调整大小到 150x150
    image = image.resize((150, 150), Image.Resampling.LANCZOS)

    # 将图片转换为字节流
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    # 将处理后的图片发送回用户
    await bot.send(event, MessageSegment.image(buffer))



check_in = on_command("比赛签到", aliases={"签到"}, priority=5)
@check_in.handle()
async def _(bot: Bot, event: PrivateMessageEvent, state: T_State, matcher: Matcher, arg: Message = CommandArg()):
    user_id = str(event.user_id)
    xcx_name = str(arg).strip()
    
    if not xcx_name:
        await check_in.finish("""神人杯报名详细流程：
1. 查询比赛码：发送"比赛查询 神人杯"即可查询到本周比赛的比赛码；
2. 在查卡器小程序上搜索比赛码，进行报名；
3. 发送"签到 XXX"，其中XXX是你的小程序用户名；
4. 向机器人发送你的卡组。可以是YGOMobile生成的链接，也可以是ydk文件，也可以是ydk文件里面的文本；
5. 签到完成后，可以用[比赛卡组确认]来确认你的卡组。如果需要修改卡组，可以重新从签到开始操作一遍；
6. 每轮开始时机器人会在群里@对应成员，请留意避免错过对局；
7. 如果需要退赛，直接私聊bot"退赛"即可。""")

    match_state = get_match_state()
    if not match_state:
        await check_in.finish("比赛信息获取失败！")

    match_id = match_state["match_id"]
    users = match_state["user_states"]
    checked_in = match_state["checked_in"]

    info = await get_tournament_info(match_id, match_state["match_code"])
    if not info:
        await check_in.finish("比赛信息获取失败！")

    if info["status"] == "ongoing":
        await check_in.finish(f"比赛已经开始！")
    if info["status"] == "end":
        await check_in.finish(f"本届比赛已经结束，请等待下一届报名！")

    contestants = await get_contestants(match_id)

    xcx_id = None
    for contestant in contestants:
        if contestant["name"] == xcx_name:
            xcx_id = contestant["id"]
            break
    
    if not xcx_id:
        await check_in.finish(f"未找到ID【{xcx_name}】的报名信息，请先在查卡器小程序报名！比赛码：{match_state['match_code']}")

    if xcx_name in checked_in:
        existing_user = checked_in[xcx_name]
        if existing_user != user_id:
            await check_in.finish(f"参赛ID【{xcx_name}】已被其他用户使用，请更换ID或联系赛事管理员。")

    users[user_id] = {"xcx_name": xcx_name, "xcx_id": xcx_id, "state": "waiting_for_deck"}

    save_match_state(match_state)

    matcher.stop_propagation()
    await check_in.finish(f"您的参赛ID为【{xcx_name}】，请提交您比赛使用的卡组（链接或文件）：")



quit = on_command("退赛", priority=5)
@quit.handle()
async def _(bot: Bot, event: PrivateMessageEvent, state: T_State, matcher: Matcher):
    user_id = str(event.user_id)

    match_state = get_match_state()
    if not match_state:
        await quit.finish("比赛信息获取失败！")
    
    info = await get_tournament_info(match_state["match_id"], match_state["match_code"])
    if not info:
        await quit.finish("比赛信息获取失败！")

    users = match_state["user_states"]
    checked_in = match_state["checked_in"]
    xcx_name = users[user_id]["xcx_name"]
    xcx_id = users[user_id]["xcx_id"]

    if users[user_id]["state"] == "finish_check_in":
        result = await match_quit(xcx_id)
        if not result:
            await quit.finish("退赛失败，请重试!")
        
        if info["status"] == "pending":
            del checked_in[xcx_name]
            del users[user_id]
            save_match_state(match_state)
            file_path = os.path.join(DECK_DIR, f"{xcx_name}.ydk")
            os.remove(file_path)
        
        await quit.finish("您已退出比赛。")



ygo_match_refresh = on_command("新建比赛", priority=5, permission=SUPERUSER)
@ygo_match_refresh.handle()
async def _(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    match_name = str(arg).strip()
    code, id = await start_tournament(match_name)
    if id:
        reset_match_state(match_name, id, code)
        await ygo_match_refresh.finish(f"已开启比赛【{match_name}】，比赛码：{code}")



collect_deck = on_message(priority=10)

@collect_deck.handle()
async def _(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    match_state = get_match_state()
    if not match_state:
        return
    
    users = match_state["user_states"]
    checked_in = match_state["checked_in"]

    # 检查用户状态
    if user_id not in users or users[user_id].get("state") != "waiting_for_deck":
        return  # 用户不在等待提交卡组的状态，忽略消息

    xcx_name = users[user_id]["xcx_name"]
    xcx_id = users[user_id]["xcx_id"]

    msg = event.get_message()

    for seg in msg:
        if seg.type == "text":
            text = seg.data["text"]
            if is_deck_url(text):
                deck_text = get_deck_text_from_url(text)
            elif is_deck_code(text):
                deck_text = text
            else:
                await collect_deck.finish("请提交正确的卡组链接！")

            if deck_text:
                result = await match_check_in(xcx_id)
                if not result:
                    await collect_deck.finish("签到失败，请重试!")

                os.makedirs(DECK_DIR, exist_ok=True)
                file_path = os.path.join(DECK_DIR, f"{xcx_name}.ydk")
                save_deck_text_as_ydk(deck_text, file_path)
                users[user_id]["state"] = "finish_check_in"
                checked_in[xcx_name] = user_id
                save_match_state(match_state)
                await collect_deck.finish("签到成功!")

        elif seg.type == "file":
            # 如果是文件，保存到本地
            file_id = seg.data["file_id"]

            # 调用 get_file API 获取文件信息
            file_info = await bot.call_api("get_file", file=file_id)
            file_path = file_info.get("file")
            
            if file_path and os.path.exists(file_path):
                # 直接从文件路径读取内容
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    deck_text = f.read()
            
                if is_deck_code(deck_text):
                    result = await match_check_in(xcx_id)
                    if not result:
                        await collect_deck.finish("签到失败，请重试!")

                    os.makedirs(DECK_DIR, exist_ok=True)
                    file_path = os.path.join(DECK_DIR, f"{xcx_name}.ydk")
                    save_deck_text_as_ydk(deck_text, file_path)
                    users[user_id]["state"] = "finish_check_in"
                    checked_in[xcx_name] = user_id
                    save_match_state(match_state)
                    await collect_deck.finish("签到成功!")
                else:
                    await collect_deck.finish("文件内容不是有效的卡组文件。")



confirm_deck = on_command("比赛卡组确认", aliases={"卡组确认"}, priority=5)

@confirm_deck.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.user_id)
    
    match_state = get_match_state()
    if not match_state:
        await confirm_deck.finish("比赛信息获取失败！")

    users = match_state["user_states"]
    checked_in = match_state["checked_in"]
    xcx_name = None
    deck_name = ""
    result = ""

    if user_id in users and users[user_id]["state"] == "finish_check_in":
        xcx_name = users[user_id]["xcx_name"]

    if texts:=args.extract_plain_text().strip().split():
        if not checked_in.get(texts[0]):
            return
        elif user_id == "909333601":
            xcx_name = texts[0]
        elif not xcx_name or xcx_name != texts[0]:
            await confirm_deck.finish("你没有权限查看其他人的卡组！")
        if len(texts) >= 2:
            deck_name = texts[1]
        if len(texts) >= 3 and user_id == "909333601":
            result = texts[2]
    
    if not xcx_name:
        await confirm_deck.finish("你尚未提交卡组！")

    file_path = os.path.join(DECK_DIR, f"{xcx_name}.ydk")

    with open(file_path, "r", encoding="utf-8-sig") as f:
        deck_text = f.read()

    deck_img = await generate_deck_image(deck_text, xcx_name, match_state["match_name"], deck_name=deck_name, result=result)
    if not deck_img:
        await confirm_deck.finish("卡组图像生成失败！")

    buffer = io.BytesIO()
    deck_img.save(buffer, format="PNG")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.read()).decode('utf-8')

    await confirm_deck.finish(Message([MessageSegment("image", {"file": f"base64://{image_base64}"})]))



generate_all_deck_image = on_command("环境统计", priority=5, permission=SUPERUSER)
@generate_all_deck_image.handle()
async def _(bot: Bot, event: MessageEvent):
    match_state = get_match_state()
    if not match_state:
        await generate_all_deck_image.finish("比赛信息获取失败！")
    checked_in = match_state["checked_in"]
    for xcx_name in checked_in.keys():
        deck_file_path = os.path.join(DECK_DIR, f"{xcx_name}.ydk")
        with open(deck_file_path, "r", encoding="utf-8") as f:
            deck_text = f.read()
        deck_img = await generate_deck_image(deck_text, xcx_name, match_state["match_name"])
        if deck_img:
            output_directory = os.path.join(DECK_DIR, "pics")
            os.makedirs(output_directory, exist_ok=True)
            deck_img.save(os.path.join(output_directory, f"{xcx_name}.png"))



pairing_info = on_command("对阵信息", priority=5, permission=SUPERUSER)
@pairing_info.handle()
async def _(bot: Bot, event: MessageEvent):
    match_state = get_match_state()
    if not match_state:
        await pairing_info.finish("比赛信息获取失败！")

    match_id = match_state["match_id"]
    checked_in = match_state["checked_in"]

    info = await get_tournament_info(match_id, match_state["match_code"])
    if not info:
        await pairing_info.finish("比赛信息获取失败！")
    
    current_round = info["current_round"]
    pairing = await get_pairing(match_id, current_round)
    if not pairing:
        await pairing_info.finish("配对信息获取失败！")

    if len(pairing) == 1:
        battle_name = "决赛"
    elif len(pairing) == 2:
        battle_name = "半决赛"
    elif len(pairing) == 4:
        battle_name = "8进4"
    elif len(pairing) == 8:
        battle_name = "16进8"
    else:
        battle_name = f"瑞士轮第{current_round}轮"
    
    result_message = f"{battle_name}对阵信息："
    for battle in pairing:
        desk = battle["desk"]
        
        if battle["a"] == "轮空":
            qq_b = checked_in[battle["b"]]
            at_b = MessageSegment.at(int(qq_b))
            result_message += f"\n轮空：{at_b}"
        elif battle["b"] == "轮空":
            qq_a = checked_in[battle["a"]]
            at_a = MessageSegment.at(int(qq_a))
            result_message += f"\n轮空：{at_a}"
        else:
            qq_a = checked_in[battle["a"]]
            qq_b = checked_in[battle["b"]]

            at_a = MessageSegment.at(int(qq_a))
            at_b = MessageSegment.at(int(qq_b))

            result_message += f"\n第{desk}桌：{at_a} vs {at_b}"

    await pairing_info.finish(Message(result_message))



deck_list = on_command("卡表", aliases={"中文卡表", "简中卡表", "日文卡表", "英文卡表"}, priority=5)
@deck_list.handle()
async def _(bot: Bot, event: PrivateMessageEvent):
    await deck_list.finish("请访问最新网页版 http://ygo.xyk.one/deck")

# async def _(bot: Bot, event: PrivateMessageEvent, arg: Message = CommandArg()):
#     qq = str(event.user_id)
#     text = html.unescape(str(arg).strip())
#     if is_deck_url(text):
#         deck_text = get_deck_text_from_url(text)
#     elif is_deck_code(text):
#         deck_text = text
#     else:
#         await deck_list.finish("用法：卡表 \"YGOM卡组链接\"/\"YDK卡组内容\"")
#     if deck_text:
#         friend_list = await bot.call_api("get_friend_list")
#         if not any(str(friend["user_id"]) == qq for friend in friend_list):
#             await deck_list.finish("未添加好友无法发送文件，请先添加好友！")
#         file_name = qq+"_sc.pdf"
#         file_path = await generate_deck_list_pdf(deck_text, "sc", file_name=file_name)
#         if not file_path:
#             await deck_list.finish("生成失败，请检查卡组内容或重试！")
#         record_deck_usage(qq)
#         await bot.upload_private_file(user_id=qq, file=file_path, name=file_name)

# jp_deck_list = on_command("生成日文卡表", aliases={"日文卡表"}, priority=5)

# @jp_deck_list.handle()
# async def _(bot: Bot, event: PrivateMessageEvent, arg: Message = CommandArg()):
#     qq = str(event.user_id)
#     text = html.unescape(str(arg).strip())
#     if is_deck_url(text):
#         deck_text = get_deck_text_from_url(text)
#     elif is_deck_code(text):
#         deck_text = text
#     else:
#         await jp_deck_list.finish("用法：日文卡表 \"YGOM卡组链接\"/\"YDK卡组内容\"")
#     if deck_text:
#         friend_list = await bot.call_api("get_friend_list")
#         if not any(str(friend["user_id"]) == qq for friend in friend_list):
#             await jp_deck_list.finish("未添加好友无法发送文件，请先添加好友！")
#         file_name = qq+"_jp.pdf"
#         file_path = await generate_deck_list_pdf(deck_text, "jp", file_name=file_name)
#         if not file_path:
#             await deck_list.finish("生成失败，请检查卡组内容或重试！")
#         record_deck_usage(qq)
#         await bot.upload_private_file(user_id=qq, file=file_path, name=file_name)

# en_deck_list = on_command("生成英文卡表", aliases={"英文卡表", "tcg卡表", "TCG卡表"}, priority=5)

# @en_deck_list.handle()
# async def _(bot: Bot, event: PrivateMessageEvent, arg: Message = CommandArg()):
#     qq = str(event.user_id)
#     text = html.unescape(str(arg).strip())
#     if is_deck_url(text):
#         deck_text = get_deck_text_from_url(text)
#     elif is_deck_code(text):
#         deck_text = text
#     else:
#         await en_deck_list.finish("用法：英文卡表 \"YGOM卡组链接\"/\"YDK卡组内容\"")
#     if deck_text:
#         friend_list = await bot.call_api("get_friend_list")
#         if not any(str(friend["user_id"]) == qq for friend in friend_list):
#             await en_deck_list.finish("未添加好友无法发送文件，请先添加好友！")
#         file_name = qq+"_en.pdf"
#         file_path = await generate_deck_list_pdf(deck_text, "en", file_name=file_name)
#         if not file_path:
#             await deck_list.finish("生成失败，请检查卡组内容或重试！")
#         record_deck_usage(qq)
#         await bot.upload_private_file(user_id=qq, file=file_path, name=file_name)



deck_pic = on_command("生成卡组图片", aliases={"卡组图片", "卡组"}, priority=5)
# 卡组 http xxx 比赛 卡组名称 成绩
@deck_pic.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    player_id = ""
    match_name = ""
    deck_name = ""
    result = ""

    if texts:=html.unescape(args.extract_plain_text()).strip().split():
        text = texts[0]
        if is_deck_url(text):
            deck_text = get_deck_text_from_url(text)
        else:
            return
        if len(texts) >= 2:
            player_id = texts[1]
        if len(texts) >= 3:
            match_name = texts[2]
        if len(texts) >= 4:
            deck_name = texts[3]
        if len(texts) >= 5:
            result = texts[4]
    else:
        return

    deck_img = await generate_deck_image(deck_text, player_id, match_name, deck_name=deck_name, result=result)
    if not deck_img:
        await deck_pic.finish("卡组图像生成失败！")

    buffer = io.BytesIO()
    deck_img.save(buffer, format="PNG")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.read()).decode('utf-8')

    await deck_pic.finish(Message([MessageSegment("image", {"file": f"base64://{image_base64}"})]))