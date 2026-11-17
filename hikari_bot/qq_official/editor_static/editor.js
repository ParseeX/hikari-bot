'use strict';
const $ = id => document.getElementById(id);
let data, feature, key, spec, dirty = false, previewSeq = 0, previewTimer, noticeTimer, qqRequest, exampleId;
const copy = value => structuredClone(value);
function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function notice(text, error = false) { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; $('notice').hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => $('notice').hidden = true, error ? 12000 : 5000); }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) { if (response.status === 401 && path !== 'login') showLogin(); throw new Error(typeof result.detail === 'string' ? result.detail : '请求内容无效'); }
  return result;
}
function showLogin() { $('login').hidden = false; $('workspace').hidden = true; $('logout').hidden = true; }
function canLeave() { return window.sourceEditor.canLeave() && (!dirty || window.confirm('这条文案还没有保存，放弃本地修改？')); }
function statusTag(info) { return el('span', info.status || '已接入', 'tag ' + (info.status === '暂缓' || info.status === '已停用' ? 'deferred' : info.status === '部分接入' || info.status === '待接入' ? 'pending' : '')); }
function matchingStates(id) { const term = $('search').value.trim().toLowerCase(); const f = data.features[id]; const whole = !term || [f.label, ...(f.commands || [])].join(' ').toLowerCase().includes(term); return Object.entries(data.states).filter(([k,s]) => s.feature === id && (whole || (k + s.label + s.draft.template).toLowerCase().includes(term))); }
function renderNav() {
  $('features').replaceChildren();
  for (const [id, info] of Object.entries(data.features)) {
    const matches = matchingStates(id); if ($('search').value && !matches.length && !info.label.includes($('search').value)) continue;
    const b = el('button', undefined, id === feature ? 'selected' : ''); b.dataset.feature=id; b.append(el('span', info.label), statusTag(info)); b.onclick = () => { if (canLeave()) selectFeature(id); }; $('features').append(b);
  }
}
function selectFeature(id, preferredKey) {
  feature = id; dirty = false; window.sourceEditor.select(id); const info = data.features[id]; $('feature-info').replaceChildren();
  $('feature-info').append(el('h2', info.label));
  const grid = el('div', undefined, 'status-grid');
  for (const [heading, value] of [['与旧版的差异',info.differences],['原因',info.reason],['尚未接入',info.pending_work]]) { if (value) { const box = el('div'); box.append(el('h3',heading), el('p',value)); grid.append(box); } }
  $('feature-info').append(grid); renderNav(); renderStates();
  const available = matchingStates(feature); const primary=available.find(([,s])=>s.category==='main'&&s.parts)||available.find(([,s])=>s.category==='main')||available[0]; if (available.length) selectState(available.some(([k]) => k === preferredKey) ? preferredKey : primary[0]); else { key = undefined; $('editor').hidden = true; }
}
function renderStates() {
  const states = matchingStates(feature); $('states').replaceChildren(); $('count').textContent = states.length + ' 个场景'; $('no-states').hidden = !!states.length;
  for (const [category,label] of [['main','主要回复'],['part','引用段落'],['error','错误与异常提示']]) {
    const rows=states.filter(([,s])=>(s.category||'main')===category); if(!rows.length)continue;
    const section=el('details',undefined,'scene-group');section.open=category==='main'||rows.some(([k])=>k===key);section.append(el('summary',label+' · '+rows.length));const list=el('div',undefined,'scene-items');
    for (const [k,s] of rows) { const b = el('button', undefined, k === key ? 'selected' : ''); b.append(el('strong',s.label + (s.has_draft ? ' · 草稿' : '')), el('span',s.draft.template.replaceAll('\n',' '),'sample')); b.onclick = () => openState(k); list.append(b); }
    section.append(list);$('states').append(section);
  }
  $('related').replaceChildren();
  for(const item of data.features[feature].related || []){const b=el('button','共用文案：'+item.label);b.onclick=()=>{if(canLeave()){$('search').value='';selectFeature(item.feature,item.key);}};$('related').append(b);}
}
function openState(k) { if(!data.states[k]||!canLeave())return;feature=data.states[k].feature;selectState(k);$('editor').scrollIntoView({behavior:'smooth',block:'start'}); }
function selectState(k) { key = k; spec = copy(data.states[k].draft); dirty = false; exampleId=undefined; fillEditor(); renderStates(); }
function fillEditor() {
  const state = data.states[key]; $('editor').hidden = false; $('scene-title').textContent = state.label; $('scene-key').textContent = data.features[feature].label + ' / 场景文案'; $('scene-note').textContent = state.note || '此场景出现时使用以下文案。';
  $('draft-status').textContent = dirty ? '未保存' : state.has_draft ? '草稿未发布' : '当前已发布';
  $('mode').value = state.fragment && state.preview_root ? data.states[state.preview_root].draft.mode : spec.mode; $('mode').disabled = !!state.fragment;
  $('mode-note').textContent = state.format_note || (state.fragment ? '这是组合消息中的一段，格式跟随该功能的“完整回复”设置。' : '群聊和私聊均按此处选择发送：普通文字不解析 Markdown 标记；Markdown 使用官方 Markdown 消息。保存后需发布。QQ 已开放群聊/私聊自定义 Markdown，当前应用的群聊客户端效果仍待验收。历史的单张曲线已接入官方素材，可与 Markdown 正文合发（图在上），最终显示请在 QQ 验收。其他功能的上传图片、PDF、视频仍与 Markdown 分条发送。长文分段可能拆开 Markdown 语法。');
  $('template').value = spec.template; $('preview').textContent = '正在生成完整回复示例…'; $('context-title').textContent = '整条回复预览'; $('part-preview-box').hidden = true; renderVariables(); renderParts();
  $('back-main').hidden=!state.preview_root;$('back-main').onclick=()=>openState(state.preview_root);
  const root=data.states[state.preview_root||key];$('example').replaceChildren();for(const item of root.examples||[{id:'default',label:'实际回复示例（数据为虚构）'}]){const option=el('option',item.label);option.value=item.id;$('example').append(option);}
  $('button-settings').hidden = !Object.keys(spec.labels).length; $('button-labels').replaceChildren();
  for (const [name, text] of Object.entries(spec.labels)) { const label = el('label', name); const input = el('input'); input.value = text; input.maxLength = 24; input.dataset.role = name; input.oninput = edited; label.append(input); $('button-labels').append(label); }
  $('button-rows').value = spec.rows.map(row => row.join(' ')).join('\n');
  $('rollback').disabled = !state.can_rollback; updatePublish(); queuePreview();
}
function updatePublish() { $('publish').disabled = !Object.values(data.states).some(s => s.feature === feature && s.has_draft); }
function readSpec() { spec.template = $('template').value; if (!data.states[key].fragment) spec.mode = $('mode').value; for (const input of $('button-labels').querySelectorAll('input')) spec.labels[input.dataset.role] = input.value; spec.rows = $('button-rows').value.split('\n').map(row=>row.trim()).filter(Boolean).map(row=>row.split(/\s+/)); return spec; }
function edited() { dirty = true; $('draft-status').textContent = '未保存'; readSpec(); renderParts(); queuePreview(); }
function renderVariables(values) {
  const state=data.states[key];$('variables').replaceChildren();
  for(const [name,label] of Object.entries(state.fields)){
    if((state.hidden_fields||[]).includes(name)||state.parts?.[name])continue;
    const field=el('div',undefined,'data-field');field.append(el('strong',label),el('code','{'+name+'}'),el('span','示例：'+String(values?.[name]??state.samples[name]??'').slice(0,160),'muted'));$('variables').append(field);
  }
}
function partOrder() { return Object.entries(data.states[key].parts||{}).sort(([a],[b])=>{const x=spec.template.indexOf('{'+a+'}'),y=spec.template.indexOf('{'+b+'}');return (x<0?Infinity:x)-(y<0?Infinity:y);}); }
function movePart(name,step) {
  const parts=partOrder().filter(([n])=>spec.template.includes('{'+n+'}'));const i=parts.findIndex(([n])=>n===name),other=parts[i+step];if(!other)return;
  const a='{'+name+'}',b='{'+other[0]+'}',x=spec.template.indexOf(a),y=spec.template.indexOf(b);const first=x<y?[x,a,b,y]:[y,b,a,x];
  $('template').value=spec.template.slice(0,first[0])+first[2]+spec.template.slice(first[0]+first[1].length,first[3])+first[1]+spec.template.slice(first[3]+first[2].length);edited();
}
function renderParts(values) {
  const state=data.states[key];$('parts').replaceChildren();const parts=partOrder();$('parts-section').hidden=!parts.length&&!state.related_states?.length;
  parts.forEach(([name,part],i)=>{
    const row=el('div',undefined,'part-row'),links=el('div',undefined,'part-links');row.append(el('strong',part.label+'  {'+name+'}'));
    for(const child of [...new Set([...part.items.map(x=>x.key),...(part.alternatives||[])])]){const button=el('button','编辑：'+data.states[child].label);button.onclick=()=>openState(child);links.append(button);}
    const controls=el('div',undefined,'part-controls');for(const [step,label] of [[-1,'上移'],[1,'下移']]){const button=el('button',label);button.disabled=!spec.template.includes('{'+name+'}')||(step<0?i===0:i===parts.length-1);button.onclick=()=>movePart(name,step);controls.append(button);}
    row.append(links,controls,el('p',values?.[name]??'点击段落名称编辑里面的字样；上移/下移调整主要回复的顺序。','part-sample'));$('parts').append(row);
  });
  for(const child of state.related_states||[]){const button=el('button','相关内容：'+data.states[child].label);button.onclick=()=>openState(child);$('parts').append(button);}
}
function inline(parent, text) {
  // 只通过 DOM 创建文本、强调、代码和安全链接，禁止原样注入 HTML。
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\((?:https?:\/\/|mqqapi:\/\/aio\/inlinecmd\?)[^\s)]+\))/g; let start = 0;
  for (const m of text.matchAll(re)) { parent.append(document.createTextNode(text.slice(start,m.index))); const token = m[0]; let node; if(token.startsWith('**')) node=el('strong',token.slice(2,-2)); else if(token.startsWith('`')) node=el('code',token.slice(1,-1)); else { const split=token.indexOf(']('); node=el('a',token.slice(1,split)); const url=token.slice(split+2,-1); if(url.startsWith('mqqapi:')) {node.href='#';node.title='请在 QQ 中点击使用';node.onclick=e=>e.preventDefault();} else {node.href=url;node.target='_blank';node.rel='noopener noreferrer';} } parent.append(node); start=m.index+token.length; } parent.append(document.createTextNode(text.slice(start)));
}
function renderPreview(text, mode, id='preview') { const root=$(id); root.replaceChildren(); if(mode!=='markdown'){root.textContent=text;return;} for(const line of text.split('\n')) {const head=line.match(/^(#{1,3})\s+(.*)$/);const quote=line.startsWith('> ');const node=el(head?'h'+head[1].length:quote?'blockquote':'p'); inline(node,head?head[2]:quote?line.slice(2):line||'\u00a0');root.append(node);} }
function queuePreview() { const seq=++previewSeq; clearTimeout(previewTimer); previewTimer=setTimeout(async()=>{try{ const result=await api('preview',{key,spec:copy(readSpec()),example:exampleId});if(seq===previewSeq){renderPreview(result.context,result.context_mode);$('context-title').textContent='整条回复预览 · '+result.root_label;$('part-preview-box').hidden=result.root_key===key;renderPreview(result.text,result.mode,'part-preview');$('example').value=result.example;$('preview-note').textContent=(result.unrepresented?'当前例子不会触发这一段，下方单独显示本段。':'示例数据为虚构；预览包含本功能已保存的其他草稿。')+' '+result.media_note;renderVariables(result.values);renderParts(result.values);}}catch(error){if(seq===previewSeq)$('preview').textContent=error.message;}},250); }
async function load() { const next=await api('templates');data=next;$('login').hidden=true;$('workspace').hidden=false;$('logout').hidden=false; selectFeature(data.features[feature]?feature:Object.keys(data.features)[0],key); }
async function mutate(operation) {
  if(operation!=='save' && !canLeave()) return;
  const selectedFeature=feature, selectedKey=key;
  const body={operation,feature,revision:data.revision}; if(operation==='save'){body.key=key;body.spec=copy(readSpec());}
  $('workspace').inert=true;
  for(const id of ['save','publish','rollback']) $(id).disabled=true;
  try { data=await api('change',body);selectFeature(selectedFeature,selectedKey);notice(operation==='save'?'草稿已保存，尚未发布':operation==='publish'?'本功能文案已发布，新查询将使用新文案':'已恢复本功能上一版'); }
  catch(error){notice(error.message,true);}
  finally{$('workspace').inert=false;$('save').disabled=false;updatePublish();$('rollback').disabled=!data.states[key]?.can_rollback;}
}
$('login-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api('login',{password:$('password').value});$('password').value='';await load();}catch(error){notice(error.message,true);}finally{button.disabled=false;}};
$('logout').onclick=async()=>{if(!canLeave())return;try{await api('logout',{});dirty=false;data=undefined;showLogin();}catch(error){notice(error.message,true);}};
$('refresh').onclick=async()=>{if(canLeave())try{await load();notice('已载入最新版本');}catch(error){notice(error.message,true);}};
$('search').oninput=()=>{renderNav();renderStates();};
$('template').oninput=edited;$('mode').onchange=edited;$('button-rows').oninput=edited;
$('example').onchange=()=>{exampleId=$('example').value;queuePreview();};
$('save').onclick=()=>mutate('save');$('publish').onclick=()=>mutate('publish');$('rollback').onclick=()=>mutate('rollback');
$('restore').onclick=()=>{spec=copy(data.states[key].default);dirty=true;fillEditor();};
$('qq-preview').onclick=async()=>{if(dirty){notice('请先保存这条草稿，再发到 QQ 预览',true);return;}$('qq-preview').disabled=true;try{const result=await api('qq-preview',{key,revision:data.revision});qqRequest=result.id;$('qq-note').textContent=result.note;$('qq-status').disabled=false;}catch(error){notice(error.message,true);}finally{$('qq-preview').disabled=false;}};
$('qq-status').onclick=async()=>{if(!qqRequest)return;try{const result=await api('qq-preview/'+qqRequest);$('qq-note').textContent=result.note;}catch(error){notice(error.message,true);}};
window.addEventListener('beforeunload',event=>{if(dirty || window.sourceEditor.dirty()){event.preventDefault();event.returnValue='';}});
(async()=>{try{await api('session');await load();}catch(error){showLogin();if(error.message!=='请先登录')notice(error.message,true);}})();
