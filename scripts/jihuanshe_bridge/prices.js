(function(){
  const input=__INPUT__, request=require('api/cloud.js').cloudRequest;
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  Promise.all(input.version_ids.map(id=>{
    const params={card_version_id:id,game_key:'ygo',game_sub_key:'ocg',token:getApp().globalData.jwt};
    return Promise.all([
      request(params,'findCardVersion','/api/market/card-versions/'+id),
      request(Object.assign({},params,{filter:'1_month'}),'getPriceHistory','/api/market/products/price-history')
    ]).then(results=>{
      const detail=results[0].result.data, history=results[1].result.data;
      if(Number(detail.id)!==id||!Array.isArray(history))throw new Error('invalid price');
      const points=history.filter(p=>p&&typeof p.date==='string').sort((a,b)=>a.date.localeCompare(b.date));
      const latest=points.length?points[points.length-1]:null;
      return {id,min_price:detail.min_price??null,market_price:latest?latest.price:null,market_date:latest?latest.date:null};
    }).catch(()=>({id,error:true}));
  })).then(prices=>{Object.assign(state,{status:'done',prices});}).catch(()=>{Object.assign(state,{status:'error'});});
  return JSON.stringify({status:'started'});
})()
