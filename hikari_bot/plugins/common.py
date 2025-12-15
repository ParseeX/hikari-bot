from nonebot import on_command, on_message, on_request, on_notice, get_driver
from nonebot.adapters.onebot.v11 import FriendRequestEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from hikari_bot.utils.whitelist import *
from hikari_bot.utils.constants import *
from nonebot.matcher import Matcher
import base64
import re
import asyncio
import shutil
from jmcomic import download_album, create_option_by_file
import os

async def message_superusers(bot: Bot, message: str):
    for uid in get_driver().config.superusers:
        await bot.send_private_msg(user_id=int(uid), message=message)

driver = get_driver()
@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    await message_superusers(bot, "早上好！")


help_pic = os.path.join(RESOURCES_DIR, 'help.png')
help = on_command("帮助", priority=5)
@help.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    with open(help_pic, "rb") as f:
        image_data = f.read()
    image_base64 = base64.b64encode(image_data).decode("utf-8")
    await help.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


reload = on_command("重载插件", permission=SUPERUSER)
@reload.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        # 强制丢弃本地所有修改并拉取远程最新
        cmds = [
            ["git", "reset", "--hard"],
            ["git", "clean", "-fd"],
            ["git", "pull"]
        ]
        git_output = ""
        for cmd in cmds:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            git_output += stdout.decode().strip() + "\n" + stderr.decode().strip() + "\n"
            if proc.returncode != 0:
                await reload.finish(f"更新失败：\n{git_output}")
                return
        await reload.send("更新完成，正在重启...")
        os._exit(0)
    except Exception as e:
        await reload.finish(f"重载插件失败：{e}")

whitelist = on_command("添加至白名单", aliases={"白名单"}, permission=SUPERUSER)

@whitelist.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if args:
        group_id = int(args.extract_plain_text())
    elif isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    else:
        await whitelist.finish("请提供需要添加到白名单的群号！")
    
    if add_group_to_whitelist(group_id):
        await whitelist.finish(f"已添加群{group_id}至白名单。")
    else:
        await whitelist.finish(f"群{group_id}已经在白名单中。")


whitelist_check = on_message(priority=1, block=False)

@whitelist_check.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher):
    if isinstance(event, GroupMessageEvent) and not is_allowed_group(event.group_id):
        matcher.stop_propagation()


broadcast = on_command('广播', permission=SUPERUSER)

@broadcast.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    message = args.extract_plain_text()
    for group in get_whitelist()["groups"]:
        try:
            await bot.send_group_msg(group_id=group, message=message)
        except Exception as e:
            print(f"向群{group}发送消息失败: {e}")


spy_check = on_command('卧底检测', permission=SUPERUSER)

@spy_check.handle()
async def _(bot: Bot, event: MessageEvent):
    members_srds = await bot.get_group_member_list(group_id=457767939)
    members_st = await bot.get_group_member_list(group_id=876573031)
    
    print(len(members_srds))
    print(len(members_st))

    bot_id = bot.self_id
    member_ids_srds = {
        member["user_id"]: member["nickname"]
        for member in members_srds if member["user_id"] != bot_id
    }
    member_ids_st = {
        member["user_id"]: member["nickname"]
        for member in members_st if member["user_id"] != bot_id
    }

    common_ids = set(member_ids_srds.keys()) & set(member_ids_st.keys())
    if not common_ids:
        await spy_check.finish(f"查询完成，没有卧底。")

    result = "以下成员可能是卧底："
    kill = []
    for user_id in common_ids:
        info_srds = await bot.get_group_member_info(group_id=457767939, user_id=user_id)
        info_st = await bot.get_group_member_info(group_id=876573031, user_id=user_id)
        if info_srds and info_st:
            l1 = int(info_srds["level"])
            l2 = int(info_st["level"])
            name = info_srds["nickname"]
            if l1 < l2:
                kill.append({"user_id":user_id, "name":name, "l1":l1, "l2":l2})
                result += f"\n{name},{l1},{l2}"
    
    await spy_check.finish(result)


request_handler = on_request(priority=1)

@request_handler.handle()
async def _(bot: Bot, event: FriendRequestEvent):
    try:
        # 自动通过好友请求
        await bot.call_api("set_friend_add_request", flag=event.flag, approve=True)
        print(f"已自动通过好友申请，来自用户：{event.user_id}")
    except Exception as e:
        print(f"处理好友申请失败：{e}")

@request_handler.handle()
async def request_handler(bot: Bot, event: GroupRequestEvent):
    try:
        if event.sub_type == "invite":
            await bot.call_api(
                "set_group_add_request",
                flag=event.flag,
                sub_type=event.sub_type,
                approve=True
            )
            await message_superusers(bot, f"已自动通过群邀请，来自用户：{event.user_id}，群号：{event.group_id}")
    except Exception as e:
        print(f"处理群邀请失败：{e}")



# def _invited(bot: Bot, event: Event, state: dict)->bool:
#     return event.notice_type=='group_increase' and event.sub_type=='invite'


# invited = on_notice(_invited)

# @invited.handle()
# async def _(bot: Bot, event: Event):
#     if str(event.user_id) == str(bot.self_id):
#         await message_superusers(bot, f"已被用户 {event.operator_id} 拉入群：{event.group_id}")


srdslist = on_command('队员列表', permission=SUPERUSER)

@srdslist.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    member_list = await bot.get_group_member_list(group_id=group_id)

    result = []
    for member in member_list:
        card = member.get("card", "") or member.get("nickname", "")
        if card.startswith("SRDS"):
            new_card = re.sub(r"^SRDS\s*", "", card)
            result.append(f"{new_card} {member['user_id']}")
            print(result)

    MAX_LINES = 100
    output = "\n".join(result[:MAX_LINES])
    await srdslist.finish(output)


async def _jm_download(bot: Bot, event: MessageEvent, comic_id: int):
    if isinstance(event, PrivateMessageEvent):
        friend_list = await bot.call_api("get_friend_list")
        if not any(str(friend["user_id"]) == str(event.user_id) for friend in friend_list):
            await bot.send(event=event, message="未添加好友无法发送文件，请先添加好友！")

    loop = asyncio.get_running_loop()
    option = create_option_by_file(os.path.join(RESOURCES_DIR, "option.yml"))
    try:
        await loop.run_in_executor(None, download_album, comic_id, option)
        # 删除 JM_DIR/tmp/comic_id 目录
        tmp_dir = os.path.join(JM_DIR, "tmp", str(comic_id))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # 发送pdf文件
        pdf_path = os.path.join(JM_DIR, f"{comic_id}.pdf")
        if isinstance(event, PrivateMessageEvent):
            await bot.upload_private_file(user_id=event.user_id, file=pdf_path, name=f"{comic_id}.pdf")
        else:
            await bot.upload_group_file(group_id=event.group_id, file=pdf_path, name=f"{comic_id}.pdf")

    except Exception as e:
        await bot.send(event=event, message=f"下载失败，请重试。\n{type(e).__name__}: {e}")


jmcomic_download = on_command('jm', priority=5, permission=SUPERUSER)

@jmcomic_download.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    comic_id = args.extract_plain_text().strip()
    if not comic_id.isdigit():
        return
    comic_id = int(comic_id)
    await jmcomic_download.send(f"开始下载jm{comic_id}")
    asyncio.create_task(_jm_download(bot, event, comic_id))
