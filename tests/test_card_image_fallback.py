"""真实调用入口的先行卡 CDN 回退回归测试。"""
import asyncio
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from hikari_bot.services import ygocard, ygodeck


CDN = 'https://cdncf.moecube.com/ygopro-super-pre/data/pics/'


def card_bytes():
    image = Image.new('RGB', (400, 580), 'red')
    ImageDraw.Draw(image).rectangle((53, 110, 347, 404), fill='blue')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class Response:
    def __init__(self, status=200, data=b''):
        self.status, self.data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self.data


class Session:
    def __init__(self, primary, backup=None):
        self.primary = primary
        self.backup = backup or Response(data=card_bytes())
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        response = self.backup if url.startswith(CDN) else self.primary
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize('failure', [Response(404), Response(data=b'<html>error</html>'), TimeoutError()])
@pytest.mark.parametrize('artwork', [True, False])
def test_both_entrypoints_recover_from_missing_invalid_or_timed_out_sources(monkeypatch, tmp_path, failure, artwork):
    session = Session(failure)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))

    async def unknown():
        return None

    monkeypatch.setattr(ygocard, 'get_unknown_card', unknown)
    loader = ygocard.get_image_by_id if artwork else ygocard.get_ygopic
    data = asyncio.run(loader(101307030))
    assert data is not None, '现有图片源失败后应从先行卡 CDN 取得卡图'
    assert session.urls[-1] == CDN + '101307030.jpg'
    with Image.open(BytesIO(data)) as image:
        assert image.size == ((295, 295) if artwork else (400, 580))
        assert image.getpixel((147, 147))[2] > 200
    if not artwork:
        assert (tmp_path / 'fallback' / '101307030.jpg').read_bytes() == data
        assert asyncio.run(loader(101307030)) == data
        assert len(session.urls) == 3


@pytest.mark.parametrize('artwork', [True, False])
def test_primary_success_does_not_contact_cdn(monkeypatch, tmp_path, artwork):
    session = Session(Response(data=card_bytes()))
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    loader = ygocard.get_image_by_id if artwork else ygocard.get_ygopic
    assert asyncio.run(loader(101307030)) == card_bytes()
    assert len(session.urls) == 1
    assert not session.urls[0].startswith(CDN)


@pytest.mark.parametrize('artwork', [True, False])
def test_blank_images_and_unknown_placeholders_fall_back(monkeypatch, tmp_path, artwork):
    from hikari_bot.services.card_image_cache import _UNKNOWN
    images = [Image.new('RGB', (400, 580), 'white'), Image.frombytes('RGB', (8, 8), _UNKNOWN)]
    for image in images:
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        session = Session(Response(data=buffer.getvalue()))
        monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
        monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path / str(len(buffer.getvalue()))))
        loader = ygocard.get_image_by_id if artwork else ygocard.get_ygopic
        assert asyncio.run(loader(101307030))
        assert session.urls[-1] == CDN + '101307030.jpg'


@pytest.mark.parametrize('first', [TimeoutError(), Response(503), Response(429)])
def test_cdn_transient_failure_retries_once(monkeypatch, first):
    session = Session(Response(404))
    original_get = session.get
    attempts = iter([first, Response(data=card_bytes())])

    def get(url, **kwargs):
        if url.startswith(CDN):
            session.backup = next(attempts)
        assert kwargs['timeout'].total == 6
        return original_get(url, **kwargs)

    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    assert asyncio.run(ygocard.get_image_by_id(101307030))
    assert session.urls.count(CDN + '101307030.jpg') == 2


@pytest.mark.parametrize('failure, attempts', [(Response(404), 1), (Response(data=b'bad'), 1), (TimeoutError(), 2)])
def test_all_sources_failure_is_bounded_and_not_cached(monkeypatch, tmp_path, failure, attempts):
    session = Session(Response(404), failure)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))

    async def unknown():
        return None

    monkeypatch.setattr(ygocard, 'get_unknown_card', unknown)
    assert asyncio.run(ygocard.get_ygopic(123)) is None
    assert session.urls.count(CDN + '123.jpg') == attempts
    assert not list(tmp_path.rglob('*.jpg'))
    session.primary = Response(data=card_bytes())
    assert asyncio.run(ygocard.get_ygopic(123)) == card_bytes()


def test_deck_timeout_keeps_other_cards_and_duplicate_positions(monkeypatch):
    cancelled = []

    async def download(code):
        if code == '1':
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(code)
        return card_bytes()

    monkeypatch.setattr(ygodeck, 'get_ygopic', download)
    monkeypatch.setattr(ygodeck, 'CARD_IMAGE_LOAD_TIMEOUT', .03)

    async def render():
        return await asyncio.wait_for(ygodeck.generate_deck_image('#main\n1\n2\n1\n#extra\n2\n!side\n2', '', ''), 3)

    image = asyncio.run(render())
    assert cancelled == ['1']
    assert image.getpixel((40, 140)) == (238, 238, 238)
    assert image.getpixel((255, 140)) == (255, 0, 0)
    image.close()


def test_cancellation_does_not_start_fallback_requests(monkeypatch):
    session = Session(asyncio.CancelledError())
    # CancelledError 继承 BaseException，明确让请求抛出取消信号。
    def get(url, **kwargs):
        session.urls.append(url)
        raise asyncio.CancelledError()
    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ygocard.get_image_by_id(101307030))
    assert len(session.urls) == 1
