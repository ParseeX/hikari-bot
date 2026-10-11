import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest


YDK = "#main\n89631139\n89631139\n#extra\n23995346\n!side\n14558127"


@pytest.fixture
def web(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "deck_web_test", Path(__file__).parents[1] / "hikari_bot/plugins/web/routes/deck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "settings", SimpleNamespace(mycard_archive_dir=tmp_path))
    (tmp_path / 'deck-rules.json').write_text(json.dumps({'version': 1, 'rules': [
        {'label': '青眼', 'cards': [89631139]},
        {'label': '额外不参与识别', 'cards': [23995346]},
        {'label': '副卡不参与识别', 'cards': [14558127]},
    ]}), encoding='utf-8')
    calls = {"pdf": [], "image": [], "usage": []}

    async def pdf(text, language):
        calls["pdf"].append((text, language))
        return io.BytesIO(b"%PDF-1.7\nexample")

    async def image(text, player, match, result='', deck_name=''):
        calls["image"].append((text, player, match, result, deck_name))
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
    assert calls["image"] == [(YDK, "", "", "", "青眼")]
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
    assert calls["image"] == [(YDK, "", "", "", "青眼")]
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

    async def fail(*args, **kwargs):
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


def test_custom_image_information_overrides_auto_identification(web, monkeypatch):
    module, client, calls = web
    def forbidden(*args):
        pytest.fail('手填卡组名时不应读取分类规则')
    monkeypatch.setattr(module, 'identify_deck_name', forbidden)
    response = upload(client, output='image', player_name=' 水桥 ', deck_name=' 自定义青眼 ',
                      event_name=' 周赛 ', result=' 冠军 ')
    assert response.headers['content-type'] == 'image/png'
    assert calls['image'] == [(YDK, '水桥', '周赛', '冠军', '自定义青眼')]


def test_blank_name_uses_current_rules_and_sixty_card_prefix(web):
    module, client, calls = web
    text = '#main\n' + '\n'.join(['89631139'] * 60) + '\n#extra\n!side'
    response = client.post('/deck/generate', data={'input_type': 'ydk', 'output': 'image', 'deck_name': '   '},
                           files={'ydk_file': ('deck.ydk', ('\ufeff' + text).encode())})
    assert response.headers['content-type'] == 'image/png'
    assert calls['image'][-1][-1] == '60青眼'
    (module.settings.mycard_archive_dir / 'deck-rules.json').write_text(
        json.dumps({'version': 1, 'rules': [{'label': '更新名称', 'cards': [89631139]}]}), encoding='utf-8')
    assert upload(client, output='image').headers['content-type'] == 'image/png'
    assert calls['image'][-1][-1] == '更新名称'


def test_unmatched_deck_uses_existing_fallback(web):
    module, client, calls = web
    (module.settings.mycard_archive_dir / 'deck-rules.json').write_text(
        '{"version":1,"rules":[]}', encoding='utf-8')
    assert upload(client, output='image').headers['content-type'] == 'image/png'
    assert calls['image'][-1][-1] == '未识别'


def test_pdf_does_not_read_image_rules(web, monkeypatch):
    module, client, calls = web
    def forbidden(*args):
        pytest.fail('PDF 不应使用图片信息或识别分类')
    monkeypatch.setattr(module, 'identify_deck_name', forbidden)
    assert upload(client, player_name='水桥', deck_name='测试').content.startswith(b'%PDF')
    assert calls['image'] == []


@pytest.mark.parametrize('field,limit', [('player_name', 40), ('deck_name', 80), ('event_name', 60), ('result', 30)])
def test_image_information_length_is_bounded(web, field, limit):
    _, client, calls = web
    assert upload(client, output='image', **{field: '名' * (limit + 1)}).status_code == 422
    assert not any(calls.values())
