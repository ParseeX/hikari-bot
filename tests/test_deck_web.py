import importlib.util
import io
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest


YDK = "#main\n89631139\n89631139\n#extra\n23995346\n!side\n14558127"


@pytest.fixture
def web(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "deck_web_test", Path(__file__).parents[1] / "hikari_bot/plugins/web/routes/deck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = {"pdf": [], "image": [], "usage": []}

    async def pdf(text, language):
        calls["pdf"].append((text, language))
        return io.BytesIO(b"%PDF-1.7\nexample")

    async def image(text, player, match):
        calls["image"].append((text, player, match))
        return Image.new("RGB", (20, 30), "red")

    monkeypatch.setattr(module, "generate_deck_list_pdf", pdf)
    monkeypatch.setattr(module, "generate_deck_image", image)
    monkeypatch.setattr(module, "record_deck_usage", calls["usage"].append)
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        yield module, client, calls


def upload(client, **data):
    return client.post("/deck/generate", data={"input_type": "ydk", **data},
                       files={"ydk_file": ("deck.ydk", YDK.encode(), "text/plain")})


def test_default_pdf_and_image_preserve_deck_and_count_only_once(web):
    _, client, calls = web
    pdf = upload(client, language="ja")
    image = upload(client, output="image", language="ja")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "deck_list_ja.pdf" in pdf.headers["content-disposition"]
    assert image.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(image.content)) as rendered:
        assert rendered.size == (20, 30)
    assert calls["pdf"] == [(YDK, "ja")]
    assert calls["image"] == [(YDK, "", "")]
    assert len(calls["usage"]) == 1


def test_link_image_uses_same_decoded_sections(web):
    import base64
    _, client, calls = web
    bits = "0000000100010001"
    for count, code in [(2, 89631139), (1, 23995346), (1, 14558127)]:
        bits += f"{count:02b}{code:027b}"
    bits += "0" * (-len(bits) % 8)
    encoded = base64.urlsafe_b64encode(int(bits, 2).to_bytes(len(bits) // 8, "big")).decode()
    response = client.post("/deck/generate", data={
        "input_type": "link", "output": "image",
        "deck_link": f"https://deck.ourygo.top/?ygotype=deck&d={encoded}",
    })
    assert response.headers["content-type"] == "image/png"
    assert calls["image"] == [(YDK, "", "")]
    assert calls["usage"] == []


@pytest.mark.parametrize("data,files", [
    ({"input_type": "link"}, None),
    ({"input_type": "link", "deck_link": "https://deck.ourygo.top/?ygotype=deck&d=bad"}, None),
    ({"input_type": "ydk"}, None),
    ({"input_type": "ydk"}, {"ydk_file": ("bad.ydk", b"invalid")}),
])
def test_invalid_input_does_not_render(web, data, files):
    _, client, calls = web
    response = client.post("/deck/generate", data={**data, "output": "image"}, files=files)
    assert response.json()["success"] is False
    assert not any(calls.values())


def test_image_failure_does_not_break_pdf_or_expose_exception(web, monkeypatch):
    module, client, calls = web

    async def fail(*args):
        raise RuntimeError("private filesystem path")

    monkeypatch.setattr(module, "generate_deck_image", fail)
    response = upload(client, output="image")
    assert response.status_code == 500
    assert "private" not in response.text
    assert calls["usage"] == []
    assert upload(client).content.startswith(b"%PDF")


def test_oversized_extra_or_side_returns_image_error(web, monkeypatch):
    module, client, _ = web
    from hikari_bot.services.ygodeck import generate_deck_image
    monkeypatch.setattr(module, "generate_deck_image", generate_deck_image)
    for section in ["#extra", "!side"]:
        response = client.post("/deck/generate", data={"input_type": "ydk", "output": "image"},
                               files={"ydk_file": ("deck.ydk", (section + "\n1" * 16).encode())})
        assert "超过15张" in response.json()["message"]


def test_unknown_output_is_rejected(web):
    _, client, calls = web
    assert upload(client, output="unknown").status_code == 422
    assert not any(calls.values())
