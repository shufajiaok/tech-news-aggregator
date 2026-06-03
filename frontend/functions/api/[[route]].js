/**
 * Cloudflare Pages Function — Supabase API 代理
 * 浏览器 → Cloudflare → Supabase，解决国内直连AWS不稳定的问题
 *
 * 前端请求 /api/rest/v1/tech_news → 本函数转发到 Supabase
 */

const SUPABASE_URL = 'https://spzerhlpmzsvzwbhrdmz.supabase.co';
const SUPABASE_KEY = 'sb_publishable_j9UNOuDWNeJrG9cCAZp2IQ_vryI02cy';

export async function onRequest(context) {
  const { request } = context;
  const url = new URL(request.url);

  // 提取 /api/ 后面的路径，拼接 Supabase URL
  const path = url.pathname.replace(/^\/api/, '') + url.search;
  const targetUrl = `${SUPABASE_URL}${path}`;

  // 构建转发请求
  const headers = new Headers(request.headers);
  headers.set('apikey', SUPABASE_KEY);
  headers.set('Authorization', `Bearer ${SUPABASE_KEY}`);
  // 删除浏览器自动添加的 host/origin
  headers.delete('host');
  headers.set('Origin', SUPABASE_URL);

  const proxyRequest = new Request(targetUrl, {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? null : request.body,
    redirect: 'follow',
  });

  try {
    const resp = await fetch(proxyRequest);

    // 返回响应，追加 CORS 头
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
    return new Response(JSON.stringify({ error: 'Proxy error', detail: err.message }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }
}