"""验证真实小程序查询模板的参数，防止 packId 被误改成被忽略的 pack_id。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_product_query_uses_series_filter_and_separate_card_count():
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js 执行实际 JavaScript 模板')
    source = Path('scripts/jihuanshe_bridge/product-search.js').read_text(encoding='utf-8')
    source = source.replace('__INPUT__', json.dumps({'pack_id': 4404, 'page': 1}))
    harness = r"""
const vm=require('vm'),assert=require('assert');
const context={require:()=>({cloudRequest:(params,name,url)=>{
  if(name==='getCardVersions'){
    assert.strictEqual(params.packId,4404);
    assert.strictEqual(params.pack_id,undefined);
    return Promise.resolve({result:{data:{current_page:1,last_page:1,total:3,data:[
      {card_version_id:1,card_id:2,card_object_type:'card',number:'无编号',rarity:'SER',name_cn:'卡片'},
      {card_version_id:3,card_id:4,card_object_type:'goods',number:'MC1-Z01',rarity:'卡册',name_cn:'卡册'}]}}});
  }
  assert.strictEqual(url,'/api/market/packs/4404');
  return Promise.resolve({result:{data:{id:4404,name_cn:'EX 特典卡',card_version_count:1,card_versions:[{id:1}]}}});
}})};
vm.runInNewContext(SOURCE,context);
setImmediate(()=>console.log(JSON.stringify(context.__codexJhsFastProbe)));
""".replace('SOURCE', json.dumps(source))
    result = subprocess.run([node, '-e', harness], capture_output=True, text=True, encoding='utf-8', check=True)
    data = json.loads(result.stdout)
    assert data['status'] == 'done'
    assert data['product']['version_count'] == 1
    assert data['entries'][0]['number'] == '无编号'
    assert data['entries'][0]['object_type'] == 'card'
    assert data['entries'][1]['object_type'] == 'goods'
    assert data['product']['sample_version_ids'] == [1]
