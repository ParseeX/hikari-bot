"""
mycard_query.py — MyCard 竹马平台查询插件

功能：
  - 历史战绩查询：通过》X月历史「触发，支持@某人/自己/指定用户名
  - 帐号绑定、订阅/退订对戰通知
  - 首胜查询、绑定查询、月度胜率统计图表
  - 用户标签管理
"""

import html
import re
from datetime import datetime

import pytz

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg, EventMessage

from hikari_bot.core.commands import on_cmd
from hikari_bot.services import mycard as mycard_service




# ── 历史战绩查询 ───────────────────────────────────────────────────────────────────
# 用法：直接发送》X月历史「或》X月历史 用户名「

# 触发正则：X月历史 / XX年X月历史 / X月历史 用户名
mycard_regex = r"^(?:(\d{2,4})年)?(?:(1[0-2]|[1-9])月)?历史(?:\s+(.+))?$"
mycard_query = on_regex(r".*历史.*", priority=5)

@mycard_query.handle()
async def _(bot: Bot, event: MessageEvent, message: Message = EventMessage()):
    plain_text = event.get_plaintext().strip()
    res = re.match(mycard_regex, plain_text)

    if not res:
        return

    year = int(res.group(1)) if res.group(1) else None
    month = int(res.group(2)) if res.group(2) else None
    user_id = res.group(3)

    if user_id:
        user_id = html.unescape(user_id)
    else:
        at_targets = [
            seg.data.get("qq")
            for seg in message
            if seg.type == "at"
            and seg.data.get("qq") not in ("all", str(bot.self_id))
        ]
        at_qq = at_targets[0] if at_targets else None

        if at_qq:
            user_id = mycard_service.get_mycard_user().get(str(at_qq))
            if not user_id:
                await mycard_query.finish("对方未绑定 MyCard 用户名！")
        else:
            user_id = mycard_service.get_mycard_user().get(str(event.user_id))
            if not user_id:
                await mycard_query.finish("请先绑定或提供 MyCard 用户名！")


    now_shanghai = datetime.now(pytz.timezone("Asia/Shanghai"))
    current_month = now_shanghai.month
    current_year = now_shanghai.year

    if not month:
        year = current_year
        month = current_month
    elif not year:
        if month <= current_month:
            year = current_year
        else:
            year = current_year - 1
    elif year < 2000:
        year = year + 2000

    if year > current_year or (year == current_year and month > current_month):
        return

    from hikari_bot.services.mycard_reports import history_report
    result = await history_report(user_id, month, year)
    message = Message(MessageSegment.text(result.text))
    for image in result.images:
        message += MessageSegment.image(image)
    await mycard_query.finish(message)


# ── 帐号绑定与订阅管理 ────────────────────────────────────────────────────────────
# 绑定 用户名 | 订阅 用户名 | 退订 用户名

mycard_bind = on_cmd("绑定", priority=5)

@mycard_bind.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    qq = str(event.user_id)
    id = html.unescape(args.extract_plain_text().strip())
    if not id:
        await mycard_bind.finish("请提供要绑定的用户名！")
    mycard_service.add_mycard_user(qq, id)
    await mycard_bind.finish("绑定成功！")


mycard_subscribe = on_cmd("订阅", priority=5)

@mycard_subscribe.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    id = html.unescape(args.extract_plain_text().strip())
    if not id:
        await mycard_subscribe.finish("请提供要订阅的用户名！")
    record = await mycard_service.fetch_player_history(id, 1)
    if not record or record == []:
        await mycard_subscribe.finish("用户不存在！")
    if isinstance(event, GroupMessageEvent):
        usertype = "group"
        qq = str(event.group_id)
    else:
        usertype = "private"
        qq = str(event.user_id)
    mycard_service.subscribe(usertype, qq, id)
    await mycard_subscribe.finish("订阅成功！")


mycard_unsubscribe = on_cmd("退订", priority=5)

@mycard_unsubscribe.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    id = html.unescape(args.extract_plain_text().strip())
    if not id:
        await mycard_unsubscribe.finish("请提供要退订的用户名！")
    if isinstance(event, GroupMessageEvent):
        role = getattr(event.sender, "role", None)
        # if not role in ("owner", "admin"):
        #     await mycard_unsubscribe.finish("只有群主或管理员可以退订！")
        #     return
        usertype = "group"
        qq = str(event.group_id)
    else:
        usertype = "private"
        qq = str(event.user_id)
    mycard_service.unsubscribe(usertype, qq, id)
    await mycard_unsubscribe.finish("退订成功！")


# ── 首胜查询 ────────────────────────────────────────────────────────────────────────
# 首胜查询 [用户名]

mycard_firstwin = on_cmd("首胜查询", aliases={"首赢查询"}, priority=5)

@mycard_firstwin.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        user_id = html.unescape(input)
    else:
        user_id = mycard_service.get_mycard_user()[str(event.user_id)]
    if not user_id:
        await mycard_query.finish("请先绑定或提供用户名！")

    firstwin = await mycard_service.is_first_win(user_id)
    if firstwin:
        await mycard_firstwin.finish(f"{user_id}已完成今日首赢！")
    else:
        await mycard_firstwin.finish(f"{user_id}还未完成今日首赢！")


# ── 绑定查询 ────────────────────────────────────────────────────────────────────────
# 查询绑定 [@某人] / 查询绑定 [用户名]

mycard_whois = on_cmd("查询绑定", aliases={"绑定查询"}, priority=5)

@mycard_whois.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg(), message: Message = EventMessage()):
    # 检查是否有@某人
    at_targets = [
        seg.data.get("qq")
        for seg in message
        if seg.type == "at"
        and seg.data.get("qq") not in ("all", str(bot.self_id))
    ]

    if at_targets:
        # 如果有@某人，查询被@的人的绑定信息
        qq = at_targets[0]
        user_id = mycard_service.get_mycard_user().get(str(qq))
        if user_id:
            await mycard_whois.finish(f"该用户绑定的 MyCard 用户名为：{user_id}")
        else:
            await mycard_whois.finish(f"该用户还未绑定 MyCard 用户名！")
    elif input_text := args.extract_plain_text().strip():
        # 如果有输入参数，作为MyCard用户名反向查找QQ号
        mycard_username = html.unescape(input_text)
        user_list = mycard_service.get_mycard_user()
        found_qq_list = []
        for qq, username in user_list.items():
            if username == mycard_username:
                found_qq_list.append(qq)

        if found_qq_list:
            qq_list_text = "、".join(found_qq_list)
            await mycard_whois.finish(f"以下qq绑定了 Mycard 用户名 {mycard_username}：{qq_list_text}")
        else:
            await mycard_whois.finish(f"暂无用户绑定 Mycard 用户名 {mycard_username}！")
    else:
        # 如果没有@也没有参数，查询自己的绑定信息
        qq = str(event.user_id)
        user_id = mycard_service.get_mycard_user().get(str(qq))
        if user_id:
            await mycard_whois.finish(f"你绑定的 MyCard 用户名为：{user_id}")
        else:
            await mycard_whois.finish(f"你还未绑定 MyCard 用户名！")


# ── 胜率统计 ────────────────────────────────────────────────────────────────────────
# 胜率查询 [用户名]

mycard_winrate = on_cmd("胜率查询", aliases={"胜率统计"}, priority=5)

@mycard_winrate.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        user_id = html.unescape(input)
    else:
        user_id = mycard_service.get_mycard_user().get(str(event.user_id))
    if not user_id:
        await mycard_winrate.finish("请先绑定或提供用户名！")

    from hikari_bot.services.mycard_reports import winrate_report
    result = await winrate_report(user_id)
    message = Message(MessageSegment.text(result.text))
    for image in result.images:
        message += MessageSegment.image(image)
    await mycard_winrate.finish(message)


# ── 标签管理（待实现） ───────────────────────────────────────────────────────────────
# 添加标签 标签名 | 删除标签 标签名 | 查看标签

mycard_addtag = on_cmd("添加标签", priority=5)
mycard_deltag = on_cmd("删除标签", priority=5)
mycard_taglist = on_cmd("查看标签", priority=5)
