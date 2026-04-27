"""
mycard_query.py — MyCard 竹马平台查询插件

功能：
  - 历史战绩查询：通过》X月历史「触发，支持@某人/自己/指定用户名
  - 帐号绑定、订阅/退订对戰通知
  - 首胜查询、绑定查询、月度胜率统计图表
  - 用户标签管理
"""

import base64
import html
import re
from datetime import datetime
from io import BytesIO

import matplotlib.pyplot as plt
import pytz
from matplotlib.ticker import MaxNLocator

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg, EventMessage

from hikari_bot.core.commands import on_cmd
from hikari_bot.services.mycard import *


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
            user_id = get_mycard_user().get(str(at_qq))
            if not user_id:
                await mycard_query.finish("对方未绑定 MyCard 用户名！")
        else:
            user_id = get_mycard_user().get(str(event.user_id))
            if not user_id:
                await mycard_query.finish("请先绑定或提供 MyCard 用户名！")
    
    
    current_month = datetime.now().month
    current_year = datetime.now().year

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

    records = await mycard_get_records(user_id, month, year)
    if records == None:
        await mycard_query.finish(f"查询失败，请稍后重试")

    wins = [record for record in records if record['winner'] == user_id]

    result_message = f"""玩家：{user_id}
{str(year-2000)+'年' if year!=current_year else ''}{month}月场次：{len(records)}
{str(year-2000)+'年' if year!=current_year else ''}{month}月胜率：{(0 if len(records)==0 else len(wins)*100.0/len(records)):.2f}%"""

    if len(records) > 0:
        pt_ex = [record['pta_ex'] if record['usernamea'] == user_id else record['ptb_ex'] for record in records]
        pt = [record['pta'] if record['usernamea'] == user_id else record['ptb'] for record in records]

        pt.append(pt_ex[-1])
        pt.reverse()

        result_message = result_message + f"\n{str(year-2000)+'年' if year!=current_year else ''}{month}月最高分：{max(pt):.2f}"

        if month == current_month and year == current_year:
            rank = await mycard_get_player_rank(user_id)
            if rank != None:
                result_message = result_message + f"\n当前排名：{rank}"
        else:
            rank = await fetch_player_history_rank(user_id, year, month)
            if rank != None:
                result_message = result_message + f"\n结算排名：{rank}"

        plt.figure(figsize=(8, 6))
        
        # 绘制折线
        plt.plot(pt, marker='.', linestyle='--', color='b', linewidth=0.5)

        # 不显示标题、标签和图例
        plt.title("")  # 不显示标题
        plt.xlabel("")  # 不显示 x 轴标签
        plt.ylabel("")  # 不显示 y 轴标签
        plt.grid(False)  # 隐藏网格

        # 标记第一个和最后一个数据点的值
        if pt:
            plt.text(0, pt[0], f"{pt[0]:.2f}", ha='center', va='bottom', fontsize=10, color='black')
            plt.text(len(pt)-1, pt[-1], f"{pt[-1]:.2f}", ha='center', va='bottom', fontsize=10, color='black')

        plt.gca().xaxis.set_major_locator(MaxNLocator(integer=True))
        plt.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05)

        # 保存图表到内存中的二进制流
        buf = BytesIO()
        plt.savefig(buf, format="png", bbox_inches='tight')
        buf.seek(0)  # 将文件指针移到开始位置
        plt.close()

        image_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

        await mycard_query.finish(Message([MessageSegment("text", {"text": result_message}), MessageSegment("image", {"file": f"base64://{image_base64}"})]))

    await mycard_query.finish(result_message)


# ── 帐号绑定与订阅管理 ────────────────────────────────────────────────────────────
# 绑定 用户名 | 订阅 用户名 | 退订 用户名

mycard_bind = on_cmd("绑定", priority=5)

@mycard_bind.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    qq = str(event.user_id)
    id = html.unescape(args.extract_plain_text().strip())
    if not id:
        await mycard_bind.finish("请提供要绑定的用户名！")
    add_mycard_user(qq, id)
    await mycard_bind.finish("绑定成功！")


mycard_subscribe = on_cmd("订阅", priority=5)

@mycard_subscribe.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    id = html.unescape(args.extract_plain_text().strip())
    if not id:
        await mycard_subscribe.finish("请提供要订阅的用户名！")
    record = await fetch_player_history(id, 1)
    if not record or record == []:
        await mycard_subscribe.finish("用户不存在！")
    if isinstance(event, GroupMessageEvent):
        usertype = "group"
        qq = str(event.group_id)
    else:
        usertype = "private"
        qq = str(event.user_id)
    subscribe(usertype, qq, id)
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
    unsubscribe(usertype, qq, id)
    await mycard_unsubscribe.finish("退订成功！")


# ── 首胜查询 ────────────────────────────────────────────────────────────────────────
# 首胜查询 [用户名]

mycard_firstwin = on_cmd("首胜查询", aliases={"首赢查询"}, priority=5)

@mycard_firstwin.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if input:=args.extract_plain_text().strip():
        user_id = html.unescape(input)
    else:
        user_id = get_mycard_user()[str(event.user_id)]
    if not user_id:
        await mycard_query.finish("请先绑定或提供用户名！")

    firstwin = await is_first_win(user_id)
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
        user_id = get_mycard_user().get(str(qq))
        if user_id:
            await mycard_whois.finish(f"该用户绑定的 MyCard 用户名为：{user_id}")
        else:
            await mycard_whois.finish(f"该用户还未绑定 MyCard 用户名！")
    elif input_text := args.extract_plain_text().strip():
        # 如果有输入参数，作为MyCard用户名反向查找QQ号
        mycard_username = html.unescape(input_text)
        user_list = get_mycard_user()
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
        user_id = get_mycard_user().get(str(qq))
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
        user_id = get_mycard_user().get(str(event.user_id))
    if not user_id:
        await mycard_winrate.finish("请先绑定或提供用户名！")

    records = await fetch_player_history(user_id)
    if records == None:
        await mycard_winrate.finish(f"查询失败，请稍后重试")

    if len(records) == 0:
        await mycard_winrate.finish(f"玩家 {user_id} 暂无对战记录")

    # 按月份分组记录
    monthly_data = {}
    for record in records:
        start_time_utc = datetime.strptime(record["start_time"], "%Y-%m-%dT%H:%M:%S.%fZ")
        utc_zone = pytz.utc
        start_time_utc = utc_zone.localize(start_time_utc)
        start_time_bj = start_time_utc.astimezone(pytz.timezone("Asia/Shanghai"))
        
        month_key = f"{start_time_bj.year}-{start_time_bj.month:02d}"
        if month_key not in monthly_data:
            monthly_data[month_key] = {'total': 0, 'wins': 0}
        
        monthly_data[month_key]['total'] += 1
        if record['winner'] == user_id:
            monthly_data[month_key]['wins'] += 1

    # 排序月份并计算胜率
    sorted_months = sorted(monthly_data.keys())
    win_rates = []
    month_labels = []
    
    for month_key in sorted_months:
        total_games = monthly_data[month_key]['total']
        wins = monthly_data[month_key]['wins']
        win_rate = (wins * 100.0 / total_games) if total_games > 0 else 0.0
        
        win_rates.append(win_rate)
        # 格式化月份标签
        year, month = month_key.split('-')
        month_labels.append(f"{year[-2:]}/{int(month)}")

    total_wins = len([record for record in records if record['winner'] == user_id])
    total_games = len(records)
    overall_rate = (total_wins * 100.0 / total_games) if total_games > 0 else 0.0

    result_message = f"""玩家：{user_id}
总场次：{total_games}
总胜率：{overall_rate:.2f}%
有对局的月份：{len(sorted_months)}个"""

    # 生成胜率曲线图
    if len(win_rates) > 0:
        plt.figure(figsize=(12, 6))
        
        # 绘制胜率曲线
        plt.plot(range(len(win_rates)), win_rates, marker='o', linestyle='-', color='b', linewidth=2, markersize=6)
        
        # 设置图表
        plt.ylim(0, 100)  # 胜率范围0-100%
        #plt.ylabel("胜率 (%)", fontsize=12)
        #plt.xlabel("月份", fontsize=12)
        #plt.title(f"{user_id} 月度胜率统计", fontsize=14, fontweight='bold')
        plt.grid(True, alpha=0.3)
        
        # 设置x轴标签
        plt.xticks(range(len(month_labels)), month_labels, rotation=45, ha='right')
        
        # 在数据点上显示胜率值
        for i, rate in enumerate(win_rates):
            plt.text(i, rate + 2, f"{rate:.1f}%", ha='center', va='bottom', fontsize=9)
        
        # 添加平均线
        avg_rate = sum(win_rates) / len(win_rates)
        plt.axhline(y=avg_rate, color='r', linestyle='--', alpha=0.7, label=f'avg: {avg_rate:.1f}%')
        plt.legend()
        
        plt.tight_layout()

        # 保存图表到内存中的二进制流
        buf = BytesIO()
        plt.savefig(buf, format="png", bbox_inches='tight', dpi=150)
        buf.seek(0)  # 将文件指针移到开始位置
        plt.close()

        image_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

        await mycard_winrate.finish(Message([
            MessageSegment("text", {"text": result_message}), 
            MessageSegment("image", {"file": f"base64://{image_base64}"})
        ]))
    
    await mycard_winrate.finish(result_message)


# ── 标签管理（待实现） ───────────────────────────────────────────────────────────────
# 添加标签 标签名 | 删除标签 标签名 | 查看标签

mycard_addtag = on_cmd("添加标签", priority=5)
mycard_deltag = on_cmd("删除标签", priority=5)
mycard_taglist = on_cmd("查看标签", priority=5)