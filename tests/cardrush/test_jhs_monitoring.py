import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hikari_bot.features.card_prices.monitoring import HealthNotifications


def healthy(name='8.0.79', code='3182', checked_at=1000):
    return dict(ok=True, checked_at=checked_at, price_checked_at=checked_at,
                version=dict(name=name, code=code, updated='2026-10-16 20:35:29'))


def test_notifications_update_outage_recovery_and_restart(tmp_path):
    async def run():
        path = tmp_path / 'state.json'
        monitor = HealthNotifications(path)
        send = AsyncMock()
        await monitor.observe(healthy(), send, now=1000)
        assert '监测已启用' in send.call_args.args[0]
        monitor = HealthNotifications(path)
        await monitor.observe(healthy(), send, now=1001)
        assert send.call_count == 1
        await monitor.observe(healthy('8.0.80', '3200'), send, now=1002)
        assert '8.0.79 → 8.0.80' in send.call_args.args[0]
        await monitor.observe(None, send, now=1003)
        assert send.call_count == 2
        await monitor.observe(None, send, now=1004)
        assert '无法连接' in send.call_args.args[0]
        monitor = HealthNotifications(path)
        for _ in range(3):
            await monitor.observe(None, send, now=1005)
        assert send.call_count == 3
        await monitor.observe(healthy('8.0.80', '3200'), send, now=1006)
        assert '已恢复' in send.call_args.args[0]
        await monitor.observe(healthy('8.0.80', '3200'), send, now=1007)
        assert send.call_count == 4
    asyncio.run(run())


def test_failed_delivery_retries_and_same_version_reinstall_alerts(tmp_path):
    async def run():
        monitor = HealthNotifications(tmp_path / 'state.json')
        send = AsyncMock(side_effect=RuntimeError('QQ offline'))
        with pytest.raises(RuntimeError):
            await monitor.observe(healthy(), send, now=1000)
        assert not monitor.state
        send.side_effect = None
        await monitor.observe(healthy(), send, now=1001)
        version = healthy()
        version['version']['updated'] = '2026-10-17 20:35:29'
        await monitor.observe(version, send, now=1002)
        assert '发生更新' in send.call_args.args[0]
        await monitor.observe(version, send, now=1003)
        assert send.call_count == 3
    asyncio.run(run())


@pytest.mark.parametrize('data,text', [
    ({**healthy(), 'ok': False, 'reason': 'price'}, '实际查价失败'),
    ({**healthy(), 'ok': False, 'reason': 'session'}, '手机连接'),
    (healthy(checked_at=1), '长时间没有完成'),
])
def test_unhealthy_or_stale_snapshot_is_never_recovery(tmp_path, data, text):
    async def run():
        monitor = HealthNotifications(tmp_path / 'state.json')
        send = AsyncMock()
        await monitor.observe(data, send, now=3000)
        await monitor.observe(data, send, now=3001)
        assert send.call_count == 1
        assert text in send.call_args.args[0]
        assert '已恢复' not in send.call_args.args[0]
    asyncio.run(run())


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=Path('scripts/jihuanshe_bridge'), Phone=object))
    spec = importlib.util.spec_from_file_location('health_worker', 'scripts/jihuanshe_bridge/worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    instance = module.Worker.__new__(module.Worker)
    instance.lock = threading.Lock()
    instance.health = dict(ok=None, checked_at=0, version=None, price_checked_at=0)
    instance.health_version_id = 119467
    instance.phone = Mock()
    instance.phone.wechat_version.return_value = healthy()['version']
    instance.prices = Mock(return_value={'prices': [dict(id=119467, min_price=None, market_price=None)]})
    instance.pause_background = Mock()
    return instance, module


def test_canary_only_on_initial_hourly_or_version_change(worker, monkeypatch):
    instance, module = worker
    instance.update_health(True)
    assert instance.health['ok'] is True
    instance.update_health(True)
    assert instance.prices.call_count == 1
    instance.phone.wechat_version.return_value = healthy('8.0.80')['version']
    instance.update_health(True)
    assert instance.prices.call_count == 2
    monkeypatch.setattr(module.time, 'time', lambda: 4601)
    instance.update_health(True)
    assert instance.prices.call_count == 3
    instance.update_health(False)
    assert not instance.health['ok']
    assert instance.health['reason'] == 'session'
    assert instance.prices.call_count == 3


def test_price_failure_is_visible_and_retried_without_version_change(worker):
    instance, _ = worker
    instance.prices.return_value = {'prices': [dict(id=119467, error=True)]}
    instance.update_health(True)
    assert not instance.health['ok']
    assert instance.health['reason'] == 'price'
    instance.prices.return_value = {'prices': [dict(id=119467, min_price=1, market_price=2)]}
    instance.update_health(True)
    assert instance.health['ok']


def test_busy_checks_skip_and_http_snapshot_does_not_touch_phone(worker):
    instance, module = worker
    import httpx
    with instance.lock:
        instance.update_health(True)
    instance.update_health(None)
    instance.phone.wechat_version.assert_not_called()
    server = module.create_server(instance, 'test-key', 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            assert client.get('/v1/health').status_code == 401
            with instance.lock:
                response = client.get('/v1/health', headers={'Authorization': 'Bearer test-key'})
            assert response.json() == instance.health
            instance.phone.wechat_version.assert_not_called()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_phone_version_extracts_no_other_package_data(monkeypatch):
    spec = importlib.util.spec_from_file_location('health_phone', 'scripts/jihuanshe_bridge/phone.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    phone = module.Phone.__new__(module.Phone)
    phone.adb = Mock(return_value='versionCode=3182 minSdk=24\nversionName=8.0.79\n'
                    'lastUpdateTime=2026-10-16 20:35:29\nprivate data must not escape')
    assert phone.wechat_version() == healthy()['version']
    phone.adb.return_value = 'invalid package'
    with pytest.raises(RuntimeError):
        phone.wechat_version()


def test_plugin_reads_authenticated_snapshot_and_uses_private_qq(monkeypatch, tmp_path):
    import time
    bot = SimpleNamespace(send_private_msg=AsyncMock())
    monkeypatch.setitem(sys.modules, 'nonebot', SimpleNamespace(
        get_bot=lambda: bot, get_driver=lambda: SimpleNamespace(on_bot_connect=lambda fn: fn),
        require=lambda name: None))
    monkeypatch.setitem(sys.modules, 'nonebot_plugin_apscheduler', SimpleNamespace(
        scheduler=SimpleNamespace(scheduled_job=lambda *a, **kw: lambda fn: fn)))
    monkeypatch.setitem(sys.modules, 'hikari_bot.core.config', SimpleNamespace(settings=SimpleNamespace(
        jihuanshe_bridge_token='test-token', jihuanshe_bridge_url='http://127.0.0.1:8791',
        superusers={'12345'})))
    monkeypatch.setitem(sys.modules, 'hikari_bot.core.constants', SimpleNamespace(DATA_DIR=str(tmp_path)))
    spec = importlib.util.spec_from_file_location('health_plugin', 'hikari_bot/plugins/jihuanshe_monitor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = Mock()
    response.json.return_value = healthy(checked_at=time.time())
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=context))
    async def run():
        await module.scheduled_check()
        await module.connected(bot)
        assert bot.send_private_msg.call_count == 1
        assert bot.send_private_msg.call_args.kwargs['user_id'] == 12345
        assert client.get.call_args.kwargs['headers'] == {'Authorization': 'Bearer test-token'}
        client.get.side_effect = module.httpx.ConnectError('unavailable')
        await module.scheduled_check()
        await module.scheduled_check()
        assert bot.send_private_msg.call_count == 2
        assert '无法连接' in bot.send_private_msg.call_args.kwargs['message']
    asyncio.run(run())
