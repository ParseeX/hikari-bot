"""Client for the OpenAI-compatible chat endpoint exposed by LM Studio."""

from __future__ import annotations

from typing import Any

import httpx

from hikari_bot.core.config import settings


async def get_local_llm_reply(
    prompt: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Send one prompt to the configured LM Studio model and return its text reply."""
    if client is None:
        async with httpx.AsyncClient(timeout=settings.local_llm_timeout) as new_client:
            return await _get_local_llm_reply(prompt, new_client)
    return await _get_local_llm_reply(prompt, client)


async def _get_local_llm_reply(prompt: str, client: httpx.AsyncClient) -> str:
    messages: list[dict[str, str]] = []
    if settings.local_llm_system_prompt:
        messages.append({"role": "system", "content": settings.local_llm_system_prompt})
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
