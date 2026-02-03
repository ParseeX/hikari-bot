from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List, Optional
from hikari_bot.utils.whitelist import message_superusers

router = APIRouter()

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
    # 时间格式化（失败就原样）
    try:
        dt = datetime.fromisoformat(payload.date.replace("Z", "+00:00"))
        time_fmt = dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
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
    m = re.search(r"\b\d{4,8}\b", payload.content)
    if m:
        await message_superusers(f"{m.group(0)}")

    return {"ok": True}