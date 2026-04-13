import os
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from hikari_bot.core.constants import *
from hikari_bot.services.ygodeck import *

router = APIRouter()
BASE_DIR = Path(WEB_DIR)
TEMPLATE_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

@router.get("/deck", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("deck.html", {"request": request})

@router.post("/deck/generate", response_class=HTMLResponse)
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

    pdf_buffer = await generate_deck_list_pdf(deck_text, language)
    record_deck_usage(request.client.host)
    return StreamingResponse(
        content=pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=deck_list_{language}.pdf"},
    )