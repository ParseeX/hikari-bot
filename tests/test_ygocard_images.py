import asyncio
import json
import struct
import pytest
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

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.urls.append(url)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


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


def card_bytes(size=(200, 290)):
    image = Image.new('RGB', size, 'red')
    ImageDraw.Draw(image).rectangle((20, 40, 180, 200), fill='blue')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_temporary_id_resolves_exact_card_and_caches_real_backup(monkeypatch, tmp_path):
    card = card_bytes()
    session = _FakeSession([
        _FakeResponse(404), _FakeResponse(404),
        _FakeResponse(200, json.dumps({'data': [{'id': 50849686, 'name': 'Flashforce Sword'}]}).encode()),
        _FakeResponse(200, card),
    ])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: {'wiki_en': 'Flashforce Sword'}))
    assert asyncio.run(ygocard.get_ygopic(100268021)) == card
    assert session.urls[-1] == f'{ygocard.IMAGE_FULL}50849686.jpg'
    assert not (tmp_path/'100268021.jpg').exists()
    assert (tmp_path/'fallback'/'100268021.jpg').read_bytes() == card
    assert asyncio.run(ygocard.get_ygopic(100268021)) == card
    assert len(session.urls) == 4


def test_blank_cache_and_primary_are_replaced_by_real_backup(monkeypatch, tmp_path):
    blank = image_bytes((200, 290), 'white')
    (tmp_path/'123.jpg').write_bytes(blank)
    card = card_bytes()
    session = _FakeSession([_FakeResponse(200, blank), _FakeResponse(200, card)])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    assert asyncio.run(ygocard.get_ygopic(123)) == card
    assert (tmp_path/'fallback'/'123.jpg').read_bytes() == card


def test_failed_sources_never_cache_placeholder_and_next_call_recovers(monkeypatch, tmp_path):
    from hikari_bot.services.card_image_cache import _UNKNOWN
    placeholder = BytesIO()
    Image.frombytes('RGB', (8, 8), _UNKNOWN).save(placeholder, format='PNG')
    unknown = placeholder.getvalue()
    session = _FakeSession([_FakeResponse(200, unknown), _FakeResponse(200, unknown)])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    async def missing():
        return unknown
    monkeypatch.setattr(ygocard, 'get_unknown_card', missing)
    assert asyncio.run(ygocard.get_ygopic(123)) == unknown
    assert not list(tmp_path.rglob('*.jpg'))
    card = card_bytes()
    session.responses = iter([_FakeResponse(200, card)])
    assert asyncio.run(ygocard.get_ygopic(123)) == card
    assert (tmp_path/'123.jpg').read_bytes() == card


def test_wrong_name_does_not_resolve_temporary_card(monkeypatch):
    session = _FakeSession([_FakeResponse(200, json.dumps({'data': [
        {'id': 50849686, 'name': 'Different Sword'}]}).encode())])
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: {'wiki_en': 'Flashforce Sword'}))
    assert asyncio.run(ygocard._resolved_image_id(session, 100268021)) is None


@pytest.mark.parametrize('data', [image_bytes(color='white'), image_bytes(color='black'), b'not an image'])
def test_blank_and_corrupt_card_images_rejected(data):
    with pytest.raises((ValueError, OSError)):
        ygocard.valid_card_image(data)


def test_transparent_card_image_rejected():
    image = Image.new('RGBA', (200, 290), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 40, 180, 200), fill=(255, 0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    with pytest.raises(ValueError):
        ygocard.valid_card_image(buffer.getvalue())


def test_background_refresh_upgrades_cached_backup(monkeypatch, tmp_path):
    backup = tmp_path/'fallback'
    backup.mkdir()
    (backup/'123.jpg').write_bytes(card_bytes())
    chinese = card_bytes((400, 580))
    session = _FakeSession([_FakeResponse(200, chinese)])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    assert asyncio.run(ygocard.get_ygopic(123, refresh=True)) == chinese
    assert asyncio.run(ygocard.get_ygopic(123)) == chinese
    assert len(session.urls) == 1


def test_artwork_size_normalizes_matching_image_and_pads_other_ratios():
    for size in [(624, 624), (321, 321), (300, 150)]:
        with Image.open(BytesIO(ygocard.normalize_card_art(image_bytes(size)))) as image:
            assert image.size == (624, 624)
            assert image.getpixel((312, 312))[0] > 240
            if size == (300, 150):
                assert min(image.getpixel((312, 10))) > 240


def test_matching_artwork_removes_embedded_jpeg_thumbnail():
    thumbnail = BytesIO()
    Image.new('RGB', (120, 120), 'blue').save(thumbnail, format='JPEG')
    thumbnail = thumbnail.getvalue()
    # 构造 EXIF 的 IFD1 缩略图，复现源图片中小图位于主图之前的结构。
    entries = [(256, 4, 120), (257, 4, 120), (259, 3, 6),
               (513, 4, 80), (514, 4, len(thumbnail))]
    exif = b'Exif\x00\x00' + b'II' + struct.pack('<HI', 42, 8)
    exif += struct.pack('<HIH', 0, 14, len(entries))
    for tag, kind, value in entries:
        exif += struct.pack('<HHII', tag, kind, 1, value)
    exif += struct.pack('<I', 0) + thumbnail
    source = BytesIO()
    Image.new('RGB', (624, 624), 'red').save(source, format='JPEG', exif=exif)

    def first_jpeg_size(raw):
        for offset in range(len(raw) - 9):
            if raw[offset:offset + 2] in (b'\xff\xc0', b'\xff\xc2'):
                return struct.unpack('>HH', raw[offset + 5:offset + 9])

    assert first_jpeg_size(source.getvalue()) == (120, 120)
    normalized = ygocard.normalize_card_art(source.getvalue())
    assert first_jpeg_size(normalized) == (624, 624)
    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (624, 624)
        assert not image.getexif()
        assert image.getpixel((312, 312))[0] > 240


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


def test_full_card_timeout_uses_backup_instead_of_placeholder(monkeypatch, tmp_path):
    session = _FakeSession([TimeoutError(), _FakeResponse(200, card_bytes((421, 614)))])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    data = asyncio.run(ygocard.get_ygopic(23219323, half=False))
    assert data is not None
    assert session.urls[-1] == 'https://images.ygoprodeck.com/images/cards/23219323.jpg'
    with Image.open(BytesIO(data)) as image:
        assert image.size == (421, 614)


def test_invalid_primary_artwork_uses_backup(monkeypatch):
    session = _FakeSession([_FakeResponse(200, b'<html>error</html>'), _FakeResponse(200, image_bytes((400, 580)))])
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    data = asyncio.run(ygocard.get_image_by_id(23219323))
    with Image.open(BytesIO(data)) as image:
        assert image.width > 0
    assert len(session.urls) == 2
