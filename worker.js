/**
 * Cloudflare Worker: Claude API CORS プロキシ
 *
 * 【デプロイ手順 A：ダッシュボードのエディタに貼り付ける（推奨）】
 * 1. https://dash.cloudflare.com → Workers & Pages → Create → Create Worker
 * 2. 「Hello World」テンプレートで Worker を作成
 * 3. 作成後「Edit Code」をクリック
 * 4. エディタの内容をすべて消してこのファイルの内容を貼り付け
 * 5. 「Save and Deploy」をクリック
 *
 * 【デプロイ手順 B：ファイルアップロード】
 * Workers & Pages → Create → Upload and deploy → このファイルをアップロード → Deploy
 */

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {

  // CORS プリフライト（OPTIONS）への応答
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: corsHeaders()
    });
  }

  // POST のみ受け付ける
  if (request.method !== 'POST') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  try {
    var url     = new URL(request.url);
    var target  = 'https://api.anthropic.com' + url.pathname + url.search;
    var apiKey  = request.headers.get('x-api-key')        || '';
    var version = request.headers.get('anthropic-version') || '2023-06-01';
    var body    = await request.text();

    // Anthropic API へ転送
    var upstream = await fetch(target, {
      method: 'POST',
      headers: {
        'Content-Type':      'application/json',
        'x-api-key':         apiKey,
        'anthropic-version': version
      },
      body: body
    });

    var responseBody = await upstream.text();

    return new Response(responseBody, {
      status: upstream.status,
      headers: Object.assign({ 'Content-Type': 'application/json' }, corsHeaders())
    });

  } catch (err) {
    return jsonResponse({ error: { type: 'proxy_error', message: err.message } }, 500);
  }
}

function jsonResponse(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: Object.assign({ 'Content-Type': 'application/json' }, corsHeaders())
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, x-api-key, anthropic-version',
    'Access-Control-Max-Age':       '86400'
  };
}
