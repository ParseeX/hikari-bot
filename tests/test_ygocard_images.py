import asyncio
from io import BytesIO
from types import SimpleNamespace

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


def image_bytes(size=(624, 624), color='red'):
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def test_artwork_size_preserves_matching_image_and_pads_other_ratios():
    original = image_bytes()
    assert ygocard.normalize_card_art(original) == original
    for size in [(321, 321), (300, 150)]:
        with Image.open(BytesIO(ygocard.normalize_card_art(image_bytes(size)))) as image:
            assert image.size == (624, 624)
            assert image.getpixel((312, 312))[0] > 240
            if size == (300, 150):
                assert min(image.getpixel((312, 10))) > 240


def test_all_artworks_keep_order_with_bounded_downloads_and_partial_failure(monkeypatch):
    ids = [10, 11, 12, 13, 14]
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(image_ids=lambda keyword: ids))
    active = maximum = 0
    async def download(card_id):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep((15 - card_id) * .001)
            if card_id == 12:
                raise TimeoutError
            return image_bytes((321, 321))
        finally:
            active -= 1
    monkeypatch.setattr(ygocard, 'get_image_by_id', download)
    images = asyncio.run(ygocard.get_card_images('青眼白龙'))
    assert [card_id for card_id, _ in images] == ids
    assert images[2][1] is None
    assert maximum == 3
    for _, raw in images:
        if raw:
            with Image.open(BytesIO(raw)) as image:
                assert image.size == (624, 624)


def test_unknown_card_does_not_download_images(monkeypatch):
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(image_ids=lambda keyword: []))
    assert asyncio.run(ygocard.get_card_images('unknown')) == []
