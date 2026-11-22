"""官方每日一卡在暂时超时与编号迁移后的真实图片加载路径。"""
import asyncio
import json
from types import SimpleNamespace

from hikari_bot.services import card_media
from test_card_image_fallback import Response, card_bytes


CARD = {'id': 69191257, 'passcode': '69191257', 'temporary_id': '101303082',
        'en_name': 'Jade Dragon Mech', 'cn_name': '翠铝之机龙'}


class Session:
    def __init__(self, *, name=CARD['en_name'], historical_retry=False):
        self.urls = []
        self.name, self.historical_retry = name, historical_retry

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        if '/api/v7/' in url:
            return Response(data=json.dumps({'data': [{'id': 69191257, 'name': self.name}]}).encode())
        if 'images.ygoprodeck.com' in url:
            return Response(data=card_bytes())
        if self.historical_retry and url.endswith('/101303082.jpg'):
            if self.urls.count(url) == 1:
                raise TimeoutError('do-not-log-private-error')
            return Response(data=card_bytes())
        return Response(404)


def test_migrated_daily_card_uses_verified_backup_and_caches(monkeypatch, tmp_path):
    session = Session()
    monkeypatch.setattr(card_media.aiohttp, 'ClientSession', lambda: session)
    media = card_media.CardMedia(SimpleNamespace(by_id=lambda code: CARD), tmp_path)
    assert asyncio.run(media.full(CARD['id'])) == card_bytes()
    calls = list(session.urls)
    assert any('/api/v7/' in url for url in calls)
    assert asyncio.run(media.full(CARD['id'])) == card_bytes()
    assert session.urls == calls


def test_historical_number_timeout_is_retried(monkeypatch, tmp_path):
    session = Session(name='Another Card', historical_retry=True)
    monkeypatch.setattr(card_media.aiohttp, 'ClientSession', lambda: session)
    media = card_media.CardMedia(SimpleNamespace(by_id=lambda code: CARD), tmp_path)
    assert asyncio.run(media.full(CARD['id'])) == card_bytes()
    assert sum(url.endswith('/101303082.jpg') for url in session.urls) == 2
    assert not any('images.ygoprodeck.com' in url for url in session.urls)


def test_wrong_identity_never_uses_unverified_image(monkeypatch, tmp_path):
    session = Session(name='Another Card')
    monkeypatch.setattr(card_media.aiohttp, 'ClientSession', lambda: session)
    media = card_media.CardMedia(SimpleNamespace(by_id=lambda code: CARD), tmp_path)
    assert asyncio.run(media.full(CARD['id'])) is None
    assert not any('images.ygoprodeck.com' in url for url in session.urls)
    assert not list(tmp_path.iterdir())
