(function(){
  const input=__INPUT__, state=globalThis.__codexJhsFastProbe={status:'pending'};
  const config=Object.values(getApp().globalData.game_configs).find(c=>c.game_key==='ygo'&&c.game_sub_key==='ocg');
  if(!config||!config.category_tree_v2){state.status='error';return JSON.stringify({status:'started'});}
  wx.request({url:config.category_tree_v2,method:'GET',success(response){
    try{
      if(response.statusCode!==200||!Array.isArray(response.data))throw new Error('invalid directory');
      const found=new Map(), keyword=input.keyword.normalize('NFKC').toLowerCase();
      function visit(value){
        if(!value||typeof value!=='object')return;
        if(value.mini_app_hidden)return;
        const id=Number(value.packId);
        if(Number.isSafeInteger(id)&&id>0&&typeof value.packName==='string'){
          const p={id,name:value.packName,alias:value.packAlias||'',released_at:value.packReleaseAt||null};
          if((p.name+' '+p.alias).normalize('NFKC').toLowerCase().includes(keyword))found.set(id,p);
        }
        for(const child of Object.values(value))if(child&&typeof child==='object'){
          if(Array.isArray(child))child.forEach(visit);else visit(child);
        }
      }
      response.data.forEach(visit);
      Object.assign(state,{status:'done',products:Array.from(found.values())});
    }catch(_){state.status='error';}
  },fail(){state.status='error';}});
  return JSON.stringify({status:'started'});
})()
