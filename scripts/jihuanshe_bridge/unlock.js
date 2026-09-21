rpc.exports.unlock=()=>new Promise((resolve,reject)=>Java.perform(()=>{
 const objects=[];
 Java.choose('com.android.systemui.keyguard.KeyguardViewMediator',{
  onMatch(o){objects.push(Java.retain(o));},
  onComplete(){
   if(objects.length!==1){reject(new Error('mediator unavailable'));return;}
   Java.scheduleOnMainThread(()=>{try{objects[0].handleKeyguardDone();resolve(true);}catch(e){reject(e);}});
  }
 });
}));
