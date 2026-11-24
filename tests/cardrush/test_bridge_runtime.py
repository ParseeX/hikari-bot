"""微信升级后验证真实恢复入口；未知运行库仍不得进入业务探针。"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


OLD_RUNTIME = '9b9257b9d57c5ebed49b24d7802f6d15de8c2bc896d723f8c994aabad7b0b87c'
NEW_RUNTIME = 'a88cb7b86e05eb0b814b6d21b48a49bd05cdc3aa69e17a232d7323eb22a7f395'


@pytest.fixture
def recovery(monkeypatch):
    probe, agent, session, device = Mock(), Mock(), Mock(), Mock()
    probe.exports_sync.path.return_value = '/data/app/wechat/lib/arm64/libwxa-runtime-binding.so'
    agent.exports_sync.init.return_value = {'ready': True}
    session.create_script.side_effect = [probe, agent]
    device.attach.return_value = session
    manager = Mock()
    manager.add_remote_device.return_value = device
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(get_device_manager=lambda: manager))
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=Path('scripts/jihuanshe_bridge'), Phone=object))
    spec = importlib.util.spec_from_file_location('bridge_runtime_test', 'scripts/jihuanshe_bridge/worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'rpc', lambda fn: fn())
    worker = module.Worker.__new__(module.Worker)
    worker.last_failure = 0
    worker.drop = Mock()
    worker.save_identity = Mock()
    worker.java = ''
    worker.phone = Mock()
    worker.phone.locked.return_value = False
    worker.phone.open_miniapp.return_value = 123
    worker.phone.identity.return_value = {'pid': 123, 'ticks': '42'}
    return worker, probe, agent


@pytest.mark.parametrize('digest', [OLD_RUNTIME, NEW_RUNTIME])
def test_verified_runtime_reaches_business_readiness(recovery, digest):
    worker, probe, agent = recovery
    worker.phone.root.return_value = digest + '  runtime.so'
    worker.recover()
    agent.exports_sync.init.assert_called_once()
    assert worker.session_failure_reason is None


def test_unknown_runtime_stays_rejected_with_actionable_reason(recovery):
    worker, probe, agent = recovery
    worker.phone.root.return_value = '0' * 64 + '  runtime.so'
    with pytest.raises(RuntimeError, match='runtime version changed'):
        worker.recover()
    agent.exports_sync.init.assert_not_called()
    assert worker.session_failure_reason == 'runtime_unsupported'


def test_missing_runtime_is_distinguished_from_unsupported(recovery):
    worker, probe, agent = recovery
    probe.exports_sync.path.side_effect = RuntimeError('runtime not ready')
    with pytest.raises(RuntimeError):
        worker.recover()
    agent.exports_sync.init.assert_not_called()
    assert worker.session_failure_reason == 'runtime_unavailable'
