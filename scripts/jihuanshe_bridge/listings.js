(function(){
  const input=__INPUT__,request=require('api/cloud.js').cloudRequest;
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  // 仅返回商品价格、数量及备注，不输出卖家身份或会话信息。
  function publicProduct(row){
    const result={};
    for(const key of ['id','product_id','card_version_id','price','min_price','quantity','remark','remark_only','condition','pull_off']){
      if(row[key]!==undefined)result[key]=row[key];
    }
    if(Array.isArray(row.products))result.products=row.products.map(publicProduct);
    result.field_names=Object.keys(row);
    return result;
  }
  Promise.all(input.pages.map(page=>request({game_key:'ygo',card_version_id:input.card_version_id,page,token:getApp().globalData.jwt},'getOnSaleProducts','/api/market/products').then(response=>{
    const body=response.result.data;
    if(!body||!Array.isArray(body.data))throw new Error('invalid listing page');
    return {current_page:body.current_page,last_page:body.last_page,total:body.total,
      entries:body.data.map(publicProduct),pinned_entries:(body.pinned_products||[]).map(publicProduct)};
  }))).then(pages=>Object.assign(state,{status:'done',pages})).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
