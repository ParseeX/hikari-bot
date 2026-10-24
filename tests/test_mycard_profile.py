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
def assets():
    from PIL import ImageDraw
    result = {}
    for key, color in [('zh', 'red'), ('en', 'blue')]:
        image = Image.new('RGB', (400, 580), color)
        ImageDraw.Draw(image).rectangle((40, 80, 350, 400), fill='white')
        out = io.BytesIO(); image.save(out, 'JPEG'); result[key] = out.getvalue()
    return result


@pytest.fixture
def images(monkeypatch, tmp_path):
    from collections import OrderedDict
    from hikari_bot.services.mycard_archive import full_image
    for name in ('_cache', '_disk_cache', '_image_tasks', '_chinese_tasks', '_retry_tasks'):
        monkeypatch.setattr(full_image, name, OrderedDict())
    monkeypatch.setattr(full_image, 'IMAGE_DIR', tmp_path / 'pics')
    monkeypatch.setattr(full_image, '_limit', asyncio.Semaphore(6))
    async def missing(*args): return None
    monkeypatch.setattr(full_image, 'fetch_chinese_image', missing)
    return full_image


def test_chinese_survives_memory_eviction_restart_and_elapsed_years(images, assets, monkeypatch):
    images.persist_image(42, (assets['zh'], 'zh'), {})
    async def forbidden(*args, **kwargs): pytest.fail('已落盘中文图不应再下载')
    monkeypatch.setattr(images, 'fetch_card_image', forbidden)
    monkeypatch.setattr(images, 'fetch_chinese_image', forbidden)
    monkeypatch.setattr(images.time, 'time', lambda: 9999999999)
    async def run():
        images._cache.clear(); images._disk_cache.clear()
        assert await images.full_card_image(42, {}, 'refresh') == (assets['zh'], 'zh')
        assert await images.check_chinese_image(42, {}) == assets['zh']
        await images.start_image_recovery()
        assert not images._retry_tasks
    asyncio.run(run())


def test_existing_thumbnail_needs_no_full_image_or_upstream_request(images, assets, monkeypatch):
    small = images.resize_thumbnail(assets['zh'])
    images.write_card_image(images.IMAGE_DIR / '42.jpg', small)
    async def forbidden(*args, **kwargs): pytest.fail('列表不应下载大图')
    monkeypatch.setattr(images, 'fetch_card_image', forbidden)
    result = asyncio.run(images.full_card_image(42, {}, size='thumb'))
    assert result == (small, 'zh')
    assert not (images.IMAGE_DIR / 'full' / '42.jpg').exists()
    with Image.open(io.BytesIO(small)) as image: assert image.width <= 200 and image.height <= 290


def test_english_three_failures_then_daily_and_restart_keeps_deadline(images, assets, monkeypatch):
    now = [1000.0]; calls = []
    monkeypatch.setattr(images.time, 'time', lambda: now[0])
    monkeypatch.setattr(images, 'schedule_recovery', lambda *args: None)
    async def missing(*args): calls.append(now[0]); return None
    monkeypatch.setattr(images, 'fetch_chinese_image', missing)
    images.persist_image(42, (assets['en'], 'en'), {})
    async def run():
        assert images.next_chinese_check(42) == 1005
        for _ in range(3):
            assert await images.full_card_image(42, {}, 'new-page') == (assets['en'], 'en')
            assert await images.check_chinese_image(42, {}) is None
        assert calls == []
        now[0] = 1005; await images.check_chinese_image(42, {})
        assert images.next_chinese_check(42) == 1035
        now[0] = 1035; await images.check_chinese_image(42, {})
        deadline = 1035 + 86400
        assert images.next_chinese_check(42) == deadline
        images._cache.clear(); images._disk_cache.clear()
        now[0] = deadline - 1
        await images.check_chinese_image(42, {})
        assert calls == [1005, 1035]
        assert await images.full_card_image(42, {}, 'after-restart') == (assets['en'], 'en')
        now[0] = deadline; await images.check_chinese_image(42, {})
        assert calls == [1005, 1035, deadline]
        assert images.next_chinese_check(42) == deadline + 86400
        assert (images.IMAGE_DIR / 'full/fallback/42.jpg').read_bytes() == assets['en']
    asyncio.run(run())


def test_recovery_updates_full_and_thumbnail_and_stops_retrying(images, assets, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(images.time, 'time', lambda: now[0])
    images.persist_image(42, (assets['en'], 'en'), {})
    async def chinese(*args): return assets['zh']
    monkeypatch.setattr(images, 'fetch_chinese_image', chinese)
    async def run():
        now[0] = 1005
        image = await images.check_chinese_image(42, {}, size='thumb')
        assert image == images.resize_thumbnail(assets['zh'])
        assert await images.full_card_image(42, {}) == (assets['zh'], 'zh')
        assert images.next_chinese_check(42) is None
        # 旧英文图保留，不做不可逆清理；中文始终优先。
        assert (images.IMAGE_DIR / 'full/fallback/42.jpg').exists()
    asyncio.run(run())


def test_duplicate_images_and_sizes_share_one_initial_download(images, assets, monkeypatch):
    calls = []
    async def download(*args, **kwargs):
        calls.append(42); await asyncio.sleep(0); return assets['zh'], 'zh'
    monkeypatch.setattr(images, 'fetch_card_image', download)
    async def run():
        results = await asyncio.gather(*(images.full_card_image(42, {}, size='thumb' if i%2 else 'full') for i in range(12)))
        assert all(language == 'zh' and data for data, language in results)
        assert calls == [42]
    asyncio.run(run())


def test_background_restores_pending_checks_after_restart(images, assets, monkeypatch):
    images.persist_image(42, (assets['en'], 'en'), {})
    state = images.read_state(42); state['next_check'] = 0; images.write_state(42, state)
    async def chinese(*args): return assets['zh']
    monkeypatch.setattr(images, 'fetch_chinese_image', chinese)
    async def run():
        await images.start_image_recovery()
        await asyncio.wait_for(images._retry_tasks[42], 1)
        assert images.load_disk_image(42) == (assets['zh'], 'zh')
        await images.stop_image_recovery()
        assert not images._retry_tasks
    asyncio.run(run())


def test_pending_daily_check_does_not_block_serving_english(images, assets, monkeypatch):
    images.persist_image(42, (assets['en'], 'en'), {})
    state = images.read_state(42); state['next_check'] += 86400; images.write_state(42, state)
    async def forbidden(*args, **kwargs): pytest.fail('每日检查时间未到')
    monkeypatch.setattr(images, 'fetch_chinese_image', forbidden)
    async def run():
        await images.start_image_recovery()
        assert await images.full_card_image(42, {}) == (assets['en'], 'en')
        await images.stop_image_recovery()
    asyncio.run(run())


def test_confirmed_canonical_chinese_precedes_english():
    calls = []
    async def download(session, url):
        calls.append(url)
        return b'chinese' if url == CHINESE + '24.jpg' else None
    assert asyncio.run(fetch_card_image(42, {'id': 24}, download=download)) == (b'chinese', 'zh')
    assert calls == [CHINESE + '42.jpg', CHINESE + '24.jpg']


def test_check_only_chinese_does_not_download_english_when_server_cache_is_empty(images, monkeypatch):
    calls = []
    async def chinese(code, info): calls.append(code); return None
    async def forbidden(*args, **kwargs): pytest.fail('检查中文不能重新下载英文图片')
    monkeypatch.setattr(images, 'fetch_chinese_image', chinese)
    monkeypatch.setattr(images, 'fetch_card_image', forbidden)
    monkeypatch.setattr(images, 'schedule_recovery', lambda *args: None)
    assert asyncio.run(images.check_chinese_image(42, {})) is None
    assert calls == [42]


def test_corrupt_disk_image_is_repaired_without_using_it_as_cache(images, assets, monkeypatch):
    images.write_card_image(images.IMAGE_DIR / 'full/42.jpg', b'broken')
    async def download(*args, **kwargs): return assets['zh'], 'zh'
    monkeypatch.setattr(images, 'fetch_card_image', download)
    assert asyncio.run(images.full_card_image(42, {})) == (assets['zh'], 'zh')
    assert images.load_disk_image(42) == (assets['zh'], 'zh')
