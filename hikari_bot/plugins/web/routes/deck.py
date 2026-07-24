from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from hikari_bot.services.ygodeck import (
    generate_deck_list_pdf,
    get_deck_text_from_url,
    is_deck_code,
    is_deck_url,
    record_deck_usage,
)

router = APIRouter()

@router.post("/deck/generate")
async def generate(
    request: Request,
    input_type: str = Form(...),
    language: str = Form("sc"),
    deck_link: str = Form(None),
    ydk_file: UploadFile = File(None),
):
    if input_type == "link":
        if not is_deck_url(deck_link):
            return JSONResponse({"success": False, "message": "请上传正确的卡组链接。"})
        deck_text = get_deck_text_from_url(deck_link)
    elif input_type == "ydk":
        ydk_bytes = await ydk_file.read()
        ydk_text = ydk_bytes.decode("utf-8", errors="ignore")
        if not is_deck_code(ydk_text):
            return JSONResponse({"success": False, "message": "请上传正确的卡组文件。"})
        deck_text = ydk_text
    else:
        return JSONResponse({"success": False, "message": "未知错误。"})

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
