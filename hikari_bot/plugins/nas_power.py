"""Private QQ commands for LAN-only Windows power control through the NAS."""

from __future__ import annotations

import httpx
from nonebot.adapters.onebot.v11 import MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.config import ConfigurationError, settings
from hikari_bot.core.logger import log_message


def _is_allowed_private_request(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and str(event.user_id) in settings.superusers


async def _request_power_action(action: str) -> None:
    base_url = settings.require("NAS_POWER_URL", settings.nas_power_url).rstrip("/")
    token = settings.require("NAS_POWER_TOKEN", settings.nas_power_token)
    if not settings.superusers:
        raise ConfigurationError("SUPERUSERS is not configured")

    async with httpx.AsyncClient(timeout=settings.nas_power_timeout) as client:
        response = await client.post(
            f"{base_url}/v1/{action}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()

    if payload.get("ok") is not True or payload.get("action") != action:
        raise RuntimeError("NAS power service returned an unexpected response")


async def _handle_power_command(matcher, event: MessageEvent, action: str, args: str) -> None:
    # Never permit group messages, even from an otherwise allowed QQ account.
    if not _is_allowed_private_request(event):
        await log_message(f"[nas_power] Rejected {action} request from {event.user_id}")
        await matcher.finish()

    if args:
        await matcher.finish("此命令不接受参数。")

    try:
        await _request_power_action(action)
    except ConfigurationError:
        await log_message(f"[nas_power] {action} is not configured")
        await matcher.finish("NAS 电源控制尚未配置。")
    except httpx.HTTPStatusError as exc:
        await log_message(
            f"[nas_power] NAS returned HTTP {exc.response.status_code} for {action}"
        )
        await matcher.finish(f"NAS 未接受请求（HTTP {exc.response.status_code}）。")
    except httpx.HTTPError as exc:
        await log_message(f"[nas_power] NAS request failed for {action}: {exc}")
        await matcher.finish("无法连接 NAS 电源服务。")
    except (RuntimeError, ValueError) as exc:
        await log_message(f"[nas_power] Invalid NAS response for {action}: {exc}")
        await matcher.finish("NAS 电源服务返回异常。")

    if action == "wake":
        await matcher.finish("已发送开机请求。")
    await matcher.finish("已发送关机请求。")


nas_wake = on_cmd("开机", priority=5, block=True)


@nas_wake.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    await _handle_power_command(nas_wake, event, "wake", arg.extract_plain_text().strip())


nas_shutdown = on_cmd("关机", priority=5, block=True)


@nas_shutdown.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    await _handle_power_command(nas_shutdown, event, "shutdown", arg.extract_plain_text().strip())
