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


@pytest.mark.parametrize('source_id,status', [(71, 'done'), (72, 'error')])
def test_card_detail_template_checks_id_and_returns_only_public_identity(source_id, status):
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js 执行实际 JavaScript 模板')
    source = Path('scripts/jihuanshe_bridge/card-detail.js').read_text(encoding='utf-8')
    source = source.replace('__INPUT__', json.dumps({'card_id': 71}))
    harness = r"""
const vm=require('vm'),assert=require('assert');
const context={require:()=>({cloudRequest:(params,name,url)=>{
  assert.strictEqual(params.card_id,71);
  assert.strictEqual(params.game_key,'ygo');
  assert.strictEqual(params.game_sub_key,'ocg');
  assert.strictEqual(name,'findCard');
  assert.strictEqual(url,'/api/market/cards/71');
  return Promise.resolve({result:{data:{id:SOURCE_ID,name_cn:'迅捷飞鼠',
    name_jp:'素早いモモンガ',type:'效果怪兽',effect_by_html:'ignored',extra:'ignored'}}});
}})};
vm.runInNewContext(SOURCE,context);
setImmediate(()=>console.log(JSON.stringify(context.__codexJhsFastProbe)));
""".replace('SOURCE_ID', str(source_id)).replace('SOURCE', json.dumps(source))
    result = subprocess.run([node, '-e', harness], capture_output=True, text=True, encoding='utf-8', check=True)
    data = json.loads(result.stdout)
    assert data['status'] == status
    if status == 'done':
        assert set(data['card']) == {'id', 'name_cn', 'name_jp', 'type'}


@pytest.mark.parametrize('failure', ['', 'duplicate', 'wrong_owner', 'no_pack', 'token'])
def test_complete_card_versions_template_requires_full_valid_identity(failure):
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js 执行实际 JavaScript 模板')
    source = Path('scripts/jihuanshe_bridge/card-versions.js').read_text(encoding='utf-8')
    source = source.replace('__INPUT__', json.dumps({'card_id': 71}))
    record = {'id': 71, 'name_cn': '迅捷飞鼠', 'name_jp': '素早いモモンガ', 'type': '效果怪兽',
              'card_versions': [{'id': 709, 'ygo_card_id': 71, 'number': 'GS05-JP002', 'rarity': 'GR',
                                 'min_price': '999', 'packs': [{'id': 10, 'name_cn': '商品一'}, {'id': 20, 'name_cn': '商品二'}]}]}
    if failure == 'duplicate':
        record['card_versions'] *= 2
    elif failure == 'wrong_owner':
        record['card_versions'][0]['ygo_card_id'] = 72
    elif failure == 'no_pack':
        record['card_versions'][0]['packs'] = []
    elif failure == 'token':
        record['type'] = 'token'
    harness = r"""
const vm=require('vm'),assert=require('assert');
const context={require:()=>({cloudRequest:(params,name,url)=>{
  assert.strictEqual(params.card_id,71);
  assert.strictEqual(params.game_key,'ygo');
  assert.strictEqual(params.game_sub_key,'ocg');
  assert.strictEqual(name,'findCard');
  return Promise.resolve({result:{data:RECORD}});
}})};
vm.runInNewContext(SOURCE,context);
setImmediate(()=>console.log(JSON.stringify(context.__codexJhsFastProbe)));
""".replace('RECORD', json.dumps(record)).replace('SOURCE', json.dumps(source))
    result = subprocess.run([node, '-e', harness], capture_output=True, text=True, encoding='utf-8', check=True)
    data = json.loads(result.stdout)
    assert data['status'] == ('error' if failure else 'done')
    if not failure:
        assert data['complete'] is True and data['card_id'] == 71
        assert [p['id'] for p in data['versions'][0]['packs']] == [10, 20]
        assert 'min_price' not in data['versions'][0]
