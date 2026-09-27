(function(){
  const input=__INPUT__,state=globalThis.__codexJhsFastProbe={status:'pending'};
  require('api/cloud.js').cloudRequest({card_id:input.card_id,game_key:'ygo',game_sub_key:'ocg'},'findCard','/api/market/cards/'+input.card_id).then(response=>{
    const c=response.result.data;
    if(!c||Number(c.id)!==input.card_id||c.type==='token'||!Array.isArray(c.card_versions)||!c.card_versions.length)throw new Error('invalid card');
    const aliases=(c.card_names||[]).map(n=>n.name_value).filter(n=>typeof n==='string'&&n);
    const seen=new Set();
    const versions=c.card_versions.map(v=>{
      const id=Number(v.id);
      if(!Number.isSafeInteger(id)||id<=0||seen.has(id)||typeof v.rarity!=='string'||!v.rarity.trim()||!Array.isArray(v.packs)||!v.packs.length)throw new Error('invalid version');
      const owner=v.ygo_card_id===undefined?v.card_id:v.ygo_card_id;
      if(owner!==undefined&&Number(owner)!==input.card_id)throw new Error('wrong card');
      seen.add(id);
      const packs=v.packs.map(p=>{
        const id=Number(p.id);
        if(!Number.isSafeInteger(id)||id<=0||typeof p.name_cn!=='string'||!p.name_cn)throw new Error('invalid pack');
        return {id,name:p.name_cn,name_origin:p.name_jp||'',released_at:p.released_at||null};
      });
      return {id,card_id:input.card_id,object_type:'card',name_jp:c.name_jp||'',name_cn:c.name_cn||'',aliases,number:v.number||'',rarity:v.rarity,packs};
    });
    Object.assign(state,{status:'done',complete:true,card_id:input.card_id,versions});
  }).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
