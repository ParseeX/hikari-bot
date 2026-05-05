"""
cr_upload.py — 接收本地爬虫上传的 Cardrush 价格数据，写入数据库。

鉴权：请求头 X-API-Key 必须与环境变量 CARDRUSH_UPLOAD_TOKEN 一致。
"""
import os
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from nonebot import get_driver

from hikari_bot.services.price import save_prices
from hikari_bot.core.logger import log_message

router = APIRouter()

# ── 鉴权 ────────────────────────────────────────────────────────────────────

def _get_expected_key() -> str:
    key = getattr(get_driver().config, "cardrush_upload_token", "") or ""
    key = key.strip()
    if not key:
        raise RuntimeError("CARDRUSH_UPLOAD_TOKEN is not set, all upload requests rejected")
    return key


def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    try:
        expected = _get_expected_key()
    except RuntimeError as e:
        logging.error(str(e))
        raise HTTPException(status_code=503, detail="API key not configured on server")

    if not x_api_key or x_api_key != expected:
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
    prices_data: list[dict[str, Any]] = [r.model_dump() for r in payload.prices]
    try:
        saved = await _run_save(prices_data)
    except Exception as e:
        await log_message(f"[cr_upload] save_prices failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    if saved > 0:
        await log_message(f"[cr_upload] Finish checking with {saved} change(s).")
    return {"ok": True, "received": len(prices_data), "saved": saved}


# save_prices 是同步函数，用线程池运行避免阻塞事件循环
import asyncio
import functools

async def _run_save(prices_data):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(save_prices, prices_data))
