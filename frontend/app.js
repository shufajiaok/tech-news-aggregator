/**
 * TechPulse — 科技新闻聚合前端
 * 直接查询 Supabase REST API，无需自建后端
 * 适配 Cloudflare Pages 部署
 */

// ── Supabase 配置 ──────────────────────────────────
const SUPABASE_URL = 'https://spzerhlpmzsvzwbhrdmz.supabase.co';
const SUPABASE_KEY = 'sb_publishable_j9UNOuDWNeJrG9cCAZp2IQ_vryI02cy';
const PAGE_SIZE = 20;

// ── 状态 ───────────────────────────────────────────
const state = {
    category: '',
    page: 0,
    news: [],
    hasMore: true,
};

// ── DOM引用 ────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const newsGrid = $('#newsGrid');
const loading = $('#loading');
const errorEl = $('#error');
const emptyEl = $('#empty');
const pagination = $('#pagination');
const prevBtn = $('#prevBtn');
const nextBtn = $('#nextBtn');
const pageInfo = $('#pageInfo');
const categoryNav = $('#categoryNav');
const modalOverlay = $('#modalOverlay');
const modalContent = $('#modalContent');
const modalClose = $('#modalClose');

// ── 主题切换 ───────────────────────────────────────
const themeToggle = $('#themeToggle');
const THEME_KEY = 'techpulse-theme';

function getTheme() {
    return localStorage.getItem(THEME_KEY) || 'dark';
}

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    themeToggle.textContent = theme === 'light' ? '☀️' : '🌙';
    localStorage.setItem(THEME_KEY, theme);
}

// 初始化主题
setTheme(getTheme());

themeToggle.addEventListener('click', () => {
    const next = getTheme() === 'dark' ? 'light' : 'dark';
    setTheme(next);
});

// ── 工具函数 ──────────────────────────────────────
function formatTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const diff = now - d;
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`;
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

function categoryClass(cat) {
    const map = {
        AI: 'cat-AI', GPU: 'cat-GPU', CPU: 'cat-CPU',
        Foundry: 'cat-Foundry', Semiconductor: 'cat-Semiconductor',
        Digital: 'cat-Digital',
    };
    return map[cat] || 'cat-AI';
}

function categoryIcon(cat) {
    const map = {
        AI: '🤖', GPU: '🎮', CPU: '💻',
        Foundry: '🏭', Semiconductor: '🔬', Digital: '📱',
    };
    return map[cat] || '📌';
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function getCardSummary(n) {
    const text = n.ai_summary || n.summary || '';
    if (text.length <= 150) return escapeHtml(text);
    return escapeHtml(text.slice(0, 150)) + '…';
}

// ── Supabase REST API 调用 ──────────────────────────
const supabaseHeaders = {
    'apikey': SUPABASE_KEY,
    'Authorization': `Bearer ${SUPABASE_KEY}`,
};

async function fetchNews(category, page) {
    const offset = page * PAGE_SIZE;
    let url = `${SUPABASE_URL}/rest/v1/tech_news?select=*&order=published_at.desc&limit=${PAGE_SIZE}&offset=${offset}`;
    if (category) {
        url += `&category=eq.${encodeURIComponent(category)}`;
    }
    const resp = await fetch(url, { headers: supabaseHeaders });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    return { data };
}

async function fetchNewsById(id) {
    const url = `${SUPABASE_URL}/rest/v1/tech_news?id=eq.${encodeURIComponent(id)}&select=*`;
    const resp = await fetch(url, { headers: supabaseHeaders });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    return { data: data[0] || null };
}

// ── 渲染 ───────────────────────────────────────────
function renderCards(newsList) {
    if (newsList.length === 0) {
        newsGrid.innerHTML = '';
        return;
    }
    newsGrid.innerHTML = newsList.map(n => `
        <article class="news-card" data-id="${n.id}" onclick="openDetail('${n.id}')">
            <div class="card-header">
                <span class="card-category ${categoryClass(n.category)}">${categoryIcon(n.category)} ${n.category}</span>
                <span class="card-time">${formatTime(n.published_at)}</span>
            </div>
            <h3 class="card-title">${escapeHtml(n.title)}</h3>
            <p class="card-summary">${getCardSummary(n)}</p>
            <div class="card-meta">
                <span class="card-source">
                    <span class="card-source-avatar">${n.original_author ? n.original_author.charAt(1).toUpperCase() : 'X'}</span>
                    ${escapeHtml(n.source)}
                </span>
                <span>${escapeHtml(n.original_author || '')}</span>
            </div>
        </article>
    `).join('');
}

function updatePagination() {
    if (state.news.length === 0 && state.page === 0) {
        pagination.style.display = 'none';
        return;
    }
    pagination.style.display = 'flex';
    prevBtn.disabled = state.page === 0;
    nextBtn.disabled = state.news.length < PAGE_SIZE;
    pageInfo.textContent = `第 ${state.page + 1} 页`;
}

// ── 加载流程 ───────────────────────────────────────
async function loadNews() {
    loading.style.display = 'flex';
    errorEl.style.display = 'none';
    emptyEl.style.display = 'none';
    newsGrid.innerHTML = '';

    try {
        const result = await fetchNews(state.category, state.page);
        state.news = result.data || [];
        state.hasMore = state.news.length >= PAGE_SIZE;
        loading.style.display = 'none';

        if (state.news.length === 0) {
            emptyEl.style.display = 'block';
            pagination.style.display = 'none';
        } else {
            renderCards(state.news);
            updatePagination();
        }
    } catch (err) {
        loading.style.display = 'none';
        errorEl.style.display = 'block';
        console.error('Failed to load news:', err);
    }
}

function switchCategory(category) {
    state.category = category;
    state.page = 0;
    state.news = [];
    $$('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.category === category);
    });
    loadNews();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── 详情弹窗 ───────────────────────────────────────
async function openDetail(id) {
    modalOverlay.classList.add('active');
    modalContent.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    document.body.style.overflow = 'hidden';

    try {
        const result = await fetchNewsById(id);
        const n = result.data;
        if (!n) {
            modalContent.innerHTML = '<p class="error">新闻不存在</p>';
            return;
        }
        modalContent.innerHTML = `
            <span class="card-category modal-category ${categoryClass(n.category)}">${categoryIcon(n.category)} ${n.category}</span>
            <h2 class="modal-title">${escapeHtml(n.title)}</h2>
            ${n.ai_summary ? `
            <div class="modal-ai-summary">
                <h3>🤖 AI 深度总结</h3>
                <div class="ai-summary-text">${escapeHtml(n.ai_summary).replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>')}</div>
            </div>` : ''}
            ${n.key_points && n.key_points.length ? `
            <div class="modal-keypoints">
                <h3>📋 关键要点</h3>
                <ul>
                    ${n.key_points.map(kp => `<li>${escapeHtml(kp)}</li>`).join('')}
                </ul>
            </div>` : ''}
            <div class="modal-footer">
                <span>来源: ${escapeHtml(n.source)} ${escapeHtml(n.original_author || '')}</span>
                <span>${formatTime(n.published_at)}</span>
                <a href="${escapeHtml(n.source_url)}" target="_blank" rel="noopener" class="modal-source-link">
                    🔗 查看原文
                </a>
            </div>
        `;
    } catch (err) {
        modalContent.innerHTML = '<p class="error">加载失败</p>';
        console.error('Failed to load detail:', err);
    }
}

function closeModal() {
    modalOverlay.classList.remove('active');
    document.body.style.overflow = '';
}

// ── 事件绑定 ───────────────────────────────────────
categoryNav.addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-item');
    if (!btn) return;
    switchCategory(btn.dataset.category);
});

prevBtn.addEventListener('click', () => {
    if (state.page > 0) {
        state.page--;
        loadNews();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
});

nextBtn.addEventListener('click', () => {
    if (state.hasMore) {
        state.page++;
        loadNews();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
});

modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
});

// ── 启动 ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadNews();
});