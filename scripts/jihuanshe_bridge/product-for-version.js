(function(){
  const input=__INPUT__, state=globalThis.__codexJhsFastProbe={status:'pending'};
  require('api/cloud.js').cloudRequest({card_version_id:input.version_id,game_key:'ygo',game_sub_key:'ocg'},'findCardVersion','/api/market/card-versions/'+input.version_id).then(response=>{
    const r=response.result.data, p=r&&r.pack;
    if(!r||Number(r.id)!==input.version_id||!p||!p.pack_id||!p.name_cn)throw new Error('invalid product');
    Object.assign(state,{status:'done',product:{id:Number(p.pack_id),name:p.name_cn,name_origin:p.name_origin||'',released_at:p.released_at||null}});
  }).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
