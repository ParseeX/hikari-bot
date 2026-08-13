"""Client for the external OCG Ruling Assistant answer API."""

from __future__ import annotations

from typing import Any

import httpx

from hikari_bot.core.config import settings


async def get_ruling_conclusion(
    question: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return only the upstream answer's short conclusion.

    The caller owns a supplied client.  This keeps the service easy to test and
    ensures production callers do not need to know the upstream response shape.
    """
    if client is None:
        async with httpx.AsyncClient(timeout=settings.ruling_assistant_timeout) as new_client:
            return await _get_ruling_conclusion(question, new_client)
    return await _get_ruling_conclusion(question, client)


async def _get_ruling_conclusion(question: str, client: httpx.AsyncClient) -> str:
    response = await client.post(
        settings.ruling_assistant_api_url,
        json={"question": question},
    )
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Ruling Assistant returned a non-object response")

    conclusion = payload.get("shortAnswer")
    if not isinstance(conclusion, str) or not (conclusion := conclusion.strip()):
        raise ValueError("Ruling Assistant response does not contain shortAnswer")
    return conclusion
