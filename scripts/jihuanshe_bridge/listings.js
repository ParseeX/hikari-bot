(function(){
  const input=__INPUT__,request=require('api/cloud.js').cloudRequest;
  const state=globalThis.__codexJhsFastProbe={status:'pending'};
  // 仅返回商品价格、数量及备注，不输出卖家身份或会话信息。
  function publicProduct(row){
    const result={};
    for(const key of ['id','product_id','card_version_id','seller_user_id','price','min_price','quantity','remark','remark_only','condition','pull_off','origin_region']){
      if(row[key]!==undefined)result[key]=row[key];
    }
    if(Array.isArray(row.products))result.products=row.products.map(publicProduct);
    return result;
  }
  Promise.all(input.pages.map(page=>request({game_key:'ygo',card_version_id:input.card_version_id,page,token:getApp().globalData.jwt},'getOnSaleProducts','/api/market/products').then(response=>{
    const body=response.result.data;
    if(!body||!Array.isArray(body.data))throw new Error('invalid listing page');
    let pinned=body.pinned_products;
    if(!pinned){
      const headers=response.result.header||response.result.headers||{};
      const key=Object.keys(headers).find(k=>k.toLowerCase()==='warehouse_product');
      pinned=key?[typeof headers[key]==='string'?JSON.parse(headers[key]):headers[key]]:[];
    }
    return {current_page:body.current_page,last_page:body.last_page,total:body.total,
      entries:body.data.map(publicProduct),pinned_entries:pinned.map(publicProduct)};
  }))).then(pages=>Object.assign(state,{status:'done',pages})).catch(()=>Object.assign(state,{status:'error'}));
  return JSON.stringify({status:'started'});
})()
