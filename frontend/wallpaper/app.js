/* ══════════════════════════════════════════════════════════════
   微博画廊 · 前端
   数据：data.json（壁纸 16:9）/ feed.json（动态，全部图）
   图片：/i/<档位>/<key> —— 服务端实时向微博 CDN 取回
   ══════════════════════════════════════════════════════════════ */
'use strict';

const REPO = 'shufajiaok/tech-news-aggregator';
const WORKFLOW_ID = '412852248';
const DISPATCH_REF = 'feat/wallpaper-gallery';
const LS_PAT = 'wg.pat';
const LS_LLM = 'wg.llm';          // 只存「已写入过」的标记，key 本体在仓库 Secret
const LS_LLM_MODEL = 'wg.llm.model';

const FEED_PAGE = 24;
const WALL_PAGE = 30;

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  page: 'feed',
  feedItems: [],
  posts: [],
  feedShown: 0,
  feedFilter: 'all',
  wallItems: [],
  wallView: [],
  wallShown: 0,
  wallOwner: 'all',
  wallSort: 'bytes',
  lb: [],
  lbIndex: -1,
};

/* ───────── 小工具 ───────── */
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const imgUrl = (key, size) => `/i/${size}/${encodeURIComponent(key || '')}`;

function thumbOf(it) {
  if (it.thumb) return it.thumb;
  return imgUrl(it.key, 'wap720');
}

function relTime(s) {
  if (!s) return '';
  const t = new Date(String(s).replace(' ', 'T'));
  if (isNaN(t)) return '';
  const d = (Date.now() - t.getTime()) / 1000;
  if (d < 0) return String(s).slice(0, 10).replace(/-/g, '.');
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
  if (d < 604800) return Math.floor(d / 86400) + ' 天前';
  return String(s).slice(0, 10).replace(/-/g, '.');
}

const initials = (name) => (name || '?').replace(/[\s·]/g, '').slice(0, 1);

function revealImg(img) { img.classList.add('on'); }

/* ───────── 玻璃重绘通知（控制台展开/收起后必须叫一次） ───────── */
function glassDirty() {
  try { dispatchEvent(new Event('glass-config-change')); } catch (e) { /* 组件未就绪 */ }
}

/* ───────── 路由 ───────── */
function setPage(name, push) {
  state.page = name;
  $('#page-feed').hidden = name !== 'feed';
  $('#page-wall').hidden = name !== 'wall';
  $$('#nav a').forEach((a) => {
    const on = a.dataset.page === name;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  if (push !== false && location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  if (name === 'wall') ensureWall(); else ensureFeed();
  scrollTo(0, 0);
  glassDirty();
}

/* ══════════════════════════ 动态流 ══════════════════════════ */

async function loadFeed() {
  const r = await fetch('feed.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error('feed.json ' + r.status);
  const d = await r.json();
  state.feedItems = d.items || [];
  state.posts = groupPosts(state.feedItems);
  $('#feedSub').textContent =
    state.posts.length + ' 条动态 · ' + state.feedItems.length + ' 张图 · ' + (d.generated_at || '');
  buildFeedChips();
  applyFeed();
}

function groupPosts(items) {
  const map = new Map();
  for (const it of items) {
    const k = it.post || it.key;
    let p = map.get(k);
    if (!p) {
      p = {
        id: k, owner: it.owner || '', group: it.group || '',
        created: it.created || '', text: it.text || '', post: it.post || '',
        imgs: [],
      };
      map.set(k, p);
    }
    if (!p.text && it.text) p.text = it.text;
    if (!p.created && it.created) p.created = it.created;
    if (it.key) p.imgs.push(it);
  }
  const arr = [...map.values()];
  for (const p of arr) p.imgs.sort((a, b) => (b.w * b.h) - (a.w * a.h));
  arr.sort((a, b) => String(b.created).localeCompare(String(a.created)));
  return arr;
}

function buildFeedChips() {
  const owners = new Map();
  for (const p of state.posts) owners.set(p.owner, (owners.get(p.owner) || 0) + 1);
  const top = [...owners.entries()].sort((a, b) => b[1] - a[1]).slice(0, 9);
  const box = $('#feedChips');
  box.innerHTML = '';
  box.appendChild(chip('全部', state.posts.length, state.feedFilter === 'all', () => {
    state.feedFilter = 'all';
    buildFeedChips();
    applyFeed();
  }));
  for (const pair of top) {
    box.appendChild(chip(pair[0], pair[1], state.feedFilter === pair[0], () => {
      state.feedFilter = pair[0];
      buildFeedChips();
      applyFeed();
    }));
  }
}

function applyFeed() {
  state.feedShown = 0;
  $('#feedList').innerHTML = '';
  renderFeedMore();
}

function feedView() {
  return state.feedFilter === 'all'
    ? state.posts
    : state.posts.filter((p) => p.owner === state.feedFilter);
}

function renderFeedMore() {
  const view = feedView();
  const end = Math.min(state.feedShown + FEED_PAGE, view.length);
  const frag = document.createDocumentFragment();
  for (let i = state.feedShown; i < end; i++) frag.appendChild(postCard(view[i]));
  $('#feedList').appendChild(frag);
  state.feedShown = end;
  $('#feedEmpty').hidden = view.length > 0;
}

function postCard(p) {
  const el = document.createElement('article');
  el.className = 'post';

  const n = Math.min(p.imgs.length, 4);
  const media = p.imgs.slice(0, n).map((it, i) => (
    '<button type="button" data-i="' + i + '" aria-label="查看大图">' +
    '<img loading="lazy" decoding="async" src="' + esc(imgUrl(it.key, 'wap720')) + '" alt=""></button>'
  )).join('');

  const dots = p.imgs.slice(0, 4).map((_, i) => '<i class="' + (i === 0 ? 'big' : '') + '"></i>').join('');
  const foot = p.post
    ? '<a href="' + esc(p.post) + '" target="_blank" rel="noopener">原微博 ↗</a>'
    : '';

  el.innerHTML =
    '<div class="post-head">' +
      '<span class="avatar">' + esc(initials(p.owner)) + '</span>' +
      '<span class="post-who">' +
        '<span class="name">' + esc(p.owner) + '</span>' +
        '<span class="when">' + esc(relTime(p.created)) +
          (p.group ? ' · ' + esc(p.group) : '') + '</span>' +
      '</span>' +
    '</div>' +
    (p.text ? '<p class="post-text">' + esc(p.text) + '</p>' : '') +
    (n ? '<div class="post-media n' + n + '">' + media + '</div>' : '') +
    '<div class="post-foot"><span class="dots">' + dots + '</span>' +
      '<span>' + p.imgs.length + ' 张</span>' + foot + '</div>';

  el.querySelectorAll('img').forEach((img) => {
    img.addEventListener('load', () => revealImg(img), { once: true });
    if (img.complete) revealImg(img);
  });

  const mediaEl = el.querySelector('.post-media');
  if (mediaEl) {
    mediaEl.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-i]');
      if (!btn) return;
      openLb(p.imgs, Number(btn.dataset.i), p);
    });
  }
  return el;
}

/* ══════════════════════════ 壁纸 ══════════════════════════ */

async function loadWall() {
  const r = await fetch('data.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error('data.json ' + r.status);
  const d = await r.json();
  state.wallItems = d.items || [];
  $('#wallSub').textContent =
    state.wallItems.length + ' 张 · 16:9 且不小于 2MB · ' + (d.generated_at || '');
  buildWallChips();
  applyWall();
}

function buildWallChips() {
  const owners = new Map();
  for (const it of state.wallItems) owners.set(it.owner, (owners.get(it.owner) || 0) + 1);

  const box = $('#wallChips');
  box.innerHTML = '';
  box.appendChild(chip('全部', state.wallItems.length, state.wallOwner === 'all', () => {
    state.wallOwner = 'all';
    buildWallChips();
    applyWall();
  }));
  const sorted = [...owners.entries()].sort((a, b) => b[1] - a[1]);
  for (const pair of sorted) {
    box.appendChild(chip(pair[0], pair[1], state.wallOwner === pair[0], () => {
      state.wallOwner = pair[0];
      buildWallChips();
      applyWall();
    }));
  }

  const sbox = $('#sortChips');
  sbox.innerHTML = '';
  const sorts = [['bytes', '体积优先'], ['created', '时间新→旧'], ['ratio', '最接近 16:9']];
  for (const pair of sorts) {
    sbox.appendChild(chip(pair[1], 0, state.wallSort === pair[0], () => {
      state.wallSort = pair[0];
      buildWallChips();
      applyWall();
    }));
  }
}

function applyWall() {
  let v = state.wallOwner === 'all'
    ? state.wallItems.slice()
    : state.wallItems.filter((it) => it.owner === state.wallOwner);

  const bytesOf = (it) => it.bytes || (it.mb ? it.mb * 1048576 : 0);
  if (state.wallSort === 'bytes') v.sort((a, b) => bytesOf(b) - bytesOf(a));
  else if (state.wallSort === 'created') v.sort((a, b) => String(b.created || '').localeCompare(String(a.created || '')));
  else v.sort((a, b) => Math.abs((a.ratio || 0) - 1.7778) - Math.abs((b.ratio || 0) - 1.7778));

  state.wallView = v;
  state.wallShown = 0;
  $('#grid').innerHTML = '';
  renderWallMore();
}

function renderWallMore() {
  const end = Math.min(state.wallShown + WALL_PAGE, state.wallView.length);
  const frag = document.createDocumentFragment();
  for (let i = state.wallShown; i < end; i++) frag.appendChild(tile(state.wallView[i], i));
  $('#grid').appendChild(frag);
  state.wallShown = end;
  $('#wallEmpty').hidden = state.wallView.length > 0;
}

function tile(it, idx) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'tile';
  el.setAttribute('aria-label', (it.owner || '') + ' ' + it.w + '×' + it.h);
  el.innerHTML =
    '<img loading="lazy" decoding="async" src="' + esc(thumbOf(it)) + '" alt="">' +
    '<span class="badge">' + (it.mb ? it.mb + 'MB' : '') + '</span>';
  const img = el.querySelector('img');
  img.addEventListener('load', () => revealImg(img), { once: true });
  if (img.complete) revealImg(img);
  el.addEventListener('click', () => openLb(state.wallView, idx));
  return el;
}

/* ───────── chips 工厂 ───────── */
function chip(label, n, on, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'chip';
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  b.innerHTML = esc(label) + (n ? ' <span class="n">' + n + '</span>' : '');
  b.addEventListener('click', onClick);
  return b;
}

/* ══════════════════════════ 灯箱 ══════════════════════════ */

function openLb(list, index, post) {
  if (!list || !list.length) return;
  state.lb = list;
  state.lbPost = post || null;
  state.lbIndex = Math.max(0, Math.min(index, list.length - 1));
  $('#lb').hidden = false;
  document.body.style.overflow = 'hidden';
  paintLb();
}

function paintLb() {
  const it = state.lb[state.lbIndex];
  if (!it) return;
  const img = $('#lbImg');
  img.classList.remove('on');
  $('#lbSpinner').hidden = false;
  img.onload = () => {
    $('#lbSpinner').hidden = true;
    img.classList.add('on');
  };
  img.onerror = () => { $('#lbSpinner').hidden = true; };
  img.src = imgUrl(it.key, 'orj1080');

  $('#lbOwner').textContent = it.owner || '';
  $('#lbDim').textContent = it.w + '×' + it.h + (it.mb ? ' · ' + it.mb + 'MB' : '') +
    ' · ' + (state.lbIndex + 1) + '/' + state.lb.length;

  const postUrl = (state.lbPost && state.lbPost.post) || it.post || '';
  $('#lbPost').hidden = !postUrl;
  if (postUrl) $('#lbPost').href = postUrl;
  $('#lbOpen').href = imgUrl(it.key, 'large');
}

function stepLb(d) {
  if (!state.lb.length) return;
  state.lbIndex = (state.lbIndex + d + state.lb.length) % state.lb.length;
  paintLb();
}

function closeLb() {
  $('#lb').hidden = true;
  document.body.style.overflow = '';
  $('#lbImg').removeAttribute('src');
}

/* ══════════════════════════ 控制台 ══════════════════════════ */

const con = {
  open(v) {
    $('#console').hidden = !v;
    $('#fab').setAttribute('aria-expanded', v ? 'true' : 'false');
    glassDirty();
    if (v) setTimeout(glassDirty, 80);
  },
  status(msg, kind) {
    const el = $('#conStatus');
    el.textContent = msg;
    el.className = 'con-status' + (kind ? ' ' + kind : '');
  },
  tag(id, text, kind) {
    const el = $(id);
    el.textContent = text;
    el.className = 'tag' + (kind ? ' ' + kind : '');
  },
};

const getPat = () => localStorage.getItem(LS_PAT) || '';
const getLlm = () => localStorage.getItem(LS_LLM) === '1';
const getLlmModel = () => localStorage.getItem(LS_LLM_MODEL) || 'qwen-plus';

function refreshTags() {
  con.tag('#patState', getPat() ? '已保存' : '未设置', getPat() ? 'ok' : '');
  con.tag('#llmState', getLlm() ? '已写入' : '未设置', getLlm() ? 'ok' : '');
  $('#patInput').value = '';
  $('#llmInput').value = '';
  $('#llmModel').value = getLlmModel();
  $('#patInput').placeholder = getPat() ? '已保存，重新输入可替换' : 'ghp_… 需要 repo + workflow';
}

/* GitHub API */
const GH = 'https://api.github.com';

async function gh(path, opts) {
  const pat = getPat();
  if (!pat) throw new Error('先填 GitHub PAT');
  const o = opts || {};
  const headers = {
    Authorization: 'Bearer ' + pat,
    Accept: 'application/vnd.github+json',
  };
  if (o.body) headers['Content-Type'] = 'application/json';
  const r = await fetch(GH + path, { method: o.method || 'GET', headers, body: o.body });
  if (r.status === 204) return null;
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!r.ok) {
    throw new Error('GitHub ' + r.status + ' ' + ((data && data.message) || text.slice(0, 120)));
  }
  return data;
}

/* sealed box：ephemeral 公钥 || box(msg, nonce=blake2b(epk||rpk)[:24])
   注意 nonce 必须是 blake2b（libsodium crypto_box_seal 的约定），
   用 SHA-512 会得到 422 improperly encrypted secret。 */
let _blake2b = null;
async function getBlake2b() {
  if (!_blake2b) _blake2b = (await import('./vendor/blakejs.esm.js')).blake2b;
  return _blake2b;
}

async function sealBox(msg, recipientPk) {
  const blake2b = await getBlake2b();
  const eph = nacl.box.keyPair();
  const buf = new Uint8Array(eph.publicKey.length + recipientPk.length);
  buf.set(eph.publicKey, 0);
  buf.set(recipientPk, eph.publicKey.length);
  const nonce = blake2b(buf, null, 24);
  const boxed = nacl.box(msg, nonce, recipientPk, eph.secretKey);
  const out = new Uint8Array(eph.publicKey.length + boxed.length);
  out.set(eph.publicKey, 0);
  out.set(boxed, eph.publicKey.length);
  return out;
}

function b64(u8) {
  let s = '';
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return btoa(s);
}

async function putSecret(name, value) {
  const pk = await gh('/repos/' + REPO + '/actions/secrets/public-key');
  const bin = Uint8Array.from(atob(pk.key), (c) => c.charCodeAt(0));
  const sealed = await sealBox(new TextEncoder().encode(value), bin);
  await gh('/repos/' + REPO + '/actions/secrets/' + name, {
    method: 'PUT',
    body: JSON.stringify({ encrypted_value: b64(sealed), key_id: pk.key_id }),
  });
}

async function deleteSecret(name) {
  await gh('/repos/' + REPO + '/actions/secrets/' + name, { method: 'DELETE' });
}

function splitInput(raw) {
  const parts = raw.split(',').map((s) => s.trim()).filter(Boolean);
  const allUid = parts.length > 0 && parts.every((p) => /^\d{5,}$/.test(p));
  return {
    names: allUid ? '' : parts.join(','),
    uids: allUid ? parts.join(',') : '',
  };
}

async function startScrape() {
  const raw = $('#qInput').value.trim();
  const parts = splitInput(raw);
  const btn = $('#qGo');
  btn.disabled = true;
  con.status(raw ? '正在触发（先由大模型解析目标）…' : '正在触发全量抓取…');
  try {
    await gh('/repos/' + REPO + '/actions/workflows/' + WORKFLOW_ID + '/dispatches', {
      method: 'POST',
      body: JSON.stringify({
        ref: DISPATCH_REF,
        inputs: {
          q: raw,                    // 原句交给 Action 里的大模型解析
          names: parts.names,        // 模型不可用时的兜底参数
          uids: parts.uids,
          pages: '6', max_new: '0', dry_run: false,
        },
      }),
    });
    con.status('已触发，排队中。抓完会自动提交索引。', 'ok');
    setTimeout(pollLatest, 8000);
  } catch (e) {
    con.status('触发失败：' + e.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

async function pollLatest() {
  try {
    const d = await gh('/repos/' + REPO + '/actions/runs?per_page=1');
    const run = d && d.workflow_runs && d.workflow_runs[0];
    if (!run) return;
    const map = { queued: '排队中', in_progress: '运行中', completed: '已完成' };
    const st = map[run.status] || run.status;
    const kind = run.conclusion === 'success' ? 'ok'
      : (run.conclusion && run.conclusion !== 'success') ? 'err' : '';
    con.status(st + (run.conclusion ? ' · ' + run.conclusion : '') +
      ' · ' + new Date(run.created_at).toLocaleTimeString('zh-CN') + '\n' + run.html_url, kind);
  } catch (e) {
    con.status('查询失败：' + e.message, 'err');
  }
}

function bindConsole() {
  $('#fab').addEventListener('click', () => con.open($('#console').hidden));
  $('#conClose').addEventListener('click', () => con.open(false));

  $('#patSave').addEventListener('click', async () => {
    const v = $('#patInput').value.trim();
    if (!v) return;
    localStorage.setItem(LS_PAT, v);
    refreshTags();
    con.status('已验证…');
    try {
      const me = await gh('/user');
      con.status('PAT 有效：' + me.login, 'ok');
    } catch (e) {
      localStorage.removeItem(LS_PAT);
      refreshTags();
      con.status('PAT 无效：' + e.message, 'err');
    }
  });

  $('#llmSave').addEventListener('click', async () => {
    const v = $('#llmInput').value.trim();
    const model = $('#llmModel').value;
    if (!v) { con.status('先粘贴大模型 Key', 'err'); return; }
    if (!getPat()) { con.status('先填 GitHub PAT', 'err'); return; }
    const btn = $('#llmSave');
    btn.disabled = true;
    con.status('正在加密写入大模型 Key…');
    try {
      // 百炼是 OpenAI 兼容接口；换别家就改这里的 base
      await putSecret('WB_LLM_KEY', v);
      await putSecret('WB_LLM_MODEL', model);
      await putSecret('WB_LLM_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1');
      localStorage.setItem(LS_LLM, '1');
      localStorage.setItem(LS_LLM_MODEL, model);
      $('#llmInput').value = '';
      refreshTags();
      con.status('大模型 Key 已写入 Secret（' + model + '），下次抓取生效。', 'ok');
    } catch (e) {
      con.status('写入失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
    }
  });

  $('#llmClear').addEventListener('click', async () => {
    if (!getPat()) { con.status('先填 GitHub PAT', 'err'); return; }
    con.status('正在清空…');
    try {
      for (const n of ['WB_LLM_KEY', 'WB_LLM_MODEL', 'WB_LLM_BASE_URL']) {
        try { await deleteSecret(n); } catch (e) { /* 不存在就跳过 */ }
      }
      localStorage.removeItem(LS_LLM);
      localStorage.removeItem(LS_LLM_MODEL);
      refreshTags();
      con.status('大模型 Secret 已清空，自然语言解析停用。', 'ok');
    } catch (e) {
      con.status('清空失败：' + e.message, 'err');
    }
  });

  $('#ckSave').addEventListener('click', async () => {
    const v = $('#ckInput').value.trim();
    if (!v) return;
    if (!getPat()) { con.status('先填 GitHub PAT', 'err'); return; }
    const btn = $('#ckSave');
    btn.disabled = true;
    con.status('正在加密写入…');
    try {
      await putSecret('WB_COOKIE', v);
      $('#ckInput').value = '';
      con.tag('#ckState', '已写入', 'ok');
      con.status('Cookie 已加密写入 Secret，下次抓取生效。', 'ok');
    } catch (e) {
      con.status('写入失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
    }
  });

  $('#ckClear').addEventListener('click', async () => {
    if (!getPat()) { con.status('先填 GitHub PAT', 'err'); return; }
    try {
      await deleteSecret('WB_COOKIE');
      con.tag('#ckState', '未设置', '');
      con.status('Secret 已删除，抓取回到访客态。', 'ok');
    } catch (e) {
      con.status('删除失败：' + e.message, 'err');
    }
  });

  $('#qGo').addEventListener('click', startScrape);
  $('#qInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startScrape();
  });

  refreshTags();
}

/* ══════════════════════════ 绑定 ══════════════════════════ */

function bindNav() {
  $$('#nav a').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      setPage(a.dataset.page);
    });
  });
  addEventListener('hashchange', () => {
    const p = location.hash.replace('#', '');
    if (p === 'feed' || p === 'wall') setPage(p, false);
  });
}

function bindInfinite() {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      if (e.target.id === 'feedSentinel') {
        if (state.feedShown < feedView().length) renderFeedMore();
      } else if (e.target.id === 'wallSentinel') {
        if (state.wallShown < state.wallView.length) renderWallMore();
      }
    }
  }, { rootMargin: '700px 0px' });
  io.observe($('#feedSentinel'));
  io.observe($('#wallSentinel'));
}

function bindLb() {
  $('#lbClose').addEventListener('click', closeLb);
  $('#lbPrev').addEventListener('click', () => stepLb(-1));
  $('#lbNext').addEventListener('click', () => stepLb(1));
  $('#lb').addEventListener('click', (e) => {
    if (e.target.id === 'lb' || (e.target.classList && e.target.classList.contains('lb-stage'))) closeLb();
  });
  addEventListener('keydown', (e) => {
    if ($('#lb').hidden) return;
    if (e.key === 'Escape') closeLb();
    else if (e.key === 'ArrowLeft') stepLb(-1);
    else if (e.key === 'ArrowRight') stepLb(1);
  });
}

/* ───────── 按需初始化 ───────── */
const ready = { feed: false, wall: false };

function ensureFeed() {
  if (ready.feed) return;
  ready.feed = true;
  loadFeed().catch((e) => {
    $('#feedSub').textContent = '动态载入失败：' + e.message;
  });
}

function ensureWall() {
  if (ready.wall) return;
  ready.wall = true;
  loadWall().catch((e) => {
    $('#wallSub').textContent = '壁纸载入失败：' + e.message;
  });
}

/* ───────── 启动 ───────── */
(function boot() {
  bindNav();
  bindConsole();
  bindLb();
  bindInfinite();

  const hashed = location.hash.replace('#', '');
  const start = (hashed === 'wall' || hashed === 'feed') ? hashed : 'feed';
  ensureFeed();
  setPage(start, false);

  fetch('data.json', { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d) return;
      $('#footStat').textContent =
        '壁纸 ' + d.count + ' 张 · 索引 ' + (d.generated_at || '');
    })
    .catch(() => {});
})();
