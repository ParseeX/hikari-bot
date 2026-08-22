"""Client for the OpenAI-compatible chat endpoint exposed by LM Studio."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from hikari_bot.core.config import settings


DEFAULT_IMAGE_PROMPT = "请描述这张图片的内容。"
_SUPPORTED_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_IMAGE_MEDIA_TYPE_ALIASES = {"image/jpg": "image/jpeg"}


class ImageInputError(ValueError):
    """Raised when a QQ image cannot safely be sent to the vision model."""


async def get_local_llm_reply(
    prompt: str,
    *,
    image_urls: Sequence[str] = (),
    client: httpx.AsyncClient | None = None,
) -> str:
    """Send a text-and-image prompt to the configured LM Studio model."""
    if client is None:
        async with httpx.AsyncClient(timeout=settings.local_llm_timeout) as new_client:
            return await _get_local_llm_reply(prompt, image_urls, new_client)
    return await _get_local_llm_reply(prompt, image_urls, client)


async def _get_local_llm_reply(
    prompt: str,
    image_urls: Sequence[str],
    client: httpx.AsyncClient,
) -> str:
    if len(image_urls) > settings.local_llm_image_max_count:
        raise ImageInputError(
            f"一次最多发送 {settings.local_llm_image_max_count} 张图片。"
        )

    image_data_urls = [
        await _download_image_as_data_url(image_url, client) for image_url in image_urls
    ]
    if not prompt and not image_data_urls:
        raise ValueError("A text prompt or image is required")

    messages: list[dict[str, Any]] = []
    if settings.local_llm_system_prompt:
        messages.append({"role": "system", "content": settings.local_llm_system_prompt})
    if image_data_urls:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt or DEFAULT_IMAGE_PROMPT}
        ]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_data_url}}
            for image_data_url in image_data_urls
        )
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    headers = (
        {"Authorization": f"Bearer {settings.local_llm_api_key}"}
        if settings.local_llm_api_key
        else None
    )
    response = await client.post(
        f"{settings.local_llm_api_base_url}/chat/completions",
        json={
            "model": settings.local_llm_model,
            "messages": messages,
            "stream": False,
        },
        headers=headers,
    )
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise ValueError("LM Studio returned a non-object response")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LM Studio response does not contain choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("LM Studio response contains an invalid choice")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("LM Studio response does not contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not (content := content.strip()):
        raise ValueError("LM Studio response does not contain text content")
    return content


async def _download_image_as_data_url(image_url: str, client: httpx.AsyncClient) -> str:
    """Download one supported image into memory and encode it as a data URL."""
    try:
        async with client.stream("GET", image_url, follow_redirects=True) as response:
            response.raise_for_status()
            content_type = _normalise_image_media_type(response.headers.get("content-type"))
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > settings.local_llm_image_max_bytes:
                raise ImageInputError("图片超过大小限制。")

            image_data = bytearray()
            async for chunk in response.aiter_bytes():
                image_data.extend(chunk)
                if len(image_data) > settings.local_llm_image_max_bytes:
                    raise ImageInputError("图片超过大小限制。")
    except ImageInputError:
        raise
    except (httpx.HTTPError, ValueError) as error:
        raise ImageInputError("无法下载图片。") from error

    encoded_image = base64.b64encode(image_data).decode("ascii")
    return f"data:{content_type};base64,{encoded_image}"


def _normalise_image_media_type(content_type: str | None) -> str:
    media_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    media_type = _IMAGE_MEDIA_TYPE_ALIASES.get(media_type, media_type)
    if media_type not in _SUPPORTED_IMAGE_MEDIA_TYPES:
        raise ImageInputError("仅支持 JPEG、PNG 或 WebP 图片。")
    return media_type
