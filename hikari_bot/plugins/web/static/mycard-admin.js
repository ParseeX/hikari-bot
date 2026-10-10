'use strict';
const $ = id => document.getElementById(id);
let snapshot = {rules: [], names: {}, revision: ''};
let editing = null;
let selected = [];
let busy = false;
function status(message, error = false) {
  $('status').textContent = message;
  $('status').className = error ? 'error' : '';
  $('status').hidden = !message;
}
function showLogin() {
  $('login-panel').hidden = false; $('workspace').hidden = true; $('logout').hidden = true;
}
async function api(path, body) {
  const response = await fetch('/mc/admin/api/' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const error = new Error(typeof data.detail === 'string' ? data.detail : '请检查填写内容');
    error.status = response.status; throw error;
  }
  return data;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await fn(); } catch (error) { status(error.message || '请求失败，请重试', true); }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
function button(text, fn, className = 'quiet') {
  const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.className = className;
  el.addEventListener('click', fn); return el;
}
function reset() {
  editing = null; selected = []; $('rule-form').reset(); $('search-result').replaceChildren();
  $('editor-title').textContent = '添加映射'; $('save').textContent = '添加映射'; $('cancel').hidden = true; renderSelected();
}
function renderSelected() {
  $('selected').replaceChildren(...selected.map(card => button(card.name + ' ×', () => {
    selected = selected.filter(c => c.id !== card.id); renderSelected();
  })));
}
function renderRules() {
  const filter = $('filter').value.trim().toLowerCase();
  $('count').textContent = snapshot.rules.length;
  const rows = [];
  snapshot.rules.forEach((rule, index) => {
    const names = rule.cards.map(id => `${snapshot.names[id] || id} (${id})`);
    if (![rule.label, ...names].join(' ').toLowerCase().includes(filter)) return;
    const row = document.createElement('article'); row.className = 'rule';
    const title = document.createElement('div'); title.className = 'rule-title';
    const label = document.createElement('strong'); label.textContent = rule.label;
    const actions = document.createElement('div'); actions.className = 'actions';
    actions.append(button('编辑', () => {
      editing = index; $('label').value = rule.label; $('match').value = rule.match || 'any';
      selected = rule.cards.map(id => ({id, name: snapshot.names[id] || String(id)}));
      $('editor-title').textContent = '编辑映射'; $('save').textContent = '保存修改'; $('cancel').hidden = false;
      $('search-result').replaceChildren(); renderSelected(); $('label').focus();
    }), button('删除', () => {
      if (!confirm(`删除“${rule.label}”这条映射？`)) return;
      action(async () => {
        await api('rules/delete', {revision: snapshot.revision, index}); reset(); await load(); status('映射已删除');
      });
    }, 'quiet danger'));
    title.append(label, actions);
    const detail = document.createElement('p'); detail.textContent = (rule.match === 'all' ? '全部命中：' : '任一命中：') + names.join('、');
    row.append(title, detail); rows.push(row);
  });
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = snapshot.rules.length ? '没有匹配的映射' : '还没有分类映射。添加第一张识别卡片吧。'; rows.push(empty); }
  $('rules').replaceChildren(...rows);
}
async function load() {
  snapshot = await api('rules'); renderRules();
  $('login-panel').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
}
$('login-form').addEventListener('submit', event => {
  event.preventDefault(); action(async () => {
    const password = $('password').value;
    try { await api('login', {password}); } finally { $('password').value = ''; }
    reset(); await load(); status('');
  });
});
$('logout').addEventListener('click', () => action(async () => { await api('logout', {}); reset(); showLogin(); status('已退出登录'); }));
$('search').addEventListener('click', () => action(async () => {
  const query = $('card-query').value.trim();
  if (!query) throw new Error('请输入卡名或卡号');
  const {card} = await api('cards?q=' + encodeURIComponent(query));
  $('search-result').replaceChildren();
  if (!card) throw new Error('没有找到这张卡，请换一个卡名或卡号');
  const result = document.createElement('div'); result.className = 'candidate';
  const name = document.createElement('span'); name.textContent = card.name;
  const id = document.createElement('small'); id.textContent = card.id; name.append(id);
  result.append(name, button('选择', () => {
    if (!selected.some(c => c.id === card.id)) selected.push(card);
    renderSelected(); $('search-result').replaceChildren(); $('card-query').value = '';
  }, 'secondary'));
  $('search-result').append(result); status('');
}));
$('card-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); $('search').click(); } });
$('rule-form').addEventListener('submit', event => {
  event.preventDefault(); action(async () => {
    if (!selected.length) throw new Error('请先选择识别卡片');
    await api('rules', {revision: snapshot.revision, index: editing, label: $('label').value,
      cards: selected.map(c => c.id), match: $('match').value});
    reset(); await load(); status('映射已保存');
  });
});
$('cancel').addEventListener('click', reset);
$('refresh').addEventListener('click', () => action(async () => { reset(); await load(); status('已刷新'); }));
$('filter').addEventListener('input', renderRules);
load().catch(error => { if (error.status !== 401) status(error.message, true); });
