'use strict';
window.sourceEditor = (() => {
  const byId = id => document.getElementById(id);
  let feature, file, listing, unsaved = false, generation = 0, pollTimer;
  function message(text, error=false) { byId('source-message').textContent=text; byId('source-message').className=error?'error':''; }
  async function request(operation, extra={}) {
    const response=await fetch('/api/source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation,feature,...extra})});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'源码操作失败');
    return result;
  }
  function canLeave(){return !unsaved || window.confirm('源码尚未保存，放弃这次本地修改？');}
  function cursor(){const input=byId('source-text');byId('source-cursor').textContent='第 '+(input.value.slice(0,input.selectionStart).split('\n').length)+' 行';}
  function dirty(){unsaved=true;byId('source-status').textContent='源码未保存';cursor();}
  function renderList(){
    byId('source-files').replaceChildren();
    for(const row of listing.files){const button=document.createElement('button');button.textContent=row.path.split('/').slice(-2).join('/')+(row.has_draft?' · 草稿':'');button.title=row.path;button.className=file?.path===row.path?'selected':'';button.onclick=()=>{if(canLeave())open(row.path).catch(e=>message(e.message,true));};byId('source-files').append(button);}
    byId('source-empty').hidden=!!listing.files.length;
    byId('source-rollback').disabled=!listing.can_rollback;
  }
  function showJob(){
    const job=listing.job;byId('source-job').textContent=job?job.note+(job.commit?' 版本 '+job.commit:''):'';
    byId('source-job').className=job&&['failed','recovery_required'].includes(job.status)?'error':'';
    const busy=job&&['pending','running','recovery_required'].includes(job.status);
    byId('source-publish').disabled=!!busy;byId('source-save').disabled=!!busy;byId('source-rollback').disabled=!!busy||!listing.can_rollback;
    clearTimeout(pollTimer);
    if(job&&['pending','running'].includes(job.status))pollTimer=setTimeout(()=>refreshJob().catch(()=>{byId('source-job').textContent='服务正在重载或连接暂时中断，稍后自动重查。';pollTimer=setTimeout(()=>refreshJob().catch(()=>{}),3000);}),2500);
  }
  async function refreshJob(){const selected=feature;const next=await request('list');if(selected!==feature)return;listing=next;renderList();showJob();}
  async function open(path){
    const seq=++generation;const next=await request('read',{path});if(seq!==generation)return;
    file=next;unsaved=false;listing.revision=next.revision;byId('source-body').hidden=false;
    byId('source-text').value=file.text;byId('source-path').textContent=file.path;
    byId('source-status').textContent=file.conflict?'线上已变化':file.has_draft?'源码草稿未发布':'当前源码';
    byId('source-affected').textContent='共用此文件的功能：'+(file.path.endsWith('/output_schema.json')?'全部功能（共用文案定义）':file.affected.map(f=>document.querySelector('#features button[data-feature="'+f+'"] span')?.textContent||f).join('、'));
    byId('source-diff').textContent=file.diff||'没有改动';message(file.conflict?'线上代码已更新；发布被阻止，需先合并草稿。':'');
    byId('source-symbol').replaceChildren();const first=document.createElement('option');first.textContent='选择函数或类';first.value='';byId('source-symbol').append(first);
    for(const anchor of file.anchors){const option=document.createElement('option');option.value=String(anchor.line);option.textContent=anchor.name+' · 第 '+anchor.line+' 行';byId('source-symbol').append(option);}
    renderList();showJob();cursor();
  }
  async function select(id){
    if(feature===id&&unsaved)return;
    feature=id;file=undefined;unsaved=false;const seq=++generation;clearTimeout(pollTimer);byId('source-body').hidden=true;byId('source-files').textContent='正在读取源码…';
    try{const next=await request('list');if(seq!==generation)return;listing=next;renderList();showJob();if(listing.files.length)await open(listing.files[0].path);}
    catch(error){if(seq===generation)byId('source-files').textContent=error.message;}
  }
  async function save(){
    if(!file)return;const selected=file.path;const result=await request('save',{path:selected,text:byId('source-text').value,base:file.base,revision:listing.revision});
    file=result;unsaved=false;listing.revision=result.revision;listing.files.find(x=>x.path===selected).has_draft=true;byId('source-diff').textContent=result.diff||'没有改动';byId('source-status').textContent='源码草稿未发布';renderList();message('源码草稿已保存，线上尚未改变。');
  }
  async function publish(operation){
    if(operation==='rollback'&&!canLeave())return;
    if(operation==='publish'&&unsaved)await save();
    const text=operation==='rollback'?'恢复上一次源码发布前的代码并重载三个服务？草稿和业务数据不会回退。':'发布当前功能已保存的源码改动，并重载官方机器人、原后台和网页？共享文件同时影响相关功能；代码产生的数据改动和消息不能靠回退撤销。';
    if(!window.confirm(text))return;
    listing=await request(operation,{revision:listing.revision});showJob();message('请求已提交，请查看发布状态。');
  }
  document.addEventListener('DOMContentLoaded',()=>{
    byId('source-text').oninput=dirty;byId('source-text').onclick=cursor;byId('source-text').onkeyup=cursor;
    byId('source-text').onkeydown=event=>{if(event.key==='Tab'){event.preventDefault();const input=event.target;input.setRangeText('    ',input.selectionStart,input.selectionEnd,'end');dirty();}};
    byId('source-symbol').onchange=()=>{const line=Number(byId('source-symbol').value);if(!line)return;const input=byId('source-text');const start=input.value.split('\n').slice(0,line-1).reduce((n,s)=>n+s.length+1,0);input.focus();input.setSelectionRange(start,start);input.scrollTop=Math.max(0,(line-4)*22.4);cursor();};
    byId('source-save').onclick=()=>save().catch(e=>message(e.message,true));
    byId('source-check').onclick=async()=>{try{const result=await request('check',{path:file.path,text:byId('source-text').value});message(result.message,!result.ok);}catch(e){message(e.message,true);}};
    byId('source-reload').onclick=()=>{if(canLeave())open(file.path).catch(e=>message(e.message,true));};
    byId('source-publish').onclick=()=>publish('publish').catch(e=>message(e.message,true));
    byId('source-rollback').onclick=()=>publish('rollback').catch(e=>message(e.message,true));
    byId('source-job-refresh').onclick=()=>refreshJob().catch(e=>message(e.message,true));
  });
  return {select,canLeave,dirty:()=>unsaved};
})();
