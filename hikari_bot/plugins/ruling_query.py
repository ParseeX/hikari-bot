"""OCG ruling query command backed by the external ruling assistant API."""

from __future__ import annotations

import httpx
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.logger import log_message
from hikari_bot.services.ruling_assistant import get_ruling_conclusion


DISCLAIMER = "[非官方裁定，仅供参考]"

ruling_query = on_cmd("裁定查询", priority=5, block=True)


@ruling_query.handle()
async def _(args: Message = CommandArg()):
    question = args.extract_plain_text().strip()
    if not question:
        await ruling_query.finish("请在“裁定查询”后输入需要裁定的内容。")

    try:
        conclusion = await get_ruling_conclusion(question)
    except httpx.HTTPStatusError as error:
        await log_message(
            f"[ruling_query] Upstream returned HTTP {error.response.status_code}"
        )
        await ruling_query.finish("裁定服务暂时无法响应，请稍后再试。")
    except httpx.HTTPError as error:
        await log_message(f"[ruling_query] API request failed: {error}")
        await ruling_query.finish("无法连接裁定服务，请稍后再试。")
    except ValueError as error:
        await log_message(f"[ruling_query] Invalid API response: {error}")
        await ruling_query.finish("裁定服务未返回可用结论，请稍后再试。")

    await ruling_query.finish(f"{conclusion}\n{DISCLAIMER}")
