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
