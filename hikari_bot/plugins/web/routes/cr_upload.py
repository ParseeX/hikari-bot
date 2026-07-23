"""
cr_upload.py — 接收本地爬虫上传的 Cardrush 价格数据，写入数据库。

鉴权：请求头 X-API-Key 必须与环境变量 CARDRUSH_UPLOAD_TOKEN 一致。
"""
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from hikari_bot.core.config import settings
from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush import (
    CardrushError,
    PriceRecord as DomainPriceRecord,
    get_default_cardrush_service,
)

router = APIRouter()
service = get_default_cardrush_service()

# ── 鉴权 ────────────────────────────────────────────────────────────────────

def _get_expected_key() -> str:
    key = settings.cardrush_upload_token
    if not key:
        raise RuntimeError("CARDRUSH_UPLOAD_TOKEN is not set, all upload requests rejected")
    return key


def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    try:
        expected = _get_expected_key()
    except RuntimeError as e:
        logging.error(str(e))
        raise HTTPException(status_code=503, detail="API key not configured on server")

    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── 数据模型 ─────────────────────────────────────────────────────────────────

class PriceRecord(BaseModel):
    product_id: int
    name: str
    price: int
    rarity: Optional[str] = None
    model_number: Optional[str] = None
    updated_at: Optional[str] = None


class UploadPayload(BaseModel):
    prices: list[PriceRecord]


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.post("/cr_upload", dependencies=[Depends(verify_api_key)])
async def cr_upload(payload: UploadPayload):
    records = [
        DomainPriceRecord.from_mapping(record.model_dump())
        for record in payload.prices
    ]
    try:
        saved = await service.save_prices(records)
    except CardrushError as error:
        await log_message(
            f"[cr_upload] save_prices failed: {error}"
        )
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error
    if saved > 0:
        await log_message(
            f"[cr_upload] Finish checking with {saved} change(s)."
        )
    return {
        "ok": True,
        "received": len(records),
        "saved": saved,
    }
