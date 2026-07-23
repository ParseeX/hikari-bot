"""
jm.py — JMComic 漫画下载插件

功能：
  - 通过禁漫本子 ID 下载并转换为 PDF，发送给用户
  - 支持私聊（上传好友文件）与群聊（上传群文件）
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from jmcomic import create_option_by_file, download_album

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.config import settings
from hikari_bot.core.constants import RESOURCES_DIR
from hikari_bot.core.logger import log_message


JM_DIR = str(settings.jm_data_dir)


def _create_jm_option():
    """Render deployment paths and the PDF password into a temporary option file."""
    template_path = Path(RESOURCES_DIR) / "option.yml"
    template = template_path.read_text(encoding="utf-8")
    password = settings.require("JM_PDF_PASSWORD", settings.jm_pdf_password)
    rendered = template.replace(
        "${JM_TMP_DIR}", json.dumps(str(settings.jm_data_dir / "tmp"), ensure_ascii=False)
    ).replace(
        "${JM_PDF_DIR}", json.dumps(str(settings.jm_data_dir), ensure_ascii=False)
    ).replace(
        "${JM_PDF_PASSWORD}", json.dumps(password, ensure_ascii=False)
    )

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", encoding="utf-8", delete=False
        ) as temp_file:
            temp_file.write(rendered)
            temp_path = temp_file.name
        return create_option_by_file(temp_path)
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


# ── 下载工具 ──────────────────────────────────────────────────────────────────────────
# 用法：jm <ID>

jmcomic_download = on_cmd('jm', priority=5, permission=SUPERUSER)

async def _jm_download(bot: Bot, event: MessageEvent, comic_id: int):
    if isinstance(event, PrivateMessageEvent):
        friend_list = await bot.call_api("get_friend_list")
        if not any(str(friend["user_id"]) == str(event.user_id) for friend in friend_list):
            await bot.send(event=event, message="未添加好友无法发送文件，请先添加好友！")

    loop = asyncio.get_running_loop()
    try:
        option = _create_jm_option()
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
