import asyncio
import os
import shutil

from jmcomic import create_option_by_file, download_album

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from hikari_bot.core.constants import DATA_DIR, RESOURCES_DIR

JM_DIR = os.path.join(DATA_DIR, "jm")

jmcomic_download = on_command('jm', priority=5, permission=SUPERUSER)

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
        await log_message(f"[jm] Exception occurred in jm_download: {e}")
        await bot.send(event=event, message=f"下载失败，请重试。\n{type(e).__name__}: {e}")


@jmcomic_download.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    comic_id = args.extract_plain_text().strip()
    if not comic_id.isdigit():
        return
    comic_id = int(comic_id)
    await jmcomic_download.send(f"开始下载jm{comic_id}")
    asyncio.create_task(_jm_download(bot, event, comic_id))
