import asyncio
import base64
import os
import re

from nonebot import get_driver, on_command, on_message, on_notice, on_request
from nonebot.adapters.onebot.v11 import Bot, Event, FriendRequestEvent, GroupMessageEvent, GroupRequestEvent, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from hikari_bot.core.constants import RESOURCES_DIR
from hikari_bot.core.logger import get_bot_startup_info, log_message, log_read
from hikari_bot.core.whitelist import *
from hikari_bot.plugins.monitors.mycard import ws_status_check

driver = get_driver()

@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    await log_message("QQ connected.")
    await message_superusers("早上好！")

read_log = on_command("读取日志", permission=SUPERUSER)
@read_log.handle()
async def _(bot: Bot, event: MessageEvent):
    log_content = await log_read()    
    # 发送日志内容，分段发送以避免消息过长
    MAX_LINE = 50
    for i in range(0, len(log_content), MAX_LINE):
        await read_log.send("".join(log_content[i:i+MAX_LINE]))


status = on_command("状态查询", permission=SUPERUSER)
@status.handle()
async def _(bot: Bot, event: MessageEvent):
    ws_status = ws_status_check()
    startup_time, uptime = get_bot_startup_info()
    
    status_message = f"""服务器状态：
- 启动时间：{startup_time}
- 运行时长：{uptime}
- MyCard监控：{'在线' if ws_status else '离线'}"""
    await status.finish(status_message)


help_pic = os.path.join(RESOURCES_DIR, 'help.png')
help = on_command("帮助", priority=5)
@help.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    with open(help_pic, "rb") as f:
        image_data = f.read()
    image_base64 = base64.b64encode(image_data).decode("utf-8")
    await help.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))


version = on_command("版本查询", permission=SUPERUSER)
@version.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        # 获取git信息的命令
        git_commands = {
            "commit_message": ["git", "log", "-1", "--pretty=format:%s"],
            "commit_date": ["git", "log", "-1", "--pretty=format:%ad", "--date=format:%Y-%m-%d %H:%M:%S"]
        }
        
        git_info = {}
        for key, cmd in git_commands.items():
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    git_info[key] = stdout.decode().strip()
                else:
                    git_info[key] = "获取失败"
            except Exception as e:
                await log_message(f"[version] Exception occurred while getting git info: {e}")
                git_info[key] = "获取失败"
        
        # 格式化版本信息
        version_info = f"""提交信息: {git_info.get('commit_message', '无')}
提交时间: {git_info.get('commit_date', '未知')}"""
        
        await version.finish(version_info)
        
    except Exception as e:
        if not isinstance(e, FinishedException):
            await version.finish(f"版本信息查询失败：{e}")

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
                await log_message(f"[reload] Git command failed: {git_output}")
                await reload.finish(f"更新失败：\n{git_output}")
                return
        await reload.send("更新完成，正在重启...")
        os._exit(0)
    except Exception as e:
        await log_message(f"[reload] Exception occurred while reloading plugins: {e}")
        await reload.finish(f"重载插件失败：{e}")

reboot = on_command("重启服务器", permission=SUPERUSER)
@reboot.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        await reboot.send("正在重启服务器...")
        proc = await asyncio.create_subprocess_exec(
            "sudo", "reboot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            error_message = stderr.decode().strip() if stderr else "未知错误"
            await reboot.finish(f"重启失败：{error_message}")
        else:
            await reboot.finish("重启命令已执行")
            
    except Exception as e:
        await log_message(f"[reboot] Exception occurred while rebooting server: {e}")
        await reboot.finish(f"重启服务器失败：{e}")

whitelist = on_command("添加至白名单", aliases={"白名单"}, permission=SUPERUSER)
@whitelist.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if args:
        group_id = int(args.extract_plain_text())
    elif isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    else:
        await whitelist.finish("请提供需要添加到白名单的群号！")
    
    if await add_group_to_whitelist(group_id):
        await whitelist.finish(f"已添加群{group_id}至白名单。")
    else:
        await whitelist.finish(f"群{group_id}已经在白名单中。")

kill_all_whitelist = on_command("清空白名单", permission=SUPERUSER)

@kill_all_whitelist.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        await save_whitelist({"groups": [], "users": []})
        await kill_all_whitelist.finish("已清空白名单。")
    except Exception as e:
        await log_message(f"[kill_all_whitelist] Exception occurred while clearing whitelist: {e}")
        await kill_all_whitelist.finish(f"清空白名单失败：{e}")

whitelist_check = on_message(priority=1, block=False)

@whitelist_check.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher):
    if isinstance(event, GroupMessageEvent) and not await is_allowed_group(event.group_id):
        matcher.stop_propagation()


broadcast = on_command('广播', permission=SUPERUSER)

@broadcast.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    message = args.extract_plain_text()
    whitelist = await get_whitelist()
    for group in whitelist["groups"]:
        try:
            await bot.send_group_msg(group_id=group, message=message)
        except Exception as e:
            await log_message(f"[broadcast] Failed to send message to group {group}: {e}")


request_handler = on_request(priority=1)

@request_handler.handle()
async def _(bot: Bot, event: FriendRequestEvent):
    try:
        # 自动通过好友请求
        await bot.call_api("set_friend_add_request", flag=event.flag, approve=True)
        await log_message(f"[friend_request] Automatically approved friend request from user: {event.user_id}")
    except Exception as e:
        await log_message(f"[friend_request] Failed to process friend request from user {event.user_id}: {e}")

@request_handler.handle()
async def _(bot: Bot, event: GroupRequestEvent):
    try:
        if event.sub_type == "invite":
            await bot.call_api(
                "set_group_add_request",
                flag=event.flag,
                sub_type=event.sub_type,
                approve=True
            )
            await log_message(f"[group_request] Automatically approved group invite from user {event.user_id} for group {event.group_id}")
            await message_superusers(f"已自动通过群邀请，来自用户：{event.user_id}，群号：{event.group_id}")
    except Exception as e:
        await log_message(f"[group_request] Failed to process group invite from user {event.user_id} in group {event.group_id}: {e}")


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

    MAX_LINES = 100
    output = "\n".join(result[:MAX_LINES])
    await srdslist.finish(output)
