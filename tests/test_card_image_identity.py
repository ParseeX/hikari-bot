"""正式卡密与外部图片编号不同，HTTP 200 不能代表卡片身份正确。"""
import asyncio
import json
import pytest
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from hikari_bot.services import ygocard
from test_card_image_fallback import Response, card_bytes


CARD = {
    'id': 10000100, 'temporary_id': '101307030',
    'wiki_en': 'The Winged Dragon of Ra, Sun God of Darkness',
}
CDN_URL = 'https://cdncf.moecube.com/ygopro-super-pre/data/pics/101307030.jpg'


class IdentitySession:
    def __init__(self, metadata=None):
        self.urls = []
        self.metadata = metadata or {'data': [{'id': 101403030, 'name': CARD['wiki_en']}]}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        if '/api/v7/' in url:
            if isinstance(self.metadata, Exception):
                raise self.metadata
            return Response(data=json.dumps(self.metadata).encode())
        if url == CDN_URL:
            return Response(data=card_bytes())
        if 'ygoprodeck.com/images/' in url and url.endswith('/10000100.jpg'):
            # 模拟能解码、非空白，但内容不属于这张卡的主源图片。
            image = Image.new('RGB', (624, 624), 'green')
            image.paste('yellow', (0, 0, 80, 80))
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            return Response(data=buffer.getvalue())
        return Response(404)


def test_ra_query_ignores_wrong_200_image_and_uses_historical_cdn_id(monkeypatch):
    session = IdentitySession()
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(
        by_id=lambda code: CARD, image_ids=lambda name: [10000100]))
    images = asyncio.run(ygocard.get_card_images('暗黒の太陽神－ラーの翼神竜'))
    assert images[0][0] == 10000100
    with Image.open(BytesIO(images[0][1])) as image:
        assert image.getpixel((312, 312))[2] > 200, '应返回翼神龙插画，而不是主源编号冲突的图片'
    assert CDN_URL in session.urls
    assert not any(url.endswith('/10000100.jpg') and '/images/' in url for url in session.urls)


@pytest.mark.parametrize('metadata', [TimeoutError(), {'data': [{'id': 10000100, 'name': 'Wrong Card'}]}])
def test_unverified_identity_skips_unsafe_primary(monkeypatch, metadata):
    session = IdentitySession(metadata)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    data = asyncio.run(ygocard.get_image_by_id(10000100))
    assert data
    assert CDN_URL in session.urls
    assert not any('/images/' in url for url in session.urls)


def test_full_card_ignores_old_unverified_backup_and_reuses_identified_cache(monkeypatch, tmp_path):
    session = IdentitySession()
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    old_cache = tmp_path / 'fallback' / '10000100.jpg'
    old_cache.parent.mkdir()
    old_data = session.get(ygocard.IMAGE_ORIGIN + '10000100.jpg').data
    old_cache.write_bytes(old_data)
    session.urls.clear()
    assert asyncio.run(ygocard.get_ygopic(10000100)) == card_bytes()
    assert old_cache.read_bytes() == old_data
    assert (tmp_path / 'fallback-identified' / '10000100.jpg').read_bytes() == card_bytes()
    requests = list(session.urls)
    assert asyncio.run(ygocard.get_ygopic(10000100)) == card_bytes()
    assert session.urls == requests


def test_verified_primary_remains_preferred(monkeypatch):
    session = IdentitySession()
    original = session.get
    def get(url, **kwargs):
        if url == ygocard.IMAGE_ORIGIN + '101403030.jpg':
            session.urls.append(url)
            return Response(data=card_bytes())
        return original(url, **kwargs)
    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    assert asyncio.run(ygocard.get_image_by_id(10000100)) == card_bytes()
    assert len(session.urls) == 2
    assert not any('moecube' in url or 'momobako' in url for url in session.urls)


def test_registered_alternate_art_keeps_its_own_id(monkeypatch):
    session = IdentitySession()
    original = session.get
    def get(url, **kwargs):
        if url == ygocard.IMAGE_ORIGIN + '10000101.jpg':
            session.urls.append(url)
            return Response(data=card_bytes())
        return original(url, **kwargs)
    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    assert asyncio.run(ygocard.get_image_by_id(10000101)) == card_bytes()
    assert session.urls == [ygocard.IMAGE_ORIGIN + '10000101.jpg']


def test_unavailable_old_cdn_id_still_tries_current_id(monkeypatch):
    session = IdentitySession()
    original = session.get
    def get(url, **kwargs):
        if url == CDN_URL:
            session.urls.append(url)
            return Response(404)
        if 'moecube' in url and url.endswith('/10000100.jpg'):
            session.urls.append(url)
            return Response(data=card_bytes())
        return original(url, **kwargs)
    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    assert asyncio.run(ygocard.get_image_by_id(10000100))
    assert session.urls[-2:] == [CDN_URL, CDN_URL.replace('101307030', '10000100')]


def test_direct_temporary_id_uses_same_verified_identity(monkeypatch):
    session = IdentitySession()
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', lambda: session)
    monkeypatch.setattr(ygocard, 'catalog', SimpleNamespace(by_id=lambda code: CARD))
    assert asyncio.run(ygocard.get_image_by_id(101307030))
    assert session.urls[-1] == CDN_URL
    assert ygocard.IMAGE_ORIGIN + '101403030.jpg' in session.urls
