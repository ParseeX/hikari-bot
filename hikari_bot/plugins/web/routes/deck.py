import asyncio
import io
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from hikari_bot.core.config import settings
from hikari_bot.services.deck_png import encode_deck_png
from hikari_bot.services.mycard_archive.queries import deck_title, query_rules
from hikari_bot.services.mycard_archive.replay import Deck
from hikari_bot.services.ygodeck import (
    generate_deck_image,
    generate_deck_list_pdf,
    get_deck_text_from_url,
    is_deck_code,
    is_deck_url,
    parse_ydk,
    record_deck_usage,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def identify_deck_name(deck_text):
    sections = [tuple(int(card) for card in section) for section in parse_ydk(deck_text)]
    deck = Deck("", *sections)
    rules = query_rules(settings.mycard_archive_dir / "deck-rules.json")
    return deck_title(deck, rules) or "未识别"


@router.post("/deck/generate")
async def generate(
    request: Request,
    input_type: str = Form(...),
    language: str = Form("sc"),
    deck_link: str = Form(None),
    ydk_file: UploadFile = File(None),
    output: Literal["pdf", "image"] = Form("pdf"),
    player_name: str = Form("", max_length=40),
    deck_name: str = Form("", max_length=80),
    event_name: str = Form("", max_length=60),
    result: str = Form("", max_length=30),
):
    if input_type == "link":
        if not deck_link or not is_deck_url(deck_link):
            return JSONResponse({"success": False, "message": "请上传正确的卡组链接。"})
        try:
            deck_text = get_deck_text_from_url(deck_link)
        except (ValueError, IndexError):
            return JSONResponse({"success": False, "message": "请上传正确的卡组链接。"})
    elif input_type == "ydk":
        if ydk_file is None:
            return JSONResponse({"success": False, "message": "请上传正确的卡组文件。"})
        ydk_bytes = await ydk_file.read()
        ydk_text = ydk_bytes.decode("utf-8-sig", errors="ignore")
        if not is_deck_code(ydk_text):
            return JSONResponse({"success": False, "message": "请上传正确的卡组文件。"})
        deck_text = ydk_text
    else:
        return JSONResponse({"success": False, "message": "未知错误。"})

    if output == "image":
        try:
            name = deck_name.strip() or await asyncio.to_thread(identify_deck_name, deck_text)
            image = await generate_deck_image(
                deck_text, player_name.strip(), event_name.strip(),
                result=result.strip(), deck_name=name,
            )
            if image is None:
                return JSONResponse({"success": False, "message": "生成图片失败：额外卡组或副卡组超过15张。"})
            image_buffer = io.BytesIO(await encode_deck_png(image))
            # 保持现有卡表统计口径，图片请求不计入 PDF 使用次数。
            return StreamingResponse(
                content=image_buffer,
                media_type="image/png",
                headers={"Content-Disposition": "inline; filename=deck.png"},
            )
        except Exception:
            logger.exception("生成网页卡组图片失败")
            return JSONResponse({"success": False, "message": "卡组图片生成失败，请稍后重试。"}, status_code=500)

    try:
        pdf_buffer = await generate_deck_list_pdf(deck_text, language)
    except ValueError as e:
        return JSONResponse({"success": False, "message": str(e)})
    if pdf_buffer is None:
        return JSONResponse({"success": False, "message": "生成卡表失败：额外卡组或副卡组超过15张。"})
    record_deck_usage(request.client.host)
    return StreamingResponse(
        content=pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=deck_list_{language}.pdf"},
    )
