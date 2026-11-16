'use strict';
const $ = id => document.getElementById(id);
let data, feature, key, spec, dirty = false, previewSeq = 0, previewTimer, noticeTimer, qqRequest;
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
function canLeave() { return !dirty || window.confirm('这条文案还没有保存，放弃本地修改？'); }
function statusTag(info) { return el('span', info.status || '已接入', 'tag ' + (info.status === '暂缓' || info.status === '已停用' ? 'deferred' : info.status === '部分接入' || info.status === '待接入' ? 'pending' : '')); }
function matchingStates(id) { const term = $('search').value.trim().toLowerCase(); const f = data.features[id]; const whole = !term || [f.label, ...(f.commands || [])].join(' ').toLowerCase().includes(term); return Object.entries(data.states).filter(([k,s]) => s.feature === id && (whole || (k + s.label + s.draft.template).toLowerCase().includes(term))); }
function renderNav() {
  $('features').replaceChildren();
  for (const [id, info] of Object.entries(data.features)) {
    const matches = matchingStates(id); if ($('search').value && !matches.length && !info.label.includes($('search').value)) continue;
    const b = el('button', undefined, id === feature ? 'selected' : ''); b.append(el('span', info.label), statusTag(info)); b.onclick = () => { if (canLeave()) selectFeature(id); }; $('features').append(b);
  }
}
function selectFeature(id, preferredKey) {
  feature = id; dirty = false; const info = data.features[id]; $('feature-info').replaceChildren();
  const title = el('div', undefined, 'section-heading'); title.append(el('h2', info.label), statusTag(info)); $('feature-info').append(title);
  if (info.commands?.length) $('feature-info').append(el('p', info.commands.join(' · '), 'commands'));
  const grid = el('div', undefined, 'status-grid');
  for (const [heading, value] of [['当前实现',info.implementation || info.coverage],['与旧版的差异及原因',info.differences],['自动检查',info.tests],['QQ 客户端验证',info.qq],['文案编辑范围',info.coverage],['尚未接入',info.remaining]]) { if (value) { const box = el('div'); box.append(el('h3',heading), el('p',value)); grid.append(box); } }
  $('feature-info').append(grid,el('p','状态记录更新于 '+(info.updated || '未记录'),'small muted')); renderNav(); renderStates();
  const available = matchingStates(feature); if (available.length) selectState(available.some(([k]) => k === preferredKey) ? preferredKey : available[0][0]); else { key = undefined; $('editor').hidden = true; }
}
function renderStates() {
  const states = matchingStates(feature); $('states').replaceChildren(); $('count').textContent = states.length + ' 个场景'; $('no-states').hidden = !!states.length;
  for (const [k,s] of states) { const b = el('button', undefined, k === key ? 'selected' : ''); b.append(el('strong',s.label + (s.has_draft ? ' · 草稿' : '')), el('span',s.draft.template.replaceAll('\n',' '),'sample')); b.onclick = () => {if (canLeave()) {selectState(k);$('editor').scrollIntoView({behavior:'smooth',block:'start'});}}; $('states').append(b); }
  $('related').replaceChildren();
  for(const item of data.features[feature].related || []){const b=el('button','共用文案：'+item.label);b.onclick=()=>{if(canLeave()){$('search').value='';selectFeature(item.feature,item.key);}};$('related').append(b);}
}
function selectState(k) { key = k; spec = copy(data.states[k].draft); dirty = false; fillEditor(); renderStates(); }
function fillEditor() {
  const state = data.states[key]; $('editor').hidden = false; $('scene-title').textContent = state.label; $('scene-key').textContent = data.features[feature].label + ' / 场景文案'; $('scene-note').textContent = state.note || '此场景出现时使用以下文案。';
  $('draft-status').textContent = dirty ? '未保存' : state.has_draft ? '草稿未发布' : '当前已发布';
  $('mode').value = spec.mode; $('mode').disabled = !!state.fragment;
  $('mode-note').textContent = state.format_note || (state.fragment ? '这是组合消息中的一段，格式跟随该功能的“完整回复”设置。' : '群聊和私聊均按此处选择发送：普通文字不解析 Markdown 标记；Markdown 使用官方 Markdown 消息。保存后需发布。QQ 已开放群聊/私聊自定义 Markdown，当前应用的群聊客户端效果仍待验收。上传图片、PDF、视频与 Markdown 文字目前分条发送，这是现有实现限制；图片可以使用官方 Markdown 图片语法，上传素材内嵌尚未接入。长文分段可能拆开 Markdown 语法。');
  $('template').value = spec.template; $('variables').replaceChildren();
  for (const [name, label] of Object.entries(state.fields)) { const b = el('button', '{'+name+'} · '+label); b.type = 'button'; b.onclick = () => { const area = $('template'); area.setRangeText('{'+name+'}', area.selectionStart, area.selectionEnd, 'end'); area.focus(); edited(); }; $('variables').append(b); }
  $('button-settings').hidden = !Object.keys(spec.labels).length; $('button-labels').replaceChildren();
  for (const [name, text] of Object.entries(spec.labels)) { const label = el('label', name); const input = el('input'); input.value = text; input.maxLength = 24; input.dataset.role = name; input.oninput = edited; label.append(input); $('button-labels').append(label); }
  $('button-rows').value = spec.rows.map(row => row.join(' ')).join('\n');
  $('rollback').disabled = !state.can_rollback; updatePublish(); queuePreview();
}
function updatePublish() { $('publish').disabled = !Object.values(data.states).some(s => s.feature === feature && s.has_draft); }
function readSpec() { spec.template = $('template').value; spec.mode = $('mode').value; for (const input of $('button-labels').querySelectorAll('input')) spec.labels[input.dataset.role] = input.value; spec.rows = $('button-rows').value.split('\n').map(row=>row.trim()).filter(Boolean).map(row=>row.split(/\s+/)); return spec; }
function edited() { dirty = true; $('draft-status').textContent = '未保存'; readSpec(); queuePreview(); }
function inline(parent, text) {
  // 只通过 DOM 创建文本、强调、代码和安全链接，禁止原样注入 HTML。
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g; let start = 0;
  for (const m of text.matchAll(re)) { parent.append(document.createTextNode(text.slice(start,m.index))); const token = m[0]; let node; if(token.startsWith('**')) node=el('strong',token.slice(2,-2)); else if(token.startsWith('`')) node=el('code',token.slice(1,-1)); else { const split=token.indexOf(']('); node=el('a',token.slice(1,split)); node.href=token.slice(split+2,-1); node.target='_blank'; node.rel='noopener noreferrer'; } parent.append(node); start=m.index+token.length; } parent.append(document.createTextNode(text.slice(start)));
}
function renderPreview(text, mode) { const root=$('preview'); root.replaceChildren(); if(mode!=='markdown'){root.textContent=text;return;} for(const line of text.split('\n')) {const head=line.match(/^(#{1,3})\s+(.*)$/);const quote=line.startsWith('> ');const node=el(head?'h'+head[1].length:quote?'blockquote':'p'); inline(node,head?head[2]:quote?line.slice(2):line||'\u00a0');root.append(node);} }
function queuePreview() { const seq=++previewSeq; clearTimeout(previewTimer); previewTimer=setTimeout(async()=>{try{ const result=await api('preview',{key,spec:copy(readSpec())});if(seq===previewSeq)renderPreview(result.text,result.mode);}catch(error){if(seq===previewSeq)$('preview').textContent=error.message;}},250); }
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
$('save').onclick=()=>mutate('save');$('publish').onclick=()=>mutate('publish');$('rollback').onclick=()=>mutate('rollback');
$('restore').onclick=()=>{spec=copy(data.states[key].default);dirty=true;fillEditor();};
$('qq-preview').onclick=async()=>{if(dirty){notice('请先保存这条草稿，再发到 QQ 预览',true);return;}$('qq-preview').disabled=true;try{const result=await api('qq-preview',{key,revision:data.revision});qqRequest=result.id;$('qq-note').textContent=result.note;$('qq-status').disabled=false;}catch(error){notice(error.message,true);}finally{$('qq-preview').disabled=false;}};
$('qq-status').onclick=async()=>{if(!qqRequest)return;try{const result=await api('qq-preview/'+qqRequest);$('qq-note').textContent=result.note;}catch(error){notice(error.message,true);}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
(async()=>{try{await api('session');await load();}catch(error){showLogin();if(error.message!=='请先登录')notice(error.message,true);}})();
