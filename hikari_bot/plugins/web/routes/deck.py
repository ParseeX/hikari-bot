import asyncio
import io
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from hikari_bot.services.ygodeck import (
    generate_deck_image,
    generate_deck_list_pdf,
    get_deck_text_from_url,
    is_deck_code,
    is_deck_url,
    record_deck_usage,
)

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/deck/generate")
async def generate(
    request: Request,
    input_type: str = Form(...),
    language: str = Form("sc"),
    deck_link: str = Form(None),
    ydk_file: UploadFile = File(None),
    output: Literal["pdf", "image"] = Form("pdf"),
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
        ydk_text = ydk_bytes.decode("utf-8", errors="ignore")
        if not is_deck_code(ydk_text):
            return JSONResponse({"success": False, "message": "请上传正确的卡组文件。"})
        deck_text = ydk_text
    else:
        return JSONResponse({"success": False, "message": "未知错误。"})

    if output == "image":
        try:
            image = await generate_deck_image(deck_text, "", "")
            if image is None:
                return JSONResponse({"success": False, "message": "生成图片失败：额外卡组或副卡组超过15张。"})
            with image:
                image_buffer = io.BytesIO()
                await asyncio.to_thread(image.save, image_buffer, format="PNG")
                image_buffer.seek(0)
            # 图片和 PDF 属于同一次操作，仅 PDF 请求记录使用次数。
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
