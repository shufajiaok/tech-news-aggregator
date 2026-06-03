/**
 * TechPulse — Cloudflare Pages Advanced Mode Worker
 *
 * 静态文件由 Pages 自动从输出目录提供（无需手动处理）
 * 仅处理 /api/* → Supabase 代理
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
            headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
          }
        );
      }
    }

    // 非 API 请求 → Pages 自动提供静态文件
    return env.ASSETS.fetch(request);
  },
};