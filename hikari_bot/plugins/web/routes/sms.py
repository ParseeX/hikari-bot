import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hikari_bot.core.whitelist import message_superusers

router = APIRouter()

# ── 短信黑名单（手动维护，重启后生效）────────────────────────────────────────────

_BLACKLIST: set[str] = {
    "95566",
    "10000"
}


class SmsPayload(BaseModel):
    from_: str = Field(alias="from")
    to: Optional[str] = ""
    tos: Optional[List[str]] = []
    toName: Optional[str] = ""
    toNames: Optional[List[str]] = []
    content: str
    dir: Optional[str] = ""
    date: Optional[str] = ""
    simSlot: Optional[int] = None

@router.post("/sms")
async def sms_handler(payload: SmsPayload):
    # 黑名单检查
    if payload.from_ in _BLACKLIST:
        return {"ok": True, "skipped": True}

    # 时间格式化（失败就原样）
    try:
        dt = datetime.fromisoformat(payload.date.replace("Z", "+00:00"))
        time_fmt = dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        await log_message(f"[sms_route] Exception occurred while parsing date: {e}")
        time_fmt = payload.date or "unknown"

    # 1️⃣ 完整短信（原文）
    full_msg = (
        "📩 收到一条新短信\n"
        "━━━━━━━━━━━━\n"
        f"📞 来自：{payload.from_}\n"
        f"🕒 时间：{time_fmt}\n"
        "💬 内容：\n"
        f"{payload.content}\n"
        "━━━━━━━━━━━━"
    )

    await message_superusers(full_msg)

    # 2️⃣ 验证码（如果有）
    code_candidates = [m for m in re.findall(r"\d+", payload.content) if len(m) in (4, 6, 8)]
    if code_candidates:
        await message_superusers(code_candidates[-1])

    return {"ok": True}