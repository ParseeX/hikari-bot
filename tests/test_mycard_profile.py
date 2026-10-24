import asyncio
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from hikari_bot.services.mycard_archive.profile import profile_payload, profile_ydk
from hikari_bot.services.mycard_archive.full_image import fetch_card_image, normalize_image, CHINESE, ENGLISH


def test_repeated_cards_missing_catalog_and_large_side_are_not_dropped():
    class Catalog:
        def deck_sort_data(self, codes): return {}
        def cards_by_id(self, codes):
            return {42: {'id': 24, 'cn_name': '<test>', 'catalog_id': 123, 'data': {}, 'text': {'desc': '说明'}}}
    version = SimpleNamespace(deck=SimpleNamespace(main=(42,) * 35 + (999,), extra=(), side=tuple(range(1, 19))), updated_at=123)
    data = profile_payload('玩家\n#main', version, {}, Catalog())
    deck = data['deck']
    assert len(deck['main']) == 36 and len(deck['side']) == 18
    assert deck['cards']['42']['code'] == 42 and deck['cards']['42']['id'] == 24
    assert deck['cards']['999']['name'] == '卡片 999'
    assert 'catalog_id' not in deck['cards']['42']
    ydk = profile_ydk(deck)
    assert ydk.count('#main') == 1 and ydk.count('\n42\n') >= 17
    assert len(ydk.split('!side\n')[1].strip().splitlines()) == 18
    assert profile_payload('玩家', None, {}, Catalog())['deck'] is None


def test_broken_chinese_image_falls_back_to_english():
    calls = []
    async def download(session, url):
        calls.append(url)
        if url.startswith(CHINESE): raise ValueError('占位图')
        return b'english-card'
    assert asyncio.run(fetch_card_image(42, {}, download=download)) == (b'english-card', 'en')
    assert calls == [CHINESE + '42.jpg', ENGLISH + '42.jpg']


def test_chinese_image_is_preferred_and_all_sources_missing_is_explicit():
    async def found(session, url): return b'card'
    async def missing(session, url): return None
    assert asyncio.run(fetch_card_image(42, {}, download=found)) == (b'card', 'zh')
    assert asyncio.run(fetch_card_image(42, {}, download=missing)) == (None, None)


def test_blank_and_wrong_aspect_images_rejected():
    out = io.BytesIO(); Image.new('RGB', (177, 254), 'white').save(out, 'PNG')
    with pytest.raises(ValueError): normalize_image(out.getvalue())
    out = io.BytesIO(); image = Image.effect_noise((300, 300), 60).convert('RGB'); image.save(out, 'PNG')
    with pytest.raises(ValueError): normalize_image(out.getvalue())
    out = io.BytesIO(); image = Image.effect_noise((177, 254), 60).convert('RGB'); image.save(out, 'PNG')
    assert normalize_image(out.getvalue()).startswith(b'\xff\xd8')


@pytest.fixture
def images(monkeypatch):
    from collections import OrderedDict
    from hikari_bot.services.mycard_archive import full_image
    for name in ('_cache', '_image_tasks', '_chinese_tasks', '_retry_tasks'):
        monkeypatch.setattr(full_image, name, OrderedDict())
    monkeypatch.setattr(full_image, '_limit', asyncio.Semaphore(6))
    return full_image


def test_refresh_rechecks_chinese_and_preserves_english_if_still_missing(images, monkeypatch):
    calls = []
    async def first(code, info): return b'english', 'en'
    async def retry(code, info):
        calls.append(code)
        return None if len(calls) == 1 else b'chinese'
    monkeypatch.setattr(images, 'fetch_card_image', first)
    monkeypatch.setattr(images, 'fetch_chinese_image', retry)
    monkeypatch.setattr(images, 'schedule_recovery', lambda *args: None)
    async def run():
        assert await images.full_card_image(42, {}, 'page-a') == (b'english', 'en')
        assert await images.full_card_image(42, {}, 'page-a') == (b'english', 'en')
        assert calls == []
        assert await images.full_card_image(42, {}, 'page-b') == (b'english', 'en')
        assert calls == [42]
        assert await images.full_card_image(42, {}, 'page-c') == (b'chinese', 'zh')
        assert await images.full_card_image(42, {}, 'page-d') == (b'chinese', 'zh')
        assert calls == [42, 42]
    asyncio.run(run())


def test_duplicate_images_share_one_refresh_request(images, monkeypatch):
    calls = []
    async def retry(code, info):
        calls.append(code)
        await asyncio.sleep(0)
        return b'chinese'
    monkeypatch.setattr(images, 'fetch_chinese_image', retry)
    async def run():
        images.remember(42, (b'english', 'en'), 'old-page')
        results = await asyncio.gather(*(images.full_card_image(42, {}, 'new-page') for _ in range(12)))
        assert results == [(b'chinese', 'zh')] * 12
        assert calls == [42]
    asyncio.run(run())


def test_background_keeps_retrying_and_promotes_chinese(images, monkeypatch):
    calls = []
    async def first(code, info): return b'english', 'en'
    async def retry(code, info):
        calls.append(code)
        return b'chinese' if len(calls) == 3 else None
    monkeypatch.setattr(images, 'fetch_card_image', first)
    monkeypatch.setattr(images, 'fetch_chinese_image', retry)
    monkeypatch.setattr(images, 'RETRY_DELAYS', (0, 0))
    monkeypatch.setattr(images, 'RETRY_INTERVAL', 0)
    async def run():
        assert await images.full_card_image(42, {}, 'page-a') == (b'english', 'en')
        await asyncio.wait_for(images._retry_tasks[42], 1)
        assert calls == [42, 42, 42]
        assert await images.full_card_image(42, {}, 'page-b') == (b'chinese', 'zh')
    asyncio.run(run())


def test_expired_fallback_is_not_retried_forever_without_visitors(images, monkeypatch):
    async def forbidden(*args): pytest.fail('过期的闲置图片不应继续请求图源')
    monkeypatch.setattr(images, 'fetch_chinese_image', forbidden)
    monkeypatch.setattr(images, 'RETRY_DELAYS', (0,))
    async def run():
        images._cache[42] = (0, (b'english', 'en'), 'old')
        await images.recover_chinese(42, {})
    asyncio.run(run())


def test_confirmed_canonical_chinese_precedes_english():
    calls = []
    async def download(session, url):
        calls.append(url)
        return b'chinese' if url == CHINESE + '24.jpg' else None
    assert asyncio.run(fetch_card_image(42, {'id': 24}, download=download)) == (b'chinese', 'zh')
    assert calls == [CHINESE + '42.jpg', CHINESE + '24.jpg']
