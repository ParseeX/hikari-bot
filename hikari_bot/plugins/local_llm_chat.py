"""Restricted group-chat access to the LM Studio model running on the LAN."""

from __future__ import annotations

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import EventMessage
from nonebot.rule import Rule

from hikari_bot.core.constants import ADMIN
from hikari_bot.core.logger import log_message
from hikari_bot.services.local_llm import get_local_llm_reply


def _mentions_bot(message: Message, bot_id: str) -> bool:
    """Return whether a OneBot message explicitly @-mentions this bot."""
    return any(
        segment.type == "at" and str(segment.data.get("qq")) == bot_id
        for segment in message
    )


async def _is_authorized_group_prompt(bot: Bot, event: MessageEvent) -> bool:
    """Accept only non-empty group prompts from an administrator who @-mentioned us."""
    return (
        isinstance(event, GroupMessageEvent)
        and str(event.user_id) in ADMIN
        and _mentions_bot(event.get_message(), str(bot.self_id))
        and bool(event.get_plaintext().strip())
    )


local_llm_chat = on_message(
    rule=Rule(_is_authorized_group_prompt),
    priority=100,
    block=True,
)


@local_llm_chat.handle()
async def _(event: GroupMessageEvent, message: Message = EventMessage()):
    prompt = message.extract_plain_text().strip()
    try:
        reply = await get_local_llm_reply(prompt)
    except httpx.HTTPStatusError as error:
        await log_message(
            f"[local_llm_chat] LM Studio returned HTTP {error.response.status_code}"
        )
        await local_llm_chat.finish("本地模型暂时无法响应，请稍后再试。")
    except httpx.HTTPError as error:
        await log_message(f"[local_llm_chat] LM Studio request failed: {error}")
        await local_llm_chat.finish("无法连接本地模型，请稍后再试。")
    except ValueError as error:
        await log_message(f"[local_llm_chat] Invalid LM Studio response: {error}")
        await local_llm_chat.finish("本地模型未返回可用内容，请稍后再试。")

    await local_llm_chat.finish(reply)
