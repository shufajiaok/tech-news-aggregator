/**
 * TechPulse — Cloudflare Pages Advanced Mode Worker
 *
 * 职责:
 *   /api/*  → 代理到 Supabase REST API（解决国内直连不稳定的问题）
 *   其他路径  → 从 frontend/ 目录提供静态文件
 */

const SUPABASE_URL = 'https://spzerhlpmzsvzwbhrdmz.supabase.co';
const SUPABASE_KEY = 'sb_publishable_j9UNOuDWNeJrG9cCAZp2IQ_vryI02cy';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ── API 代理: /api/* → Supabase ──────────────
    if (path.startsWith('/api/')) {
      const apiPath = path.replace(/^\/api/, '') + url.search;
      const targetUrl = `${SUPABASE_URL}${apiPath}`;

      const headers = new Headers(request.headers);
      headers.set('apikey', SUPABASE_KEY);
      headers.set('Authorization', `Bearer ${SUPABASE_KEY}`);
      headers.delete('host');
      headers.set('Origin', SUPABASE_URL);

      try {
        const resp = await fetch(targetUrl, {
          method: request.method,
          headers,
          body: ['GET', 'HEAD'].includes(request.method) ? null : request.body,
          redirect: 'follow',
        });

        const responseHeaders = new Headers(resp.headers);
        responseHeaders.set('Access-Control-Allow-Origin', '*');
        responseHeaders.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
        responseHeaders.set('Access-Control-Allow-Headers', '*');

        return new Response(resp.body, {
          status: resp.status,
          statusText: resp.statusText,
          headers: responseHeaders,
        });
      } catch (err) {
        return new Response(
          JSON.stringify({ error: 'Proxy error', detail: err.message }),
          {
            status: 502,
            headers: {
              'Content-Type': 'application/json',
              'Access-Control-Allow-Origin': '*',
            },
          }
        );
      }
    }

    // ── 静态文件: 映射到 frontend/ 目录 ──────────
    let filePath = path;
    if (filePath === '/') filePath = '/index.html';

    const assetRequest = new Request(
      new URL(`/frontend${filePath}`, request.url),
      request
    );

    try {
      return await env.ASSETS.fetch(assetRequest);
    } catch (e) {
      // SPA fallback: 未匹配路由返回首页
      const fallbackRequest = new Request(
        new URL('/frontend/index.html', request.url),
        request
      );
      return env.ASSETS.fetch(fallbackRequest);
    }
  },
};