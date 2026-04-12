from nonebot import on_command, on_message, on_request, on_notice, get_driver
from nonebot.adapters.onebot.v11 import Event, FriendRequestEvent, GroupRequestEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException
from hikari_bot.utils.whitelist import *
from hikari_bot.utils.constants import *
from hikari_bot.utils.logger import *
from hikari_bot.plugins.mycard_subscriber import ws_status_check
from hikari_bot.plugins.cardrush_helper import cr_status_check
from nonebot.matcher import Matcher
import base64
import re
import asyncio
import os

driver = get_driver()
log_file = driver.state.log_file

@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    await log_message("Bot connected.")
    await message_superusers("早上好！")

read_log = on_command("读取日志", permission=SUPERUSER)
@read_log.handle()
async def _(bot: Bot, event: MessageEvent):
    log_content = await log_read()    
    # 发送日志内容，分段发送以避免消息过长
    MAX_MESSAGE_LENGTH = 2000
    for i in range(0, len(log_content), MAX_MESSAGE_LENGTH):
        await read_log.send(log_content[i:i+MAX_MESSAGE_LENGTH])


status = on_command("状态查询", permission=SUPERUSER)
@status.handle()
async def _(bot: Bot, event: MessageEvent):
    ws_status = ws_status_check()
    cr_status = cr_status_check()
    startup_time, uptime = get_bot_startup_info()
    
    status_message = f"""服务器状态：
- 启动时间：{startup_time}
- 运行时长：{uptime}
- MyCard监控：{'在线' if ws_status else '离线'}
- CardRush监控：{'在线' if cr_status else '离线'}"""
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
            except Exception:
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
                await reload.finish(f"更新失败：\n{git_output}")
                return
        await reload.send("更新完成，正在重启...")
        os._exit(0)
    except Exception as e:
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
    
    if add_group_to_whitelist(group_id):
        await whitelist.finish(f"已添加群{group_id}至白名单。")
    else:
        await whitelist.finish(f"群{group_id}已经在白名单中。")

kill_all_whitelist = on_command("清空白名单", permission=SUPERUSER)

@kill_all_whitelist.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        save_whitelist({"groups": [], "users": []})
        await kill_all_whitelist.finish("已清空白名单。")
    except Exception as e:
        await kill_all_whitelist.finish(f"清空白名单失败：{e}")

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
            await message_superusers(f"已自动通过群邀请，来自用户：{event.user_id}，群号：{event.group_id}")
    except Exception as e:
        print(f"处理群邀请失败：{e}")


def _invited(bot: Bot, event: Event)->bool:
    return event.notice_type=='group_increase' and event.sub_type=='invite'


invited = on_notice(_invited)

@invited.handle()
async def _(bot: Bot, event: Event):
    if str(event.user_id) == str(bot.self_id):
        await message_superusers(f"已被用户 {event.operator_id} 拉入群：{event.group_id}")


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
