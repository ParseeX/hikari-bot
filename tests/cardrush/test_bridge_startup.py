import importlib.util
import json
import subprocess
import sys
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_agent_waits_for_actual_business_context(tmp_path):
    # 执行真实 agent.js，模拟页面先出现、业务模块稍后才加载的情况。
    harness = tmp_path / 'readiness.cjs'
    harness.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let ready=false, evaluations=0;
const page={require(){if(!ready)throw Error('loading');return {cloudRequest(){}};},getApp(){return {globalData:{jwt:'test-only'}};}};
const runtime={evaluateJavascript(code,cb){evaluations++;const v=vm.runInNewContext(code,page);if(cb)cb.onReceiveValue(v);}};
global.rpc={exports:{}};global.Process={id:123};
const factory={use(){return {class:{hashCode(){return 1;}}};},choose(name,cbs){cbs.onMatch(runtime);cbs.onComplete();}};
global.Java={perform(fn){fn();},retain(x){return x;},use(){return {};},enumerateClassLoadersSync(){return [{}];},ClassFactory:{get(){return factory;}},registerClass(spec){return {$new(){return {jobId:{value:0},kind:{value:0},onReceiveValue:spec.methods.onReceiveValue};}};}};
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
setTimeout(()=>{ready=true;},100);
const started=Date.now();
rpc.exports.init().then(result=>{
 assert.equal(result.ready,true);assert(evaluations>=2);assert(Date.now()-started<3000);
 console.log(JSON.stringify({ready:true,evaluations}));process.exit(0);
}).catch(()=>{console.error('business readiness was not detected');process.exit(1);});
''', encoding='utf-8')
    result = subprocess.run(['node', str(harness), str(Path('scripts/jihuanshe_bridge/agent.js').resolve())],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ready']


def test_startup_prepares_once_and_does_not_keep_retrying_offline(monkeypatch):
    base = Path('scripts/jihuanshe_bridge')
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=base, Phone=object))
    spec = importlib.util.spec_from_file_location('bridge_startup_test', base / 'worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = module.Worker.__new__(module.Worker)
    worker.recover = Mock()
    worker.pause_background = Mock()
    worker.warmup()
    worker.recover.assert_called_once()
    worker.pause_background.assert_called_once()
    worker.recover.reset_mock()
    worker.recover.side_effect = RuntimeError('offline')
    worker.warmup()
    worker.recover.assert_called_once()


def test_recovery_reports_stage_without_private_exception(monkeypatch, caplog):
    base = Path('scripts/jihuanshe_bridge')
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(get_device_manager=Mock()))
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=base, Phone=object))
    spec = importlib.util.spec_from_file_location('bridge_recovery_test', base / 'worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = module.Worker.__new__(module.Worker)
    worker.last_failure = 0
    worker.drop = Mock()
    worker.phone = Mock()
    worker.phone.locked.return_value = False
    worker.phone.open_miniapp.side_effect = RuntimeError('private phone data must not be logged')
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        worker.recover()
    assert 'stage=open_miniapp' in caplog.text
    assert 'RuntimeError' in caplog.text
    assert 'private phone data' not in caplog.text
    worker.phone.adb.assert_called_with('shell', 'input', 'keyevent', '3', check=False)
