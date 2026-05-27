"""
uptime_webhook.py - 接收 Uptime Kuma Webhook 并通知超级用户。

鉴权：请求头 X-Uptime-Token 必须与环境变量 UPTIME_TOKEN 一致。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from nonebot import get_driver

from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers

router = APIRouter()


def _get_expected_token() -> str:
    token = getattr(get_driver().config, "uptime_token", "") or ""
    token = token.strip()
    if not token:
        raise RuntimeError("UPTIME_TOKEN is not set, all uptime webhook requests rejected")
    return token


def verify_uptime_token(x_uptime_token: Optional[str] = Header(default=None)):
    try:
        expected = _get_expected_token()
    except RuntimeError as e:
        logging.error(str(e))
        raise HTTPException(status_code=503, detail="Uptime token not configured on server")

    if not x_uptime_token or x_uptime_token != expected:
        raise HTTPException(status_code=401, detail="Invalid uptime token")


@router.post("/uptime_webhook", dependencies=[Depends(verify_uptime_token)])
async def uptime_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    monitor = data.get("monitor") if isinstance(data.get("monitor"), dict) else {}
    heartbeat = data.get("heartbeat") if isinstance(data.get("heartbeat"), dict) else {}

    status_map = {
        0: "故障",
        1: "恢复",
        2: "暂停",
    }

    name = monitor.get("name") or "Unknown"
    status = heartbeat.get("status")
    status_text = status_map.get(status, f"未知({status})")
    detail = (
        data.get("msg")
        or heartbeat.get("msg")
        or "-"
    )

    text = (
        "【Uptime Kuma】\n"
        f"服务：{name}\n"
        f"状态：{status_text}\n"
        f"信息：{detail}"
    )

    try:
        await message_superusers(text)
    except Exception as e:
        await log_message(f"[uptime_webhook] Failed to notify superusers: {e}")
        raise HTTPException(status_code=500, detail="Failed to send notification")

    return {"ok": True}
