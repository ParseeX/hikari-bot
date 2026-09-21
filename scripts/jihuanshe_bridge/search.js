(function(){
  const input=__INPUT__, started=Date.now();
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  require('api/cloud.js').cloudRequest({keyword:input.name_jp,game_key:'ygo',game_sub_key:'ocg',page:input.page,sorting_price_type:'product'},'getCardVersions','/api/market/card-versions').then(response=>{
    const body=response.result.data, rows=Array.isArray(body)?body:body.data;
    if(!Array.isArray(rows))throw new Error('invalid versions');
    // name_origin 是中文别名，不能作为日文原名。
    const entries=rows.map(r=>({id:r.card_version_id||r.id,card_id:r.card_id,name_jp:r.name_jp||'',name_cn:r.name_cn||'',aliases:[r.name_origin,...(r.card_names||[]).map(n=>n.name_value)].filter(n=>typeof n==='string'&&n),number:r.number||'',rarity:r.rarity||''}));
    Object.assign(state,{status:'done',entries,last_page:Array.isArray(body)?1:body.last_page||1,elapsed_ms:Date.now()-started});
  }).catch(()=>{Object.assign(state,{status:'error'});});
  return JSON.stringify({status:'started'});
})()
