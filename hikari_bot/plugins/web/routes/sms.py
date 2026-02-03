from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from hikari_bot.utils.whitelist import message_superusers

router = APIRouter()

class SmsPayload(BaseModel):
    from_: str
    to: Optional[str] = ""
    tos: Optional[List[str]] = []
    toName: Optional[str] = ""
    toNames: Optional[List[str]] = []
    content: str
    dir: Optional[str] = ""
    date: Optional[str] = ""
    simSlot: Optional[int] = None

    # 兼容字段名 "from"
    class Config:
        populate_by_name = True
        fields = {"from_": "from"}

@router.post("/sms")
async def tome_handler(payload: SmsPayload):
    sender = payload.from_
    text = payload.content
    when = payload.date

    msg = f"[SMS] from={sender}\n{text}\n({when})"

    await message_superusers(msg)

    return {"ok": True}