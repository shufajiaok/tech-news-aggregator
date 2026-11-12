/* ══════════════════════════════════════════════════════════════
   微博画廊 · 前端
   数据：data.json（壁纸，所有横竖比例 + S/A/B/C/V 分级）/ feed.json（动态，全部图）
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
  wallOrient: 'all',   // 'all' | 'landscape' | 'portrait'
  wallGrade: 'all',    // 'all' | 'S' | 'A' | 'B' | 'C' | 'V'
  wallSort: 'bytes',
  lb: [],
  lbIndex: -1,
  favItems: [],
  dispatchAt: 0,   // 本次触发的时刻，用来认出「属于这次的新 run」
  doneRunId: 0,    // 已经同步过索引的 run，避免重复刷新
  indexSha: '',    // 当前索引对应的 commit，有它才能绕开 CDN 缓存
};

/* ───────── 小工具 ───────── */
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/* 分享码必须显式透传到图片代理：网关只在「带 sharecode 或带 __wb_rsc Cookie」
   时放行 /i/ 请求。首屏、隐私模式、Cookie 失效、新标签打开大图时 Cookie 可能
   还没写入，不带码就是 403 —— 新抓的 11181 张代理图会整片空白。 */
const SHARE_CODE = (() => {
  try { return new URLSearchParams(location.search).get('sharecode') || ''; }
  catch (e) { return ''; }
})();
const withShare = (u) => (SHARE_CODE && u && u.indexOf('sharecode=') < 0)
  ? u + (u.indexOf('?') >= 0 ? '&' : '?') + 'sharecode=' + encodeURIComponent(SHARE_CODE)
  : u;
const imgUrl = (key, size) => withShare(`/i/${size}/${encodeURIComponent(key || '')}`);

/* ───────── 网页背景（只存这台浏览器） ───────── */
const BG_LS = 'wg.bg';
const BG_DEFAULT = '/bg.jpg';

function bgRead() {
  try { return JSON.parse(localStorage.getItem(BG_LS) || 'null'); } catch (e) { return null; }
}

function bgValid(src) {
  return /^data:image\/(jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(src)
    || /^https?:\/\/[^\s"'<>]+$/i.test(src)
    || /^\/[^\s"'<>]+$/.test(src);
}

/* CSS 的 background-image 与 shader 的 data-glass-source 必须是同一张图，
   否则玻璃还在折射旧壁纸。这段在普通 script 里同步跑，早于 defer 的玻璃
   module 初始化，所以不用改引擎、也不必等异步存储。 */
function applyBg() {
  const bd = $('.site-backdrop');
  if (!bd) return;
  const cfg = bgRead();
  const src = cfg && bgValid(cfg.src) ? cfg.src : '';
  bd.style.backgroundImage = src ? 'url(' + JSON.stringify(src) + ')' : '';
  bd.dataset.glassSource = src || BG_DEFAULT;
  const st = $('#bgState');
  if (st) st.textContent = '当前：' + (src ? (cfg.label || bgShort(src)) : '站点默认壁纸');
}

function bgShort(src) {
  if (src.startsWith('data:')) return '本机上传的图片';
  return src.length > 52 ? src.slice(0, 49) + '…' : src;
}

function setBg(src, label) {
  if (src && !bgValid(src)) { con.status('图片地址不合法', 'err'); return; }
  try {
    if (src) localStorage.setItem(BG_LS, JSON.stringify({ src, label: label || '' }));
    else localStorage.removeItem(BG_LS);
  } catch (e) {
    con.status('保存失败：图片太大，换张小一点的', 'err');
    return;
  }
  // shader 纹理在页面初始化时按 img.src 建立；重载一次即可让玻璃折射用上新图
  location.reload();
}

async function bgFromFile(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const max = 1920;
    const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(img.naturalWidth * k));
    c.height = Math.max(1, Math.round(img.naturalHeight * k));
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', .82);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function bindBg() {
  if (!$('#bgState')) return;
  $('#bgFile').addEventListener('change', async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (!/^image\//.test(f.type)) { con.status('请选择图片文件', 'err'); return; }
    con.status('正在处理图片…');
    try {
      setBg(await bgFromFile(f), '本机上传 · ' + f.name);
    } catch (err) {
      con.status('图片读取失败：' + err.message, 'err');
    }
    e.target.value = '';
  });
  $('#bgGo').addEventListener('click', () => {
    const v = $('#bgUrl').value.trim();
    if (!v) { con.status('先粘贴图片链接', 'err'); return; }
    setBg(v, '');
  });
  $('#bgUrl').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('#bgGo').click(); }
  });
  $('#bgReset').addEventListener('click', () => setBg('', ''));
  const cfg = bgRead();
  if (cfg && cfg.src && !cfg.src.startsWith('data:')) $('#bgUrl').value = cfg.src;
}

function thumbOf(it) {
  // 旧 1085 张是 thumbs/ 本地文件，新抓的是 /i/ 代理路径；只有代理路径需要带码
  const u = it.thumb || imgUrl(it.key, 'wap720');
  return u.charAt(0) === '/' ? withShare(u) : u;
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
  $('#page-fav').hidden = name !== 'fav';
  $$('#nav a').forEach((a) => {
    const on = a.dataset.page === name;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  if (push !== false && location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  if (name === 'fav') renderFav();
  else if (name === 'wall') ensureWall();
  else ensureFeed();
  scrollTo(0, 0);
  glassDirty();
}

/* ══════════════════════════ 动态流 ══════════════════════════ */

async function loadFeed(fresh, sha) {
  const rev = sha != null ? sha : state.indexSha;
  const qs = (fresh ? '?fresh=1' : '') + (rev ? (fresh ? '&' : '?') + 'sha=' + rev : '');
  const r = await fetch('feed.json' + qs, { cache: 'no-cache' });
  if (!r.ok) throw new Error('feed.json ' + r.status);
  const d = await r.json();
  state.feedItems = d.items || [];
  state.posts = groupPosts(state.feedItems);
  $('#feedSub').textContent =
    state.posts.length + ' 条动态 · ' + state.feedItems.length + ' 张图 · ' + (d.generated_at || '');
  buildFeedChips();
  applyFeed();
  return state.feedItems.length;
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

// 26+ 个账号不再横滑逐个找：一个按钮 + 弹出气泡列表（可搜）
function ownerChip(current, owners, onPick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'chip chip-owner' + (current !== 'all' ? ' on' : '');
  b.setAttribute('aria-pressed', current !== 'all' ? 'true' : 'false');
  b.innerHTML = (current === 'all'
    ? '选择账号'
    : esc(current) + ' <span class="n">' + (owners.get(current) || 0) + '</span>')
    + ' <span class="caret" aria-hidden="true">▾</span>';
  b.addEventListener('click', () => openPicker(owners, current, onPick));
  return b;
}

function openPicker(owners, current, onPick) {
  const box = $('#picker');
  const grid = $('#pickerGrid');
  const search = $('#pickerSearch');
  const render = (q) => {
    const kw = (q || '').trim().toLowerCase();
    const list = [...owners.entries()]
      .filter(([name]) => !kw || String(name).toLowerCase().includes(kw))
      .sort((a, b) => b[1] - a[1]);
    grid.innerHTML = '';
    if (!list.length) {
      grid.innerHTML = '<p class="picker-empty">没有匹配的账号</p>';
      return;
    }
    for (const [name, n] of list) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'bubble' + (name === current ? ' on' : '');
      el.innerHTML = '<span class="avatar">' + esc(initials(name)) + '</span>' +
        '<span class="bname">' + esc(name) + '</span>' +
        '<span class="bcount">' + n + '</span>';
      el.addEventListener('click', () => { closePicker(); onPick(name); });
      grid.appendChild(el);
    }
  };
  search.value = '';
  search.oninput = () => render(search.value);
  render('');
  box.hidden = false;
  setTimeout(() => search.focus(), 30);
}

function closePicker() {
  const box = $('#picker');
  if (box) box.hidden = true;
}

function bindPicker() {
  const box = $('#picker');
  if (!box) return;
  // 点遮罩关闭
  box.addEventListener('click', (e) => { if (e.target === box) closePicker(); });
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !box.hidden) closePicker();
  });
}

function buildFeedChips() {
  const owners = new Map();
  for (const p of state.posts) owners.set(p.owner, (owners.get(p.owner) || 0) + 1);
  const cur = state.feedFilter;
  const box = $('#feedChips');
  box.innerHTML = '';
  box.appendChild(chip('全部', state.posts.length, cur === 'all', () => {
    state.feedFilter = 'all';
    buildFeedChips();
    applyFeed();
  }));
  box.appendChild(ownerChip(cur, owners, (name) => {
    state.feedFilter = name;
    buildFeedChips();
    applyFeed();
  }));
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

async function loadWall(fresh, sha) {
  const rev = sha != null ? sha : state.indexSha;
  const qs = (fresh ? '?fresh=1' : '') + (rev ? (fresh ? '&' : '?') + 'sha=' + rev : '');
  const r = await fetch('data.json' + qs, { cache: 'no-cache' });
  if (!r.ok) throw new Error('data.json ' + r.status);
  const d = await r.json();
  state.wallItems = d.items || [];
  $('#wallSub').textContent =
    state.wallItems.length + ' 张 · 所有横竖比例，≥2MB 且长边 ≥1920 · ' + (d.generated_at || '');
  buildWallChips();
  applyWall();
  return state.wallItems.length;
}

/* 横/竖判定：ratio = w/h，<1 是竖图；没有 ratio 的旧条目用宽高兜底。
   tile() 的竖容器也走这个函数，筛选与容器不会错位。 */
function orientOf(it) {
  const r = (it.ratio != null) ? it.ratio : ((it.w && it.h) ? it.w / it.h : 0);
  return (r > 0 && r < 1) ? 'portrait' : 'landscape';
}

/* 旧版 S/A/B/C/V 五级（scripts/weibo_wallpaper.py 的口径）：
   S=16:9且≥2MB / A=16:9 / B=横图≥2MB / C=横图 / V=竖图。
   前端按 w/h/bytes 现算，不依赖数据里的 grade 字段（历史数据是脏的）。
   与 tools/cloud_build.py 的 grade_of() 保持同一套规则。 */
const GRADE_LABEL = {
  S: 'S·16:9达标', A: 'A·16:9', B: 'B·大横图', C: 'C·横图', V: 'V·竖图',
};
function gradeOf(it) {
  const w = it.w || 0, h = it.h || 0;
  const r = (it.ratio != null) ? it.ratio : (h ? w / h : 0);
  const big = (it.bytes || (it.mb ? it.mb * 1048576 : 0)) >= 2097152;
  const is169 = w > h && h > 0 && Math.abs(r - 1.7778) <= 0.06;
  if (is169) return big ? 'S' : 'A';
  if (w > h) return big ? 'B' : 'C';
  return 'V';
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
  box.appendChild(ownerChip(state.wallOwner, owners, (name) => {
    state.wallOwner = name;
    buildWallChips();
    applyWall();
  }));

  // 横竖筛选：计数按当前账号过滤后的集合算，切账号时数字跟着变
  const obox = $('#orientChips');
  obox.innerHTML = '';
  const scoped = state.wallOwner === 'all'
    ? state.wallItems
    : state.wallItems.filter((it) => it.owner === state.wallOwner);
  let nLand = 0, nPort = 0;
  for (const it of scoped) (orientOf(it) === 'portrait' ? nPort++ : nLand++);
  const orients = [
    ['all', '横竖不限', scoped.length],
    ['landscape', '横图', nLand],
    ['portrait', '竖图', nPort],
  ];
  for (const [key, label, n] of orients) {
    obox.appendChild(chip(label, n, state.wallOrient === key, () => {
      state.wallOrient = key;
      buildWallChips();
      applyWall();
    }));
  }

  // 分级筛选：S/A/B/C/V，计数同样按当前账号过滤后的集合算
  const gbox = $('#gradeChips');
  gbox.innerHTML = '';
  const gc = { S: 0, A: 0, B: 0, C: 0, V: 0 };
  for (const it of scoped) gc[gradeOf(it)]++;
  const grades = [
    ['all', '全部等级', scoped.length],
    ['S', 'S 达标', gc.S], ['A', 'A 16:9', gc.A],
    ['B', 'B 大横图', gc.B], ['C', 'C 横图', gc.C], ['V', 'V 竖图', gc.V],
  ];
  for (const [key, label, n] of grades) {
    gbox.appendChild(chip(label, n, state.wallGrade === key, () => {
      state.wallGrade = key;
      buildWallChips();
      applyWall();
    }));
  }

  const sbox = $('#sortChips');
  sbox.innerHTML = '';
  const sorts = [['bytes', '体积优先'], ['created', '时间新→旧'], ['ratio', '最接近目标比例']];
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

  if (state.wallOrient !== 'all') v = v.filter((it) => orientOf(it) === state.wallOrient);
  if (state.wallGrade !== 'all') v = v.filter((it) => gradeOf(it) === state.wallGrade);

  // 壁纸收录所有横竖比例，「最接近目标比例」= 离 16:9 或 3:2 里更近的那个最近
  const ratioDist = (it) => {
    const r = it.ratio || 0;
    return Math.min(Math.abs(r - 1.7778), Math.abs(r - 0.6667));
  };
  const bytesOf = (it) => it.bytes || (it.mb ? it.mb * 1048576 : 0);
  if (state.wallSort === 'bytes') v.sort((a, b) => bytesOf(b) - bytesOf(a));
  else if (state.wallSort === 'created') v.sort((a, b) => String(b.created || '').localeCompare(String(a.created || '')));
  else v.sort((a, b) => ratioDist(a) - ratioDist(b));

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

function tile(it, idx, list) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'tile';
  el.setAttribute('aria-label', (it.owner || '') + ' ' + it.w + '×' + it.h);
  const g = gradeOf(it);
  el.innerHTML =
    '<img loading="lazy" decoding="async" src="' + esc(thumbOf(it)) + '" alt="">' +
    '<span class="badge">' + (it.mb ? it.mb + 'MB' : '') + '</span>' +
    '<span class="grade g-' + g + '" title="' + esc(GRADE_LABEL[g]) + '">' + g + '</span>' +
    '<span class="tile-bg" role="button" tabindex="0" title="设为网页背景">设为背景</span>';
  // 3:2 竖图必须用竖的容器，否则被 16:9 的 cover 裁成中间一条横缝。
  // 判定与横竖筛选共用 orientOf，避免两处口径不一致。
  if (orientOf(it) === 'portrait') el.style.aspectRatio = '2 / 3';
  const img = el.querySelector('img');
  img.addEventListener('load', () => revealImg(img), { once: true });
  if (img.complete) revealImg(img);
  const useAsBg = (e) => {
    e.preventDefault(); e.stopPropagation();
    setBg(imgUrl(it.key, 'orj1080'), (it.owner || '壁纸') + ' · ' + it.key);
  };
  const bgb = el.querySelector('.tile-bg');
  bgb.addEventListener('click', useAsBg);
  bgb.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') useAsBg(e);
  });
  el.addEventListener('click', () => openLb(list || state.wallView, idx));
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
    ' · ' + GRADE_LABEL[gradeOf(it)] + ' · ' + (state.lbIndex + 1) + '/' + state.lb.length;

  const postUrl = (state.lbPost && state.lbPost.post) || it.post || '';
  $('#lbPost').hidden = !postUrl;
  if (postUrl) $('#lbPost').href = postUrl;
  $('#lbOpen').href = imgUrl(it.key, 'large');
  paintFavBtn();
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

/* ══════════════════════════ 我的收藏（只在本机） ══════════════════════════ */
const DB_NAME = 'wg-fav';
const STORE = 'imgs';

function openDb() {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'key' });
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}
async function favAll() {
  const db = await openDb();
  return new Promise((ok, no) => {
    const q = db.transaction(STORE).objectStore(STORE).getAll();
    q.onsuccess = () => ok(q.result || []);
    q.onerror = () => no(q.error);
  });
}
async function favPut(items) {
  const db = await openDb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, 'readwrite');
    const s = t.objectStore(STORE);
    for (const it of items) s.put(it);
    t.oncomplete = () => ok(items.length);
    t.onerror = () => no(t.error);
  });
}
async function favDel(key) {
  const db = await openDb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, 'readwrite');
    t.objectStore(STORE).delete(key);
    t.oncomplete = ok;
    t.onerror = () => no(t.error);
  });
}
async function favClear() {
  const db = await openDb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, 'readwrite');
    t.objectStore(STORE).clear();
    t.oncomplete = ok;
    t.onerror = () => no(t.error);
  });
}

function renderFav() {
  const items = state.favItems.slice().sort((a, b) => (b.saved || 0) - (a.saved || 0));
  $('#favSub').textContent = items.length + ' 张 · 只存在你自己的浏览器，不上传';
  $('#favEmpty').hidden = items.length > 0;
  const grid = $('#favGrid');
  grid.innerHTML = '';
  items.forEach((it, i) => grid.appendChild(tile(it, i, items)));
  const box = $('#favChips');
  box.innerHTML = '';
  if (items.length) box.appendChild(chip('清空收藏', 0, false, async () => {
    if (!confirm('确定清空这台浏览器上的收藏？')) return;
    await favClear();
    state.favItems = [];
    renderFav();
  }));
}

function paintFavBtn() {
  const it = state.lb[state.lbIndex];
  const on = !!(it && state.favItems.some((f) => f.key === it.key));
  const b = $('#lbFav');
  b.textContent = on ? '✓ 已收藏' : '☆ 收藏';
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
}

async function toggleFav() {
  const it = state.lb[state.lbIndex];
  if (!it || !it.key) return;
  const i = state.favItems.findIndex((f) => f.key === it.key);
  if (i >= 0) {
    await favDel(it.key);
    state.favItems.splice(i, 1);
  } else {
    const rec = {
      key: it.key, owner: it.owner || '', w: it.w || 0, h: it.h || 0,
      post: it.post || '', created: it.created || '', text: it.text || '',
      saved: Date.now(),
    };
    await favPut([rec]);
    state.favItems.push(rec);
  }
  renderFav();
  paintFavBtn();
}

/* ══════════════════════════ 高清原图提取 ══════════════════════════ */
/* 不找人、不猜昵称、不调任何微博接口。微博图床靠 URL 路径里的尺寸档位
   动态重采样，把档位换成 /large/ 就是源站原始二进制（skill 实测结论：
   woriginal 是带水印二次压缩、orj1080 是缩水版，只有 /large/ 是真原图）。
   本站 /i/large/<key> 会带 Referer: https://weibo.com/ 取回，所以是
   「能直接打开、能直接下载」的链接；CDN 直链浏览器直开会 403。 */

// 服务端 KEY_RE 只放行这三种后缀的纯字母数字 key，前端按同一口径过滤
const KEY_OK = /^[0-9a-zA-Z]{6,}\.(?:jpg|gif|png)$/i;
const CDN_HOST = 'wx3.sinaimg.cn';

function extractKeys(raw) {
  // 兼容网页源码里被转义的 \/、HTML 实体 &amp;、协议相对 //host
  const text = String(raw || '').replace(/\\\//g, '/').replace(/&amp;/g, '&');
  const re = /(?:https?:\/\/|\/\/)[a-z0-9.-]*sinaimg\.cn\/[^\s"'<>`]+/gi;
  const out = [], seen = new Set();
  for (const m of text.match(re) || []) {
    // 中文标点 / 右括号经常紧跟在链接后面，会被正则一起吞进来
    const s = (m.startsWith('//') ? 'https:' + m : m)
      .replace(/[)\],，。；、！？"'>】〉》]+$/g, '');
    try {
      const u = new URL(s);
      if (!/(^|\.)sinaimg\.cn$/i.test(u.hostname)) continue;
      const parts = u.pathname.split('/').filter(Boolean);
      const key = parts[parts.length - 1] || '';
      if (!KEY_OK.test(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    } catch (e) { /* 不是合法 URL，跳过 */ }
  }
  return out;
}

const absProxy = (key) => location.origin + imgUrl(key, 'large');
const absCdn = (key) => 'https://' + CDN_HOST + '/large/' + key;

async function copyText(s) {
  try {
    await navigator.clipboard.writeText(s);
    return true;
  } catch (e) {
    const ta = document.createElement('textarea');   // 非安全上下文降级
    ta.value = s;
    ta.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { /* 忽略 */ }
    ta.remove();
    return ok;
  }
}

async function copyAndSay(s, msg) {
  const ok = await copyText(s);
  con.status(ok ? msg : '浏览器不允许复制，请手动选中链接文本', ok ? 'ok' : 'err');
}

function extractLinks() {
  const keys = extractKeys($('#qInput').value);
  const head = $('#exHead'), list = $('#exList');
  if (!keys.length) {
    head.hidden = true; list.hidden = true; list.innerHTML = '';
    con.status('没找到微博图片链接。请粘贴图片地址（微博网页里右键图片 → 复制图片地址），' +
      '或一整段含图片链接的文本。\n只给帖子 / 主页链接取不到图：那需要登录微博，本站不解析。', 'err');
    return;
  }
  head.hidden = false; list.hidden = false;
  $('#exCount').textContent = '找到 ' + keys.length + ' 张 · /large/ 源站原图';
  $('#exCopyAll').onclick = () => copyAndSay(
    keys.map(absProxy).join('\n'), '已复制 ' + keys.length + ' 条可直开的原图链接');

  list.innerHTML = '';
  for (const key of keys) {
    const proxy = absProxy(key), cdn = absCdn(key);
    const el = document.createElement('div');
    el.className = 'ex-item';
    el.innerHTML =
      '<a class="ex-thumb" href="' + esc(proxy) + '" target="_blank" rel="noopener">' +
        '<img loading="lazy" src="' + esc(imgUrl(key, 'wap360')) + '" alt=""></a>' +
      '<div class="ex-body">' +
        '<code class="ex-key">' + esc(key) + '</code>' +
        '<a class="ex-url" href="' + esc(proxy) + '" target="_blank" rel="noopener">' + esc(proxy) + '</a>' +
        '<span class="ex-cdn">' + esc(cdn) + '</span>' +
        '<div class="ex-btns">' +
          '<button class="btn ghost" type="button" data-a="proxy">复制可直开</button>' +
          '<button class="btn ghost" type="button" data-a="cdn">复制 CDN 直链</button>' +
          '<a class="btn ghost" href="' + esc(proxy) + '" download="' + esc(key) + '">下载 ↓</a>' +
        '</div>' +
      '</div>';
    el.querySelector('[data-a="proxy"]').addEventListener('click',
      () => copyAndSay(proxy, '已复制可直开的原图链接'));
    el.querySelector('[data-a="cdn"]').addEventListener('click',
      () => copyAndSay(cdn, '已复制微博 CDN 直链\n注意：浏览器直接打开会 403（需带 Referer），拿去下载器用'));
    list.appendChild(el);
  }
  con.status('提取到 ' + keys.length + ' 张。「可直开」走本站代理带 Referer，能直接打开和下载；' +
    '「CDN 直链」给下载器用。', 'ok');
  glassDirty();
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
  // 主流程与 PAT 完全解耦：有没有 PAT 都是同一个「粘贴链接 → 出原图」。
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

// 主页链接 / 纯数字 -> uid。搜索接口在境外常被风控，直接给 uid 是最稳的路子。
function uidOf(s) {
  const t = (s || '').trim();
  if (/^\d{5,}$/.test(t)) return t;
  const m = t.match(/weibo\.(?:com|cn)\/(?:u\/|profile\/)?(\d{5,})/i);
  return m ? m[1] : '';
}

function splitInput(raw) {
  const parts = raw.split(/[,，、]+/).map((s) => s.trim()).filter(Boolean);
  const names = [], uids = [];
  for (const p of parts) {
    const u = uidOf(p);
    if (u) uids.push(u);
    else names.push(p);
  }
  return { names: names.join(','), uids: uids.join(',') };
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
          pages: '12', max_new: '0', dry_run: false,
        },
      }),
    });
    con.status('已触发，排队中。抓完会自动提交索引。', 'ok');
    state.dispatchAt = Date.now();
    setTimeout(pollLatest, 8000);
  } catch (e) {
    con.status('触发失败：' + e.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

/* 抓完必须重新拉一次索引：
   Action 提交的是仓库里的 data.json / feed.json，页面不主动要就还是旧数据，
   用户会以为「又搜失败了」。
   带上最新 commit sha 是关键：raw 域名对分支名有 5 分钟 CDN 缓存，按分支拉
   大概率还是旧的那份；换成具体 commit 后 URL 唯一，必然回源，拿到就是这次的结果。 */
async function syncIndex() {
  con.status('抓取完成，正在同步索引…');
  let sha = '';
  try {
    const c = await gh('/repos/' + REPO + '/commits?sha=' + encodeURIComponent(DISPATCH_REF) + '&per_page=1');
    sha = (c && c[0] && c[0].sha) || '';
  } catch (e) { /* 拿不到就退化成按分支拉，慢一点但能用 */ }

  let feedN = null, wallN = null;
  state.indexSha = sha;
  try { feedN = await loadFeed(true, sha); } catch (e) { /* 下面统一报 */ }
  try { wallN = await loadWall(true, sha); } catch (e) { /* 同上 */ }
  if (feedN == null && wallN == null) {
    con.status('抓取完成，但索引同步失败，稍后手动刷新页面', 'err');
    return;
  }
  const wallMsg = wallN == null ? '' : ' · 壁纸 ' + wallN + ' 张';
  con.status('抓取完成，索引已更新：动态 ' + (feedN == null ? '?' : feedN) +
    ' 张图' + wallMsg + '（壁纸收所有横竖比例，要求 ≥2MB 且长边 ≥1920）', 'ok');
}

async function pollLatest(tries) {
  tries = tries || 0;
  try {
    const d = await gh('/repos/' + REPO + '/actions/runs?per_page=1');
    const run = d && d.workflow_runs && d.workflow_runs[0];
    if (!run) return;
    const map = { queued: '排队中', in_progress: '运行中', completed: '已完成' };
    const st = map[run.status] || run.status;
    const bad = run.conclusion && run.conclusion !== 'success';
    // 失败时把挂在哪个步骤拉回来，省得再去 GitHub 翻日志
    let detail = '';
    if (bad && tries > 0) {
      try {
        const j = await gh('/repos/' + REPO + '/actions/runs/' + run.id + '/jobs');
        const steps = (((j.jobs || [])[0] || {}).steps) || [];
        const badStep = steps.filter((s) => s.conclusion === 'failure')[0];
        if (badStep) detail = ' · 挂在「' + badStep.name + '」';
      } catch (_) { /* 拿不到就算了，不挡住状态显示 */ }
    }
    const kind = run.conclusion === 'success' ? 'ok' : bad ? 'err' : '';
    con.status(st + (run.conclusion ? ' · ' + run.conclusion + detail : '') +
      ' · ' + new Date(run.created_at).toLocaleTimeString('zh-CN') + '\n' + run.html_url, kind);

    // 跑到终点就同步索引。只认本次触发之后新建的 run，
    // 否则触发的头几秒会读到上一条已完成的 run，白刷一次还误导状态。
    const mine = new Date(run.created_at).getTime() >= state.dispatchAt - 60000;
    if (run.status === 'completed' && mine && state.doneRunId !== run.id) {
      state.doneRunId = run.id;
      await syncIndex();
      return;
    }
    // 还在跑就继续追（约 15 分钟），不要只查一次就停
    if (run.status !== 'completed' && tries < 60) {
      setTimeout(() => pollLatest(tries + 1), 15000);
    }
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

  // 简化版：主流程只有「粘贴链接 → 提取高清原图」，与 PAT 完全解耦。
  $('#qGo').addEventListener('click', extractLinks);
  $('#qClear').addEventListener('click', () => {
    $('#qInput').value = '';
    $('#exHead').hidden = true;
    $('#exList').hidden = true;
    $('#exList').innerHTML = '';
    con.status('已清空，粘贴图片链接再点「提取高清原图」');
    $('#qInput').focus();
  });
  $('#qInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); extractLinks(); }
  });

  refreshTags();
  con.status('粘贴微博图片链接 → 提取 /large/ 源站原图。纯本地解析，无需账号。');
  initTuner();
}

/* ══════════════════════════ 视觉调节 · 玻璃引擎 ══════════════════════════ */
/* 引擎是 ES module；动态 import 命中同一个已加载的模块实例，不会二次初始化。
   全部参数全局统一控制（不按组件单独配），范围直接取引擎的 GLASS_LIMITS，
   这样滑块和 setGlassConfig 的校验永远一致。 */

const TUNER_LS = 'wg.glass.tuner';
const ROUNDNESS_BOUNDS = [2, 8, .1];

const TUNER_GROUPS = [
  // 玻璃只剩顶部导航一处，材质参数（折射/高光/着色）已经没有承载体，整组撤掉。
  { title: '几何 · 连续曲率圆角', group: 'geometry', keys: [
    ['radius', '圆角半径'], ['roundness', '连续曲率 n'],
  ]},
  { title: '导航 · 玻璃', group: 'nav', keys: [
    ['height', '导航高度'], ['navEdge', '边缘厚度'],
    ['navRefraction', '折射强度'], ['lensWidth', '透镜宽度'],
    ['lensStrength', '透镜强度'], ['navBlur', '导航模糊'],
    ['navFill', '填充浓度'], ['navSaturation', '饱和度'],
  ]},
];

let glassApi = null;
let tunerInitial = null;

function tunerBounds(key) {
  if (key === 'roundness') return ROUNDNESS_BOUNDS;
  return glassApi.GLASS_LIMITS[key] || [0, 1, .01];
}

function tunerValue(cfg, group, key) {
  return group === 'geometry' ? cfg.geometry.card[key] : cfg[group][key];
}

function tunerPatch(group, key, v) {
  if (group !== 'geometry') return { [group]: { [key]: v } };
  // geometry 有 card/dense/showcase 三组，全局统一，一次写齐
  const g = {};
  for (const kind of ['card', 'dense', 'showcase']) g[kind] = { [key]: v };
  return { geometry: g };
}

function tunerRead() {
  try { return JSON.parse(localStorage.getItem(TUNER_LS) || '{}'); } catch (e) { return {}; }
}

function tunerSave(patch) {
  try { localStorage.setItem(TUNER_LS, JSON.stringify(patch)); } catch (e) { /* 隐私模式 */ }
}

function tunerFormat(key, v, step) {
  if (key === 'tint') return v;
  if (key === 'blurEdge') return (+v ? '开' : '关');
  return (+v).toFixed(step >= 1 ? 0 : 2);
}

async function initTuner() {
  const box = $('#tunerGroups');
  if (!box) return;
  try {
    glassApi = await import('./glass/liquid-glass.js');
  } catch (e) {
    box.innerHTML = '<p class="cand-tip">玻璃引擎未加载：' + esc(e.message) + '</p>';
    return;
  }
  tunerInitial = glassApi.getGlassConfig();   // 页面 #glass-config 应用后的初值
  const cfg = tunerInitial;
  box.innerHTML = '';

  // 拖动中的 localStorage 写入防抖：以前每次 input 都同步写一次，拖滑块每秒几十次
  let pendingSave = null, saveTimer = 0;

  for (const grp of TUNER_GROUPS) {
    const target = box;
    const h = document.createElement('div');
    h.className = 'tuner-title';
    h.textContent = grp.title;
    target.appendChild(h);
    for (const [key, label] of grp.keys) {
      const [min, max, step] = tunerBounds(key);
      const isColor = key === 'tint';
      const isBool = key === 'blurEdge';
      const id = 'tuner_' + grp.group + '_' + key;
      let cur = tunerValue(cfg, grp.group, key);
      if (isBool) cur = cur ? 1 : 0;
      const row = document.createElement('div');
      row.className = 'tuner-row';
      row.innerHTML =
        '<label for="' + id + '">' + esc(label) + '</label>' +
        (isColor
          ? '<input type="color" id="' + id + '" value="' + esc(cur) + '">'
          : '<input type="range" id="' + id + '" min="' + min + '" max="' + max +
            '" step="' + (isBool ? 1 : step) + '" value="' + cur + '">') +
        '<output for="' + id + '"></output>';
      const input = row.querySelector('input');
      const out = row.querySelector('output');
      out.textContent = tunerFormat(key, cur, step);
      // 几何的形与曲率同时喂给 CSS（corner-shape）与 shader，拖动时两者同步变化。
      input.addEventListener('input', () => {
        const v = isBool ? (input.value === '1') : (isColor ? input.value : +input.value);
        out.textContent = tunerFormat(key, input.value, step);
        glassApi.setGlassConfig(tunerPatch(grp.group, key, v));
        pendingSave = Object.assign(pendingSave || {}, { [grp.group + '.' + key]: v });
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          tunerSave(Object.assign(tunerRead(), pendingSave));
          pendingSave = null;
        }, 300);
      });
      target.appendChild(row);
    }
  }
  $('#tunerCount').textContent =
    TUNER_GROUPS.reduce((n, g) => n + g.keys.length, 0) + ' 项 · 全局统一';

  // 恢复上次调过的值（刷新后仍在）
  const saved = tunerRead();
  for (const k of Object.keys(saved)) {
    const [group, key] = k.split('.');
    const el = document.getElementById('tuner_' + group + '_' + key);
    if (!el) continue;
    const v = saved[k];
    el.value = (key === 'blurEdge') ? (v ? 1 : 0) : v;
    el.dispatchEvent(new Event('input'));
  }
  // 恢复值走的是 input 事件，geometry 的 CSS 被 defer 了，这里补一次应用
  glassApi.setGlassConfig({});

  $('#tunerReset').addEventListener('click', () => {
    localStorage.removeItem(TUNER_LS);
    glassApi.setGlassConfig(tunerInitial);
    const c0 = glassApi.getGlassConfig();
    for (const grp of TUNER_GROUPS) for (const [key] of grp.keys) {
      const el = document.getElementById('tuner_' + grp.group + '_' + key);
      if (!el) continue;
      let v = tunerValue(c0, grp.group, key);
      if (key === 'blurEdge') v = v ? 1 : 0;
      el.value = v;
      const [, , st] = tunerBounds(key);
      el.closest('.tuner-row').querySelector('output').textContent = tunerFormat(key, v, st);
    }
    con.status('已恢复页面默认参数', 'ok');
  });

  $('#tunerCopy').addEventListener('click', () => {
    copyAndSay(JSON.stringify(glassApi.getGlassConfig(), null, 2), '已复制当前引擎配置 JSON');
  });
  glassDirty();
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
    if (p === 'feed' || p === 'wall' || p === 'fav') setPage(p, false);
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
  $('#lbFav').addEventListener('click', toggleFav);
  $('#lbBg').addEventListener('click', () => {
    const it = state.lb[state.lbIndex];
    if (it) setBg(imgUrl(it.key, 'orj1080'), (it.owner || '壁纸') + ' · ' + it.key);
  });
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
  applyBg();          // 必须早于 defer 的玻璃 module 初始化
  bindBg();
  bindNav();
  bindConsole();
  bindLb();
  bindInfinite();
  bindPicker();

  const hashed = location.hash.replace('#', '');
  const start = (hashed === 'wall' || hashed === 'feed' || hashed === 'fav') ? hashed : 'feed';

  // 有 PAT 就先问一下当前索引对应的 commit：按 commit 拉的索引不会被 CDN 的
  // 5 分钟缓存拖住，首屏看到的就是仓库里的最新数据。失败就退化成按分支拉。
  (async () => {
    if (getPat()) {
      try {
        const c = await gh('/repos/' + REPO + '/commits?sha=' + encodeURIComponent(DISPATCH_REF) + '&per_page=1');
        state.indexSha = (c && c[0] && c[0].sha) || '';
      } catch (e) { /* 不影响正常浏览 */ }
    }
    setPage(start, false);
  })();

  favAll()
    .then((a) => { state.favItems = a || []; if (state.page === 'fav') renderFav(); })
    .catch(() => {});

  fetch('data.json', { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d) return;
      $('#footStat').textContent =
        '壁纸 ' + d.count + ' 张 · 索引 ' + (d.generated_at || '');
    })
    .catch(() => {});
})();
