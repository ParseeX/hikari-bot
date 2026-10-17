'use strict';
const $ = id => document.getElementById(id);
let snapshot = {rules: [], names: {}, revision: ''};
let editing = null;
let selected = [];
let searchResults = [];
let busy = false;
let representative = null;
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
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = b.dataset.selected === 'true'); }
}
function button(text, fn, className = 'quiet') {
  const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.className = className;
  el.addEventListener('click', fn); return el;
}
function reset() {
  editing = null; selected = []; searchResults = []; $('rule-form').reset(); $('search-result').replaceChildren();
  $('editor-title').textContent = '添加映射'; $('save').textContent = '添加映射'; $('cancel').hidden = true; renderSelected();
}
function renderSelected() {
  $('selected').replaceChildren(...selected.map(card => button(card.name + ' ×', () => {
    selected = selected.filter(c => c.id !== card.id); renderSelected();
  })));
  const ids = new Set(selected.map(card => card.id));
  $('search-result').querySelectorAll('button[data-card-id]').forEach(el => {
    const picked = ids.has(Number(el.dataset.cardId));
    el.dataset.selected = String(picked); el.disabled = busy || picked;
    el.textContent = picked ? '已选择' : '选择';
  });
  const summary = $('search-summary');
  if (summary) summary.textContent = `找到 ${searchResults.length} 张卡片 · 已选 ${selected.length} 张`;
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
      editing = index; $('label').value = rule.label;
      selected = rule.cards.map(id => ({id, name: snapshot.names[id] || String(id)}));
      $('editor-title').textContent = '编辑映射'; $('save').textContent = '保存修改'; $('cancel').hidden = false;
      searchResults = []; $('search-result').replaceChildren(); renderSelected(); $('label').focus();
    }), button('删除', () => {
      if (!confirm(`删除“${rule.label}”这条映射？`)) return;
      action(async () => {
        await api('rules/delete', {revision: snapshot.revision, index}); reset(); await load(); status('映射已删除');
      });
    }, 'quiet danger'));
    title.append(label, actions);
    const detail = document.createElement('p'); detail.textContent = names.join('、');
    row.append(title, detail); rows.push(row);
  });
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = snapshot.rules.length ? '没有匹配的映射' : '还没有分类映射。添加第一张识别卡片吧。'; rows.push(empty); }
  $('rules').replaceChildren(...rows);
}
async function load() {
  snapshot = await api('rules'); renderRules(); renderCategories();
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
  const {cards} = await api('cards?q=' + encodeURIComponent(query));
  searchResults = cards;
  $('search-result').replaceChildren();
  if (!cards.length) throw new Error('没有找到匹配卡片，请换一个关键词或卡号');
  const summary = document.createElement('p'); summary.id = 'search-summary';
  const list = document.createElement('div'); list.className = 'candidate-list';
  cards.forEach(card => {
    const result = document.createElement('div'); result.className = 'candidate';
    const name = document.createElement('span'); name.textContent = card.name;
    const id = document.createElement('small'); id.textContent = card.id; name.append(id);
    const choose = button('选择', () => {
      if (!selected.some(c => c.id === card.id)) selected.push(card);
      renderSelected();
    }, 'secondary');
    choose.dataset.cardId = card.id;
    result.append(name, choose); list.append(result);
  });
  $('search-result').append(summary, list); renderSelected(); status('');
}));
$('card-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); $('search').click(); } });
$('rule-form').addEventListener('submit', event => {
  event.preventDefault(); action(async () => {
    if (!selected.length) throw new Error('请先选择识别卡片');
    await api('rules', {revision: snapshot.revision, index: editing, label: $('label').value,
      cards: selected.map(c => c.id)});
    reset(); await load(); status('映射已保存');
  });
});
$('cancel').addEventListener('click', reset);
$('refresh').addEventListener('click', () => action(async () => { reset(); await load(); status('已刷新'); }));
$('filter').addEventListener('input', renderRules);
function renderCategories() {
  const previous = $('representative-label').value;
  const labels = [...new Set(snapshot.rules.map(rule => rule.label.trim()))];
  $('representative-label').replaceChildren(...labels.map(label => {
    const option = document.createElement('option'); option.value = label; option.textContent = label; return option;
  }));
  if (labels.includes(previous)) $('representative-label').value = previous;
  chooseCategory();
}
function renderRepresentative() {
  const box = $('representative-current'); box.replaceChildren();
  const text = document.createElement('p');
  text.textContent = representative ? ({manual: '已指定：', default: '默认：', pending: '待保存：'}[representative.source] || '') + representative.name : '暂无有效代表卡';
  box.append(representative ? representativeArt(representative, false) : emptyArt('暂无代表卡'));
  box.append(text);
  document.querySelectorAll('.representative-option').forEach(option => {
    const picked = representative?.id === Number(option.dataset.representativeId);
    option.setAttribute('aria-pressed', String(picked));
    option.querySelector('.representative-selected').hidden = !picked;
  });
}
function emptyArt(message) {
  const slot = document.createElement('span'); slot.className = 'mc-art-slot';
  const fallback = document.createElement('span'); fallback.className = 'mc-art-fallback'; fallback.textContent = message;
  slot.append(fallback); return slot;
}
function representativeArt(card, lazy = true) {
  const slot = emptyArt(card.name);
  const fallback = slot.firstChild;
  const img = document.createElement('img'); img.className = 'mc-art-image'; img.alt = '';
  img.width = 160; img.height = 160; img.loading = lazy ? 'lazy' : 'eager';
  img.addEventListener('error', () => slot.replaceChildren(fallback), {once: true});
  img.src = '/mc/admin/api/art/' + card.id + '?v=square2'; slot.replaceChildren(img);
  return slot;
}
function representativeOption(card) {
  const option = button('', () => { representative = {...card, source: 'pending'}; renderRepresentative(); }, 'representative-option');
  option.dataset.representativeId = card.id;
  option.setAttribute('aria-label', '选择 ' + card.name);
  option.setAttribute('aria-pressed', String(representative?.id === card.id));
  const art = representativeArt(card);
  const selected = document.createElement('span'); selected.className = 'representative-selected';
  selected.textContent = '已选择'; selected.hidden = representative?.id !== card.id;
  // 状态标记位于图槽外层，图片失败时不会被占位内容清除。
  const picture = document.createElement('span'); picture.className = 'representative-picture'; picture.append(art, selected);
  const name = document.createElement('span'); name.className = 'representative-name'; name.textContent = card.name;
  option.append(picture, name); return option;
}
function renderCategoryCandidates(label) {
  // 同名分类可以包含多条规则；Set 保留规则及 cards 数组的首次出现顺序。
  const ids = [...new Set(snapshot.rules.filter(rule => rule.label.trim() === label).flatMap(rule => rule.cards))];
  $('representative-count').textContent = `分类内卡片 · ${ids.length} 张`;
  $('representative-gallery').replaceChildren(...ids.map(id => representativeOption({id, name: snapshot.names[id] || String(id)})));
}
function chooseCategory() {
  const label = $('representative-label').value;
  const code = snapshot.effective_representatives?.[label];
  representative = code ? {id: code, name: snapshot.names[code] || String(code),
    source: Object.hasOwn(snapshot.representatives || {}, label) ? 'manual' : 'default'} : null;
  $('representative-results').replaceChildren(); $('representative-query').value = '';
  renderCategoryCandidates(label); renderRepresentative();
}
$('representative-label').addEventListener('change', chooseCategory);
$('representative-search').addEventListener('click', () => action(async () => {
  const query = $('representative-query').value.trim();
  if (!query) throw new Error('请输入卡名或卡号');
  const {cards} = await api('cards?purpose=representative&q=' + encodeURIComponent(query));
  $('representative-results').replaceChildren(...cards.map(representativeOption));
  if (!cards.length) throw new Error('没有找到匹配卡片');
  status('');
}));
$('representative-query').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); $('representative-search').click(); }
});
async function saveRepresentative(clear = false) {
  const label = $('representative-label').value;
  if (!label) throw new Error('请先添加分类映射');
  if (!clear && !representative) throw new Error('请先选择代表卡片');
  await api('representatives', {revision: snapshot.revision, label, card_id: clear ? null : representative.id});
  await load(); status(clear ? '已恢复默认代表卡' : '代表卡已保存');
}
$('representative-form').addEventListener('submit', event => {
  event.preventDefault(); action(() => saveRepresentative());
});
$('representative-clear').addEventListener('click', () => action(() => saveRepresentative(true)));
load().catch(error => { if (error.status !== 401) status(error.message, true); });
