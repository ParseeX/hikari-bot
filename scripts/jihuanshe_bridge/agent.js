const pending=new Map();
const prefix='__hikariJhs'+Date.now()+'_';
let runtime=null,CB=null,nextJob=1;
function evalCode(code,jobId,kind){
  Java.perform(function(){const cb=CB.$new();cb.jobId.value=jobId;cb.kind.value=kind;runtime.evaluateJavascript(code,cb);});
}
function poll(jobId){
  const job=pending.get(jobId);if(!job||job.finished)return;
  evalCode('JSON.stringify(globalThis.'+prefix+jobId+')',jobId,1);
}
function finish(jobId,result){
  const job=pending.get(jobId);if(!job||job.finished)return;
  job.finished=true;clearTimeout(job.timeout);
  pending.delete(jobId);
  runtime.evaluateJavascript('delete globalThis.'+prefix+jobId,null);
  job.resolve({...result,host_elapsed_ms:Date.now()-job.started});
}
rpc.exports={
  init(){return new Promise(function(resolve,reject){Java.perform(function(){
    const retained=[];
    CB=Java.registerClass({name:'org.hikaribot.jhs.Callback'+Date.now(),implements:[Java.use('android.webkit.ValueCallback')],fields:{jobId:'int',kind:'int'},methods:{onReceiveValue:function(v){
      let result;try{result=JSON.parse(String(v));}catch(_){return;}
      const id=this.jobId.value,kind=this.kind.value;
      if(kind===0){if(result.target){runtime=retained[id];resolve({ready:true,pid:Process.id});}return;}
      const job=pending.get(id);if(!job)return;
      if(result.status==='started'){
        job.jsStarted=true;if(!job.finished)poll(id);
      }else if(result.status==='done'||result.status==='error'||result.status==='skip'){
        finish(id,result);

      }else if(result.status==='pending'&&!job.finished){setTimeout(()=>poll(id),150);}
    }}});
    const found=new Set(),name='com.tencent.mm.plugin.appbrand.jsruntime.p';
    for(const loader of Java.enumerateClassLoadersSync()){
      try{const f=Java.ClassFactory.get(loader),c=f.use(name),id=c.class.hashCode();if(found.has(id))continue;found.add(id);
        f.choose(name,{onMatch:function(o){const cb=CB.$new();cb.jobId.value=retained.length;cb.kind.value=0;retained.push(Java.retain(o));o.evaluateJavascript('JSON.stringify({target:(function(){try{return typeof require("api/cloud.js").cloudRequest==="function";}catch(e){return false;}})()})',cb);},onComplete:function(){}});
      }catch(_){}
    }
    setTimeout(()=>{if(!runtime)reject(new Error('target context not found'));},5000);
  });});},
  query(code){return new Promise(function(resolve){
    const id=nextJob++;
    const job={resolve,started:Date.now(),jsStarted:false,finished:false};pending.set(id,job);
    job.timeout=setTimeout(()=>finish(id,{status:'timeout',stage:job.jsStarted?'waiting_price':'js_not_started'}),8000);
    evalCode(code.replaceAll('__codexJhsFastProbe',prefix+id),id,2);
  });},
  cleanup(){Java.perform(function(){if(runtime)runtime.evaluateJavascript(Array.from(pending.keys(),id=>'delete globalThis.'+prefix+id).join(';'),null);});return true;}
};

rpc.exports.lifecycle=action=>new Promise((resolve,reject)=>Java.perform(()=>{try{
 if(!runtime)throw new Error('no context');
 const factory=Java.ClassFactory.get(runtime.getClass().getClassLoader());
 const field=factory.use('hl.a').class.getDeclaredField('b');field.setAccessible(true);
 const loop=Java.cast(field.get(runtime.a0()),factory.use('hl.f3'));
 if(action==='resume')loop.resume();else if(action==='pause')loop.pause();else throw new Error('bad action');
 resolve(true);
}catch(e){reject(e);}}));
