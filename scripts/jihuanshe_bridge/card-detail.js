(function(){
  const input=__INPUT__,state=globalThis.__codexJhsFastProbe={status:'pending'};
  require('api/cloud.js').cloudRequest({card_id:input.card_id,game_key:'ygo',game_sub_key:'ocg'},'findCard','/api/market/cards/'+input.card_id).then(response=>{
    const r=response.result.data;
    if(!r||Number(r.id)!==input.card_id)throw new Error('invalid card');
    // 只返回身份匹配需要的公开字段，不传递会话、HTML 或商家信息。
    const card={id:Number(r.id),name_cn:r.name_cn||'',name_jp:r.name_jp||'',type:r.type||''};
    if(!['name_cn','name_jp','type'].every(k=>typeof card[k]==='string'))throw new Error('invalid metadata');
    for(const key of ['desc','pendulum_desc']){
      if(r[key]!==undefined){
        if(r[key]!==null&&typeof r[key]!=='string')throw new Error('invalid text');
        card[key]=r[key]||'';
      }
    }
    Object.assign(state,{status:'done',card});
  }).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
