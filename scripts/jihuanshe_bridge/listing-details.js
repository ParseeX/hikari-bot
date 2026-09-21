(function(){
  const input=__INPUT__,request=require('api/cloud.js').cloudRequest;
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  function product(row){
    const out={};
    for(const key of ['id','product_id','card_version_id','price','quantity','remark','remark_only','condition','pull_off']){
      if(row[key]!==undefined)out[key]=row[key];
    }
    return out;
  }
  Promise.all(input.seller_ids.map(seller=>request({game_key:'ygo',card_version_id:input.card_version_id,seller_user_id:seller,token:getApp().globalData.jwt},'getSellerCardVersionId','/api/market/sellers/card-versions/'+input.card_version_id).then(response=>{
    const body=response.result.data;
    if(!body||!Array.isArray(body.products))throw new Error('invalid products');
    return {seller_id:seller,card_id:body.card_id,number:body.card_version_number,rarity:body.card_version_rarity,
      products:body.products.map(product),default_product:body.default_product?product(body.default_product):null};
  }))).then(sellers=>Object.assign(state,{status:'done',sellers})).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
