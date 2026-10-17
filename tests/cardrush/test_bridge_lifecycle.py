"""直接调用已定位的 JS 运行对象，避免依赖微信混淆后的私有字段。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_lifecycle_uses_runtime_methods_and_propagates_failures():
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js 执行实际桥接探针')
    source = Path('scripts/jihuanshe_bridge/agent.js').read_text(encoding='utf-8')
    harness = r"""
const vm=require('vm'),assert=require('assert');
const calls=[];
const context={rpc:{exports:{}},Java:{perform:fn=>fn(),ClassFactory:{get(){
  throw new Error('obsolete private field b');
}}}};
vm.createContext(context);
vm.runInContext(SOURCE,context);
const setRuntime=value=>{context.testRuntime=value;vm.runInContext('runtime=testRuntime',context);};
(async()=>{
  // 方法可以继承自运行对象的父类；不依赖 a0/c0 或 hl/gm 这些混淆名。
  setRuntime(Object.create({getClass:()=>({getClassLoader:()=>null}),
    resume(){calls.push('resume');},pause(){calls.push('pause');}}));
  assert.strictEqual(await context.rpc.exports.lifecycle('resume'),true);
  assert.strictEqual(await context.rpc.exports.lifecycle('pause'),true);
  assert.deepStrictEqual(calls,['resume','pause']);
  await assert.rejects(context.rpc.exports.lifecycle('destroy'),/bad action/);
  assert.deepStrictEqual(calls,['resume','pause']);
  setRuntime(null);
  await assert.rejects(context.rpc.exports.lifecycle('resume'),/no context/);
  setRuntime({});
  await assert.rejects(context.rpc.exports.lifecycle('resume'),/lifecycle method unavailable/);
  setRuntime({resume(){throw new Error('runtime destroyed');}});
  await assert.rejects(context.rpc.exports.lifecycle('resume'),/runtime destroyed/);
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace('SOURCE', json.dumps(source))
    result = subprocess.run([node, '-e', harness], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
