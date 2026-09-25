(function(){
  const input=__INPUT__, started=Date.now();
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  const request=require('api/cloud.js').cloudRequest;
  // 系列页实际使用驼峰 packId；pack_id 在搜卡接口中会被忽略。
  const tasks=[request({packId:input.pack_id,game_key:'ygo',game_sub_key:'ocg',page:input.page,sorting_price_type:'product'},'getCardVersions','/api/market/card-versions')];
  if(input.page===1)tasks.push(request({pack_id:input.pack_id,game_key:'ygo',game_sub_key:'ocg'},'findPack','/api/market/packs/'+input.pack_id));
  Promise.all(tasks).then(responses=>{
    const body=responses[0].result.data;
    if(!body||!Array.isArray(body.data)||!Number.isInteger(body.last_page)||!Number.isInteger(body.total))throw new Error('invalid page');
    const entries=body.data.map(r=>({id:r.card_version_id||r.id,card_id:r.card_id,name_jp:r.name_jp||'',name_cn:r.name_cn||'',aliases:[r.name_origin,...(r.card_names||[]).map(n=>n.name_value)].filter(n=>typeof n==='string'&&n),number:r.number||'',rarity:r.rarity||''}));
    let product=null;
    if(input.page===1){
      const p=responses[1].result.data;
      if(!p||Number(p.pack_id||p.id)!==input.pack_id||!p.name_cn)throw new Error('invalid product');
      product={id:input.pack_id,name:p.name_cn,name_origin:p.name_origin||'',released_at:p.released_at||null,version_count:p.card_version_count};
    }
    Object.assign(state,{status:'done',entries,product,current_page:body.current_page,last_page:body.last_page,total:body.total,elapsed_ms:Date.now()-started});
  }).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
