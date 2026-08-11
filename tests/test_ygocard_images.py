import asyncio
from io import BytesIO

from PIL import Image, ImageDraw
from hikari_bot.services import ygocard


class _FakeResponse:
    def __init__(self, status: int, data: bytes = b""):
        self.status = status
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def read(self) -> bytes:
        return self.data


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = iter(responses)
        self.urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url: str) -> _FakeResponse:
        self.urls.append(url)
        return next(self.responses)


def test_get_image_by_id_falls_back_to_chinese_source(monkeypatch):
    source_image = Image.new("RGB", (400, 580), "red")
    ImageDraw.Draw(source_image).rectangle((53, 110, 347, 404), fill="blue")
    image_buffer = BytesIO()
    source_image.save(image_buffer, format="JPEG")

    session = _FakeSession([
        _FakeResponse(404),
        _FakeResponse(200, image_buffer.getvalue()),
    ])
    logs: list[str] = []

    async def fake_log_message(message: str):
        logs.append(message)

    monkeypatch.setattr(ygocard.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(ygocard, "log_message", fake_log_message)

    image = asyncio.run(ygocard.get_image_by_id(12345678))

    with Image.open(BytesIO(image)) as cropped_image:
        assert cropped_image.size == (295, 295)
        assert cropped_image.getpixel((147, 147))[2] > 200
    assert session.urls == [
        f"{ygocard.IMAGE_ORIGIN}12345678.jpg",
        f"{ygocard.IMAGE_CHINESE}12345678.jpg",
    ]
    assert "IMAGE_ORIGIN" in logs[0]
