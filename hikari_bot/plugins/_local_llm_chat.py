"""Restricted group-chat access to the LM Studio model running on the LAN."""

from __future__ import annotations

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import EventMessage
from nonebot.rule import Rule

from hikari_bot.core.constants import ADMIN
from hikari_bot.core.feature_flags import get_local_llm_public_enabled
from hikari_bot.services.local_llm import ImageInputError, get_local_llm_reply


def _mentions_bot(message: Message, bot_id: str) -> bool:
    """Return whether a OneBot message explicitly @-mentions this bot."""
    return any(
        segment.type == "at" and str(segment.data.get("qq")) == bot_id
        for segment in message
    )


def _contains_image(message: Message) -> bool:
    return any(segment.type == "image" for segment in message)


def _image_urls(message: Message) -> list[str]:
    return [
        str(segment.data.get("url", "")).strip()
        for segment in message
        if segment.type == "image" and str(segment.data.get("url", "")).strip()
    ]


async def _is_authorized_group_prompt(bot: Bot, event: MessageEvent) -> bool:
    """Accept an administrator's @-mention with text, an image, or both."""
    return (
        isinstance(event, GroupMessageEvent)
        and (str(event.user_id) in ADMIN or await get_local_llm_public_enabled())
        and _mentions_bot(event.original_message, str(bot.self_id))
        and (bool(event.get_plaintext().strip()) or _contains_image(event.get_message()))
    )


local_llm_chat = on_message(
    rule=Rule(_is_authorized_group_prompt),
    # Commands use priority 5; ygomatch_query's global collector at 10 would
    # otherwise stop every event before this restricted group-chat handler.
    priority=6,
    block=True,
)


@local_llm_chat.handle()
async def _(event: GroupMessageEvent, message: Message = EventMessage()):
    prompt = message.extract_plain_text().strip()
    image_urls = _image_urls(message)
    if _contains_image(message) and not image_urls:
        await local_llm_chat.finish("未取得图片下载地址，请重新发送原图。")

    try:
        reply = await get_local_llm_reply(prompt, image_urls=image_urls)
    except ImageInputError as error:
        await local_llm_chat.finish(str(error))
    except httpx.HTTPStatusError:
        await local_llm_chat.finish("本地模型暂时无法响应，请稍后再试。")
    except httpx.HTTPError:
        await local_llm_chat.finish("无法连接本地模型，请稍后再试。")
    except ValueError:
        await local_llm_chat.finish("本地模型未返回可用内容，请稍后再试。")

    await local_llm_chat.finish(reply)
