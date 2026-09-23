"""会话检查不查价格，与前台查询互斥，并在离线后退避。"""
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=Path('scripts/jihuanshe_bridge'), Phone=object))
    spec = importlib.util.spec_from_file_location('bridge_maintenance_test', 'scripts/jihuanshe_bridge/worker.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    monkeypatch.setattr(result, 'rpc', lambda operation: operation())
    return result


@pytest.fixture
def worker(module):
    result = module.Worker.__new__(module.Worker)
    result.lock = threading.Lock()
    result.identity = {'pid': 123, 'name': 'com.tencent.mm:appbrand0', 'ticks': '456'}
    result.phone = Mock()
    result.phone.identity.return_value = result.identity
    result.script = Mock()
    result.script.exports_sync.query.return_value = {'status': 'done', 'ready': True}
    result.recover = Mock()
    result.pause_background = Mock()
    result.query = Mock()
    return result


def test_healthy_session_checks_only_local_js_and_pauses_again(module, worker):
    assert worker.check_session() is True
    worker.script.exports_sync.query.assert_called_once_with(module.SESSION_PROBE)
    worker.script.exports_sync.lifecycle.assert_called_once_with('resume')
    worker.phone.unfreeze.assert_called_once_with(123)
    worker.recover.assert_not_called()
    worker.query.assert_not_called()
    worker.pause_background.assert_called_once()
    assert not worker.lock.locked()


def test_running_query_skips_background_check(worker):
    with worker.lock:
        assert worker.check_session() is None
        worker.phone.connect.assert_not_called()
        worker.recover.assert_not_called()
        worker.pause_background.assert_not_called()


@pytest.mark.parametrize('failure', ['missing', 'reused_pid', 'logged_out', 'timeout', 'detached'])
def test_stale_context_recovers_once_then_checks_again(worker, failure):
    original_script = worker.script
    if failure == 'missing':
        worker.script = None
        worker.identity = None
    elif failure == 'reused_pid':
        worker.phone.identity.return_value = {**worker.identity, 'ticks': 'new-process'}
    elif failure == 'logged_out':
        worker.script.exports_sync.query.return_value = {'status': 'done', 'ready': False}
    elif failure == 'timeout':
        worker.script.exports_sync.query.return_value = {'status': 'timeout'}
    else:
        worker.script.exports_sync.query.side_effect = RuntimeError('detached')

    def recover():
        worker.script = original_script
        worker.identity = {'pid': 456}
        worker.script.exports_sync.query.side_effect = None
        worker.script.exports_sync.query.return_value = {'status': 'done', 'ready': True}
    worker.recover.side_effect = recover
    assert worker.check_session() is True
    worker.recover.assert_called_once()
    worker.pause_background.assert_called_once()
    worker.query.assert_not_called()
    assert not worker.lock.locked()


def test_offline_does_not_wake_phone_or_restart_context(worker, caplog):
    worker.phone.connect.side_effect = RuntimeError('private detail')
    assert worker.check_session() is False
    worker.recover.assert_not_called()
    worker.pause_background.assert_not_called()
    worker.script.exports_sync.query.assert_not_called()
    assert 'private detail' not in caplog.text
    assert not worker.lock.locked()


def test_failed_recovery_does_not_retry_in_same_cycle(worker):
    worker.script = None
    worker.recover.side_effect = RuntimeError('unavailable')
    assert worker.check_session() is False
    worker.recover.assert_called_once()
    assert not worker.lock.locked()


def test_maintenance_backs_off_caps_and_resets_after_success(module, worker):
    worker.check_session = Mock(side_effect=[False, False, False, False, True, None])
    waits = []
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return len(waits) == 7
    worker.maintain_session(Stop())
    assert waits == [300, 600, 1200, 1800, 1800, 300, 300]
    assert worker.check_session.call_count == 6


def test_stop_interrupts_wait_without_checking(worker):
    stop = threading.Event()
    stop.set()
    worker.check_session = Mock()
    worker.maintain_session(stop)
    worker.check_session.assert_not_called()


def test_probe_evaluates_without_network_or_exposing_token(module):
    harness = r'''
const vm=require('vm'),assert=require('assert');
const source=JSON.parse(process.argv[1]);
for(const loggedIn of [true,false]){
  const page={require(){return {cloudRequest(){throw Error('network must not be called');}};},
    getApp(){return {globalData:{jwt:loggedIn?'secret-never-return':''}};}};
  assert.equal(JSON.parse(vm.runInNewContext(source,page)).status,'started');
  assert.deepEqual(page.__codexJhsFastProbe,{status:'done',ready:loggedIn});
  assert(!JSON.stringify(page.__codexJhsFastProbe).includes('secret-never-return'));
}
const empty={};vm.runInNewContext(source,empty);
assert.equal(empty.__codexJhsFastProbe.ready,false);
'''
    result = subprocess.run(['node', '-e', harness, json.dumps(module.SESSION_PROBE)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_main_stops_maintenance_before_closing_phone(module, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2))
    monkeypatch.setenv('JHS_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('JHS_ACCESS_TOKEN', 'test-only-key-with-more-than-32-characters')
    monkeypatch.setattr(module.signal, 'signal', Mock())
    started, stopped = threading.Event(), threading.Event()
    service = Mock()
    def maintain(stop):
        started.set()
        assert stop.wait(2)
        stopped.set()
    service.maintain_session.side_effect = maintain
    service.close.side_effect = lambda: stopped.is_set() or pytest.fail('phone closed before maintenance stopped')
    server = Mock()
    server.serve_forever.side_effect = lambda: started.wait(2) or pytest.fail('maintenance not started')
    monkeypatch.setattr(module, 'Worker', lambda: service)
    monkeypatch.setattr(module, 'create_server', lambda *args: server)
    module.main()
    service.warmup.assert_called_once()
    service.close.assert_called_once()
    assert stopped.is_set()
