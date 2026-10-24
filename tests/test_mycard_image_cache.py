import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from PIL import Image, ImageDraw

from hikari_bot.services.mycard_archive.image_cache import CardImageCache
from hikari_bot.services.mycard_archive.library import DeckLibrary


def library(tmp_path):
    path = tmp_path/'archive.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE deck_observations (main TEXT,extra TEXT,side TEXT,updated_at REAL)')
    return DeckLibrary(path)


def record(archive, main, extra=(), side=(), updated=1):
    with sqlite3.connect(archive.path) as db:
        db.execute('INSERT INTO deck_observations VALUES(?,?,?,?)',
                   (json.dumps(main), json.dumps(extra), json.dumps(side), updated))


def save(directory, code):
    directory.mkdir(exist_ok=True)
    image = Image.new('RGB', (20,20), 'red')
    ImageDraw.Draw(image).rectangle((2, 2, 10, 10), fill='blue')
    image.save(directory/f'{code}.jpg')


def test_inventory_covers_history_sections_and_prioritizes_recent_cards(tmp_path):
    archive = library(tmp_path)
    record(archive, [1,1], [2], [3])
    record(archive, [4], [2], [5], updated=2)
    assert archive.card_ids() == [4,2,5,1,3]


def test_inventory_rejects_blank_cache_and_rechecks_aged_backup(tmp_path):
    archive = library(tmp_path)
    record(archive, [1, 2, 3])
    directory = tmp_path/'pics'
    directory.mkdir()
    Image.new('RGB', (200, 290), 'white').save(directory/'1.jpg')
    save(directory/'fallback', 2)
    save(directory/'fallback', 3)
    old = time.time() - 90000
    os.utime(directory/'fallback'/'3.jpg', (old, old))
    cache = CardImageCache(archive, directory=directory)
    assert cache.inventory() == ([1, 2, 3], [1, 3])


def test_prefetch_only_missing_deduplicates_and_enables_offline_render(tmp_path, monkeypatch):
    from hikari_bot.services import ygocard, ygodeck

    archive = library(tmp_path)
    record(archive, [1,2,2,3], [4], [5])
    directory = tmp_path/'pics'
    save(directory, 1)
    (directory/'3.jpg').write_bytes(b'broken image')
    calls = []
    active = peak = 0
    first_pair_ready = asyncio.Event()

    async def download(code):
        nonlocal active, peak
        calls.append(code)
        active += 1
        peak = max(active, peak)
        if len(calls) == 2:
            first_pair_ready.set()
        await first_pair_ready.wait()
        save(directory, code)
        active -= 1

    cache = CardImageCache(archive, directory=directory, download=download, download_interval=0)
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(directory))

    def no_network(*args, **kwargs):
        raise AssertionError('预缓存后生成卡组不应访问网络')

    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', no_network)

    async def run():
        await cache.scan_once()
        await cache.scan_once()
        assert cache.queue.qsize() == 4
        cache.start()
        try:
            await asyncio.wait_for(cache.queue.join(), 3)
            assert await cache.scan_once() == (5,5,0)
            images = await ygodeck.batch_get_images(['1','2','2','3','4','5'], preserve_positions=True)
            assert all(img is not None for img in images)
            for img in images:
                img.close()
            record(archive, [6], updated=3)
            await cache.scan_once()
            await asyncio.wait_for(cache.queue.join(), 3)
            assert cache.cached(6)
        finally:
            await cache.close()

    asyncio.run(run())
    assert sorted(calls) == [2,3,4,5,6]
    assert peak == 2
    assert not cache.tasks and not cache.pending


def test_failure_retries_later_and_restart_rebuilds_missing_queue(tmp_path):
    archive = library(tmp_path)
    record(archive, [1,2])
    directory = tmp_path/'pics'
    now = [0]
    calls = []

    async def download(code):
        calls.append(code)
        if code == 1 and now[0] < 300:
            return b'fallback-not-persisted'
        save(directory, code)

    cache = CardImageCache(archive, directory=directory, download=download,
                           download_interval=0, clock=lambda: now[0])

    async def run():
        await cache.scan_once()
        cache.start()
        try:
            await asyncio.wait_for(cache.queue.join(), 3)
            assert cache.cached(2) and not cache.cached(1)
            await cache.scan_once()
            assert cache.queue.empty()
            assert cache.retries[1] == (1,300)
            now[0] = 300
            await cache.scan_once()
            await asyncio.wait_for(cache.queue.join(), 3)
            assert cache.cached(1) and not cache.retries
        finally:
            await cache.close()
        (directory/'2.jpg').unlink()
        restarted = CardImageCache(archive, directory=directory, download=download)
        assert await restarted.scan_once() == (2,1,1)
        assert restarted.queue.get_nowait() == 2

    asyncio.run(run())
    assert calls.count(1) == 2 and calls.count(2) == 1


def test_download_queued_card_is_skipped_if_query_already_cached_it(tmp_path):
    archive = library(tmp_path)
    record(archive, [1])
    directory = tmp_path/'pics'
    calls = []

    async def download(code):
        calls.append(code)

    cache = CardImageCache(archive, directory=directory, download=download)

    async def run():
        await cache.scan_once()
        save(directory, 1)
        cache.start()
        try:
            await asyncio.wait_for(cache.queue.join(), 3)
        finally:
            await cache.close()

    asyncio.run(run())
    assert calls == []


def test_aged_valid_fallback_finishes_refresh_without_retry_loop(tmp_path):
    archive = library(tmp_path)
    record(archive, [1])
    directory = tmp_path/'pics'
    directory.mkdir()
    save(directory/'fallback', 1)
    path = directory/'fallback'/'1.jpg'
    original = path.read_bytes()
    old = time.time() - 90000
    os.utime(path, (old, old))
    calls = []

    async def download(code):
        # 中文源仍缺图，实际下载入口返回磁盘中的有效备用图。
        calls.append(code)
        return original

    cache = CardImageCache(archive, directory=directory, download=download, download_interval=0)

    async def run():
        await cache.scan_once()
        cache.start()
        try:
            await asyncio.wait_for(cache.queue.join(), 3)
            assert not cache.retries
            assert cache.cached(1)
            assert await cache.scan_once() == (1, 1, 0)
            assert path.read_bytes() == original
        finally:
            await cache.close()

    asyncio.run(run())
    assert calls == [1]


def test_shutdown_cancels_downloads_without_marking_success(tmp_path):
    archive = library(tmp_path)
    record(archive, [1,2,3])

    async def run():
        started = asyncio.Event()
        async def download(code):
            started.set()
            await asyncio.Event().wait()
        cache = CardImageCache(archive, directory=tmp_path/'pics', download=download)
        cache.start()
        await asyncio.wait_for(started.wait(), 3)
        tasks = list(cache.tasks)
        cache.start()
        assert cache.tasks == tasks
        await cache.close()
        assert all(task.done() for task in tasks)
        assert not cache.pending and not cache.downloaded and cache.queue.empty()
        await asyncio.wait_for(cache.queue.join(), 1)

    asyncio.run(run())


def test_plugin_registers_background_lifecycle_without_waiting_for_downloads(monkeypatch):
    import nonebot
    hooks = {}
    monkeypatch.setattr(nonebot, 'get_driver', lambda: SimpleNamespace(
        on_startup=lambda fn: hooks.setdefault('start', fn),
        on_shutdown=lambda fn: hooks.setdefault('stop', fn)))
    spec = importlib.util.spec_from_file_location('image_cache_plugin_test',
        Path('hikari_bot/plugins/mycard_image_cache.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    async def close():
        calls.append('stop')
    module.cache = SimpleNamespace(start=lambda: calls.append('start'), close=close)
    async def recovery_start(): calls.append('recovery-start')
    async def recovery_stop(): calls.append('recovery-stop')
    module.start_image_recovery = recovery_start
    module.stop_image_recovery = recovery_stop
    asyncio.run(hooks['start']())
    asyncio.run(hooks['stop']())
    assert calls == ['start','recovery-start','stop','recovery-stop']
