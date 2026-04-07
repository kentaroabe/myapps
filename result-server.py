#!/usr/bin/env python3
"""
翻訳チェック結果 保存・共有サーバー
translation-checker.html の結果をURLで共有するためのサーバーです。

【起動方法】
  python result-server.py

【停止方法】
  Ctrl + C

結果URL例 : http://localhost:3002/r/{id}
管理ページ : http://localhost:3002/admin
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, uuid, os, datetime, hashlib, sys, secrets, string
from urllib.parse import urlparse, parse_qs

PORT        = 3002
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'saved_results')
DB_FILE     = os.path.join(BASE_DIR, 'results_db.json')
CONFIG_FILE = os.path.join(BASE_DIR, 'server-config.json')

EXPIRE_DAYS = 30

CORS_HEADERS = [
    ('Access-Control-Allow-Origin',  '*'),
    ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
    ('Access-Control-Allow-Headers', 'Content-Type'),
    ('Access-Control-Max-Age',       '86400'),
]

os.makedirs(RESULTS_DIR, exist_ok=True)


# ── Config (admin password) ───────────────────────────────────

def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 初回起動: ランダムパスワードを生成して保存
    chars    = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(chars) for _ in range(14))
    config   = {'admin_password': password}
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    print('\n  ========================================')
    print('  初回起動: 管理パスワードを自動生成しました')
    print(f'  パスワード: {password}')
    print('  ※ server-config.json に保存済みです')
    print('     (このファイルは .gitignore で除外済み)')
    print('  ========================================\n')
    return config

_config       = load_or_create_config()
ADMIN_PW_HASH = hashlib.sha256(_config['admin_password'].encode()).hexdigest()


# ── DB helpers ───────────────────────────────────────────────

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def purge_expired(db):
    now = datetime.datetime.utcnow().isoformat()
    expired = [k for k, v in db.items() if v['expires_at'] < now]
    for k in expired:
        path = os.path.join(RESULTS_DIR, k + '.html')
        if os.path.exists(path):
            os.remove(path)
        del db[k]
    if expired:
        save_db(db)
    return len(expired)


# ── Request handler ──────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        for k, v in CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        if path.startswith('/r/'):
            self._serve_result(path[3:])
        elif path == '/admin':
            pw = qs.get('pw', [''])[0]
            if hashlib.sha256(pw.encode()).hexdigest() == ADMIN_PW_HASH:
                self._serve_admin_dashboard(pw)
            else:
                self._serve_admin_login(error=(pw != ''))
        else:
            self._send_html(404, '<h2 style="font-family:sans-serif">404 Not Found</h2>')

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)

        if path == '/api/save':
            self._handle_save(body)
        elif path == '/admin/delete':
            self._handle_delete(body, qs.get('pw', [''])[0])
        else:
            self._send_json(404, {'error': 'Not found'})

    # ── Handlers ─────────────────────────────────────────────

    def _handle_save(self, body):
        try:
            data  = json.loads(body)
            title = data.get('title', '翻訳チェック結果')
            html  = data.get('html', '')
            if not html:
                self._send_json(400, {'error': 'html is empty'})
                return

            rid        = uuid.uuid4().hex[:16]
            now        = datetime.datetime.utcnow()
            expires_at = (now + datetime.timedelta(days=EXPIRE_DAYS)).isoformat()

            with open(os.path.join(RESULTS_DIR, rid + '.html'), 'w', encoding='utf-8') as f:
                f.write(html)

            db = load_db()
            db[rid] = {'title': title, 'created_at': now.isoformat(), 'expires_at': expires_at}
            save_db(db)

            self._send_json(200, {
                'id':         rid,
                'url':        f'http://localhost:{PORT}/r/{rid}',
                'expires_at': expires_at,
            })
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_delete(self, body, pw):
        if hashlib.sha256(pw.encode()).hexdigest() != ADMIN_PW_HASH:
            self._send_json(403, {'error': 'Unauthorized'})
            return
        try:
            rid = json.loads(body).get('id', '')
            db  = load_db()
            if rid not in db:
                self._send_json(404, {'error': 'Not found'})
                return
            del db[rid]
            save_db(db)
            path = os.path.join(RESULTS_DIR, rid + '.html')
            if os.path.exists(path):
                os.remove(path)
            self._send_json(200, {'ok': True})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _serve_result(self, rid):
        db = load_db()
        if rid not in db:
            self._send_html(404, self._wrap('404 - 結果が見つかりません',
                '<p>URLが無効か、有効期限が切れています。</p>'))
            return
        entry = db[rid]
        if entry['expires_at'] < datetime.datetime.utcnow().isoformat():
            self._send_html(410, self._wrap('410 - 有効期限切れ',
                '<p>この結果の有効期限（保存から30日）が切れています。</p>'))
            return
        path = os.path.join(RESULTS_DIR, rid + '.html')
        if not os.path.exists(path):
            self._send_html(404, self._wrap('404 - ファイルが見つかりません', ''))
            return
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        self._send_raw(200, 'text/html; charset=utf-8', content.encode('utf-8'))

    def _serve_admin_login(self, error=False):
        err = '<p style="color:#dc2626;font-weight:700;margin:0 0 12px;">パスワードが違います。</p>' if error else ''
        html = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理ページ</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f1f5f9}}
  .box{{background:#fff;border-radius:14px;padding:36px 44px;box-shadow:0 4px 24px rgba(0,0,0,.1);min-width:320px}}
  h2{{margin:0 0 20px;font-size:19px}}
  input{{width:100%;padding:9px 12px;border:1px solid #d1d5db;border-radius:7px;font-size:14px;box-sizing:border-box;margin-top:6px}}
  button{{margin-top:14px;width:100%;padding:10px;background:#4f46e5;color:#fff;border:none;border-radius:7px;font-size:14px;font-weight:700;cursor:pointer}}
  button:hover{{background:#4338ca}}
  label{{font-size:13px;font-weight:600}}
</style></head>
<body><div class="box">
  <h2>🔒 管理ページ</h2>
  {err}
  <form method="get" action="/admin">
    <label>パスワード</label>
    <input type="password" name="pw" placeholder="パスワードを入力" autofocus>
    <button type="submit">ログイン</button>
  </form>
</div></body></html>'''
        self._send_html(200, html)

    def _serve_admin_dashboard(self, pw):
        db = load_db()
        purged = purge_expired(db)
        rows = ''
        for rid, e in sorted(db.items(), key=lambda x: x[1]['created_at'], reverse=True):
            created  = e['created_at'][:10]
            expires  = e['expires_at'][:10]
            url      = f'http://localhost:{PORT}/r/{rid}'
            rows += f'''<tr>
              <td>{e["title"]}</td>
              <td style="white-space:nowrap">{created}</td>
              <td style="white-space:nowrap">{expires}</td>
              <td><a href="{url}" target="_blank">{url}</a></td>
              <td><button onclick="del('{rid}')">🗑 削除</button></td>
            </tr>'''
        if not rows:
            rows = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:28px;">保存された結果はありません</td></tr>'

        purged_note = f'<span style="color:#94a3b8;font-size:12px;">（期限切れ {purged} 件を自動削除）</span>' if purged else ''
        html = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理ページ - 結果一覧</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:system-ui,sans-serif;margin:0;padding:28px;background:#f1f5f9;color:#1e293b}}
  h1{{font-size:20px;margin:0 0 20px}}
  .card{{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  .meta{{font-size:13px;color:#64748b;margin-bottom:14px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;font-size:13px}}
  th{{font-size:11px;text-transform:uppercase;color:#64748b;font-weight:700;background:#f8fafc}}
  tr:hover td{{background:#f8fafc}}
  a{{color:#4f46e5;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  button{{padding:4px 10px;border:1px solid #fca5a5;background:#fff;color:#dc2626;border-radius:5px;cursor:pointer;font-size:12px}}
  button:hover{{background:#fef2f2}}
</style></head>
<body>
<h1>📋 保存済み翻訳チェック結果</h1>
<div class="card">
  <div class="meta">全 {len(db)} 件 {purged_note}</div>
  <table>
    <thead><tr><th>タイトル</th><th>保存日</th><th>有効期限</th><th>URL</th><th>操作</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<script>
async function del(id){{
  if(!confirm('この結果を削除しますか？'))return;
  const pw=new URLSearchParams(location.search).get('pw')||'';
  const r=await fetch('/admin/delete?pw='+encodeURIComponent(pw),{{
    method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{id}})
  }});
  if(r.ok)location.reload();else alert('削除に失敗しました');
}}
</script>
</body></html>'''
        self._send_html(200, html)

    # ── Low-level senders ─────────────────────────────────────

    def _wrap(self, title, body):
        return f'''<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;padding:48px;max-width:600px;margin:auto;color:#1e293b}}</style></head>
<body><h2>{title}</h2>{body}<p><a href="javascript:history.back()">← 戻る</a></p></body></html>'''

    def _send_html(self, status, html):
        self._send_raw(status, 'text/html; charset=utf-8', html.encode('utf-8'))

    def _send_json(self, status, data):
        self._send_raw(status, 'application/json', json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _send_raw(self, status, content_type, body_bytes):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body_bytes)))
        for k, v in CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, fmt, *args):
        print(f'  [{self.address_string()}] {fmt % args}')


# ── Entry point ───────────────────────────────────────────────

def main():
    server = HTTPServer(('localhost', PORT), Handler)
    print('=' * 52)
    print(f'  翻訳チェック結果サーバー 起動完了')
    print(f'  結果URL : http://localhost:{PORT}/r/{{id}}')
    print(f'  管理    : http://localhost:{PORT}/admin')
    print(f'  停止    : Ctrl + C')
    print('=' * 52)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました。')
        sys.exit(0)

if __name__ == '__main__':
    main()
