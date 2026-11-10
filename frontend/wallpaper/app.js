/**
 * 微博壁纸画廊
 * 云端只存缩略图（720px）；原图留在本机，由 agent.py 串流。
 * 页面加载时探测本机 agent：在线 → 灯箱直接取原图；离线 → 回退缩略图。
 */
'use strict';

const AGENT_FIXED = 'http://127.0.0.1:8788';
const PAGE = 96;                 // 每次渲染的卡片数
const PROBE_TIMEOUT = 1200;

const $ = (s) => document.querySelector(s);
const state = {
  all: [], view: [], shown: 0,
  set: 'all', grade: 'all', owner: 'all', sort: 'bytes',
  labels: {}, localBase: null, lbIndex: -1,
};

/* ── 本机 agent 探测 ───────────────────────────── */
function probe(base) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), PROBE_TIMEOUT);
  return fetch(base + '/health', { signal: ctl.signal, cache: 'no-store', mode: 'cors' })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => (j && j.ok ? true : false))
    .catch(() => false)
    .finally(() => clearTimeout(timer));
}

async function detectLocal() {
  const q = new URLSearchParams(location.search).get('local');
  const cands = [];
  if (q) cands.push(q.replace(/\/+$/, ''));
  cands.push('');                       // 同源：页面由 agent 自己托管时（桌面 http 版 / 手机局域网）
  cands.push(AGENT_FIXED);
  const seen = new Set();
  for (const base of cands) {
    if (seen.has(base)) continue;
    seen.add(base);
    if (await probe(base)) return base;
  }
  return null;
}

function setStatus(kind, text, tip) {
  const el = $('#status');
  el.dataset.state = kind;
  $('#statusText').textContent = text;
  el.title = tip || text;
}

async function initLocal() {
  state.localBase = await detectLocal();
  if (state.localBase !== null) {
    setStatus('online', '本机在线 · 原图直出',
      '已连上本机 agent，灯箱直接加载高清原图');
  } else {
    setStatus('offline', '仅缩略图 · 本机未连',
      '两种可能：本机 agent 没启动，或浏览器拒绝公共页面访问本机地址（Chrome 的策略）。' +
      '点开图片后可用「本机看原图」跳到本机页面，那里同源、一定通');
  }
}

/* ── 路径 ─────────────────────────────────────── */
// thumb: "thumbs/main/xxx.jpg" → setKey "main"
const setKeyOf = (it) => it.thumb.split('/')[1];
const thumbUrl = (it) => it.thumb;
const origUrl = (it) => `${state.localBase === null ? '' : state.localBase}/img/` +
  `${encodeURIComponent(setKeyOf(it))}/${encodeURIComponent(it.file)}`;

/* ── 数据 ─────────────────────────────────────── */
async function load() {
  const r = await fetch('data.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error('data.json ' + r.status);
  const d = await r.json();
  state.all = d.items || [];
  state.labels = d.set_labels || {};
  $('#footStat').textContent =
    `${d.count} 张 · 生成于 ${d.generated_at} · 缩略图 ${(d.thumbs_bytes / 1048576).toFixed(1)}MB`;
  buildFilters();
  apply();
  openFromHash();
}

/* 深链：#p=<原图文件名>，供云端页面跳转到本机同源页面看原图 */
function openFromHash() {
  const m = location.hash.match(/^#p=(.+)$/);
  if (!m) return;
  let file;
  try { file = decodeURIComponent(m[1]); } catch { return; }
  const i = state.view.findIndex((x) => x.file === file);
  if (i >= 0) openLightbox(i);
}
window.addEventListener('hashchange', openFromHash);

function counts() {
  const bySet = {}, byGrade = {}, byOwner = {};
  for (const it of state.all) {
    const sk = setKeyOf(it);
    bySet[sk] = (bySet[sk] || 0) + 1;
    byGrade[it.grade] = (byGrade[it.grade] || 0) + 1;
    byOwner[it.owner] = (byOwner[it.owner] || 0) + 1;
  }
  return { bySet, byGrade, byOwner };
}

function chip(label, n, on, onClick) {
  const b = document.createElement('button');
  b.className = 'chip' + (on ? ' on' : '');
  b.innerHTML = label + (n != null ? `<span class="n">${n}</span>` : '');
  b.onclick = onClick;
  return b;
}

function buildFilters() {
  const { bySet, byGrade, byOwner } = counts();

  const sc = $('#setChips');
  sc.innerHTML = '';
  sc.appendChild(chip('全部', state.all.length, state.set === 'all',
    () => { state.set = 'all'; buildFilters(); apply(); }));
  for (const [k, n] of Object.entries(bySet)) {
    sc.appendChild(chip(state.labels[k] || k, n, state.set === k,
      () => { state.set = k; buildFilters(); apply(); }));
  }

  const gc = $('#gradeChips');
  gc.innerHTML = '';
  gc.appendChild(chip('全档', null, state.grade === 'all',
    () => { state.grade = 'all'; buildFilters(); apply(); }));
  for (const g of ['S', 'A', 'B', 'C']) {
    if (!byGrade[g]) continue;
    gc.appendChild(chip(g + ' 档', byGrade[g], state.grade === g,
      () => { state.grade = g; buildFilters(); apply(); }));
  }

  const oc = $('#ownerChips');
  oc.innerHTML = '';
  oc.appendChild(chip('全部账号', null, state.owner === 'all',
    () => { state.owner = 'all'; buildFilters(); apply(); }));
  Object.entries(byOwner).sort((a, b) => b[1] - a[1]).forEach(([o, n]) => {
    oc.appendChild(chip(o, n, state.owner === o,
      () => { state.owner = o; buildFilters(); apply(); }));
  });
}

/* ── 渲染 ─────────────────────────────────────── */
function apply() {
  let v = state.all.filter((it) =>
    (state.set === 'all' || setKeyOf(it) === state.set) &&
    (state.grade === 'all' || it.grade === state.grade) &&
    (state.owner === 'all' || it.owner === state.owner));

  if (state.sort === 'bytes') v.sort((a, b) => b.bytes - a.bytes);
  else if (state.sort === 'created') v.sort((a, b) => (b.created || '').localeCompare(a.created || ''));
  else v.sort((a, b) => Math.abs(a.ratio - 1.7778) - Math.abs(b.ratio - 1.7778));

  state.view = v;
  state.shown = 0;
  $('#grid').innerHTML = '';
  $('#empty').hidden = v.length > 0;
  $('#statCount').textContent = `${v.length} / ${state.all.length} 张`;
  renderMore();
}

function card(it, i) {
  const el = document.createElement('div');
  el.className = 'card';
  el.dataset.i = i;
  el.innerHTML =
    `<span class="badge" data-g="${it.grade}">${it.grade}</span>` +
    `<img loading="lazy" decoding="async" alt="">` +
    `<div class="info"><span class="who">${escapeHtml(it.owner)}</span>` +
    `<span class="dim">${it.w}×${it.h}</span></div>`;
  const img = el.querySelector('img');
  img.src = thumbUrl(it);
  img.onload = () => img.classList.add('loaded');
  el.onclick = () => openLightbox(i);
  return el;
}

function renderMore() {
  const frag = document.createDocumentFragment();
  const end = Math.min(state.shown + PAGE, state.view.length);
  for (let i = state.shown; i < end; i++) frag.appendChild(card(state.view[i], i));
  $('#grid').appendChild(frag);
  state.shown = end;
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* ── 灯箱 ─────────────────────────────────────── */
const lb = $('#lb'), lbImg = $('#lbImg'), lbSpinner = $('#lbSpinner');

function openLightbox(i) {
  state.lbIndex = i;
  lb.hidden = false;
  document.body.style.overflow = 'hidden';
  showCurrent();
}

function closeLightbox() {
  lb.hidden = true;
  document.body.style.overflow = '';
  lbImg.removeAttribute('src');
  lbImg.classList.remove('loaded');
  state.lbIndex = -1;
}

function step(d) {
  if (state.lbIndex < 0 || !state.view.length) return;
  state.lbIndex = (state.lbIndex + d + state.view.length) % state.view.length;
  showCurrent();
}

function showCurrent() {
  const it = state.view[state.lbIndex];
  if (!it) return;

  const online = state.localBase !== null;
  const url = online ? origUrl(it) : thumbUrl(it);

  lbImg.classList.remove('loaded');
  lbSpinner.hidden = false;
  $('#lbOwner').textContent = it.owner;
  $('#lbDim').textContent =
    `${it.w}×${it.h} · ${it.mb}MB · ${it.ratio.toFixed(3)}:1` +
    (it.created ? ` · ${it.created.replace('T', ' ')}` : '');
  const tag = $('#lbTag');
  tag.textContent = online ? '原图直出 · 本机' : '仅缩略图 · 本机未连';
  tag.dataset.off = online ? '0' : '1';

  // 本机页面是 http://127.0.0.1:8788/ 的同源环境，不受本地网络访问策略限制
  const ll = $('#lbLocal');
  if (online) {
    ll.hidden = true;
  } else {
    ll.hidden = false;
    ll.href = `${AGENT_FIXED}/#p=${encodeURIComponent(it.file)}`;
  }
  $('#lbPost').href = it.post || '#';
  $('#lbOpen').href = url;

  lbImg.onload = () => { lbSpinner.hidden = true; lbImg.classList.add('loaded'); };
  lbImg.onerror = () => {
    // 原图取失败（agent 中途关了）→ 回退缩略图
    if (lbImg.src !== new URL(thumbUrl(it), location.href).href) {
      lbImg.src = thumbUrl(it);
      tag.textContent = '原图取失败 · 已回退缩略图';
      tag.dataset.off = '1';
    } else {
      lbSpinner.hidden = true;
    }
  };
  lbImg.src = url;
}

/* ── 事件绑定 ─────────────────────────────────── */
$('#lbClose').onclick = closeLightbox;
$('#lbPrev').onclick = () => step(-1);
$('#lbNext').onclick = () => step(1);
lb.addEventListener('click', (e) => { if (e.target === lb) closeLightbox(); });

document.addEventListener('keydown', (e) => {
  if (lb.hidden) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
});

$('#sortSel').onchange = (e) => { state.sort = e.target.value; apply(); };

// 主题
const THEME_KEY = 'wp-theme';
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $('#themeToggle').textContent = t === 'light' ? '☀️' : '🌙';
  localStorage.setItem(THEME_KEY, t);
}
setTheme(localStorage.getItem(THEME_KEY) || 'dark');
$('#themeToggle').onclick = () =>
  setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');

// 滚动加载
new IntersectionObserver((es) => {
  if (es.some((e) => e.isIntersecting) && state.shown < state.view.length) renderMore();
}, { rootMargin: '600px' }).observe($('#sentinel'));

// 回到前台时重探一次（开机自启 agent 后不用手动刷新）
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.localBase === null) initLocal();
});

/* ── 启动 ─────────────────────────────────────── */
setStatus('probing', '检测本机…');
initLocal();
load().catch((e) => {
  $('#empty').hidden = false;
  $('#empty').textContent = '数据加载失败：' + e.message;
});
