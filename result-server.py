#!/usr/bin/env python3
"""
翻訳チェック結果 保存・共有サーバー
translation-checker.html の結果をURLで共有するためのサーバーです。

【ローカル起動】
  python result-server.py
  → http://localhost:3002/admin  で管理ページを開く

【クラウドデプロイ (Railway / Render 等)】
  環境変数 ADMIN_PASSWORD に管理パスワードを設定してください。
  例: ADMIN_PASSWORD=yourpassword

  Railway: railway.toml で自動検出、または Procfile を使用
  Render : "Start Command" に  python result-server.py  を設定

【管理ページ】
  https://あなたのドメイン/admin
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, uuid, os, datetime, hashlib, sys, secrets, string, sqlite3
from urllib.parse import urlparse, parse_qs

# ── 設定 ─────────────────────────────────────────────────────

PORT        = int(os.environ.get('PORT', 3002))
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_FILE     = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'results.db'))
CONFIG_FILE = os.path.join(BASE_DIR, 'server-config.json')
EXPIRE_DAYS = 30

CORS_HEADERS = [
    ('Access-Control-Allow-Origin',  '*'),
    ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
    ('Access-Control-Allow-Headers', 'Content-Type'),
    ('Access-Control-Max-Age',       '86400'),
]


# ── 管理パスワード ────────────────────────────────────────────

def load_or_create_config():
    # クラウド環境変数を優先
    env_pw = os.environ.get('ADMIN_PASSWORD', '').strip()
    if env_pw:
        return {'admin_password': env_pw}

    # ローカル: 設定ファイルから読み込み
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    # ローカル初回: ランダムパスワードを自動生成
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


# ── SQLite ────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS results (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            html       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )''')

def db_save(rid, title, html, created_at, expires_at):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('INSERT INTO results VALUES (?,?,?,?,?)',
                     (rid, title, html, created_at, expires_at))

def db_get(rid):
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            'SELECT id,title,html,created_at,expires_at FROM results WHERE id=?', (rid,)
        ).fetchone()
    if row:
        return dict(zip(['id','title','html','created_at','expires_at'], row))
    return None

def db_list():
    now = datetime.datetime.utcnow().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('DELETE FROM results WHERE expires_at < ?', (now,))
        rows = conn.execute(
            'SELECT id,title,created_at,expires_at FROM results ORDER BY created_at DESC'
        ).fetchall()
    return [dict(zip(['id','title','created_at','expires_at'], r)) for r in rows]

def db_delete(rid):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('DELETE FROM results WHERE id=?', (rid,))

init_db()


# ── リクエストハンドラ ─────────────────────────────────────────

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
        elif path == '/health':
            self._send_json(200, {'status': 'ok'})
        else:
            self._send_html(404, self._wrap('404 Not Found', '<p>ページが見つかりません。</p>'))

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

    # ── ハンドラ実装 ──────────────────────────────────────────

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

            db_save(rid, title, html, now.isoformat(), expires_at)

            # 発行URLはリクエストの Host ヘッダを使って自動生成
            host     = self.headers.get('Host', f'localhost:{PORT}')
            scheme   = 'https' if not host.startswith('localhost') else 'http'
            base_url = f'{scheme}://{host}'

            self._send_json(200, {
                'id':         rid,
                'url':        f'{base_url}/r/{rid}',
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
            db_delete(rid)
            self._send_json(200, {'ok': True})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _serve_result(self, rid):
        entry = db_get(rid)
        if not entry:
            self._send_html(404, self._wrap('404 - 結果が見つかりません',
                '<p>URLが無効か、有効期限が切れています。</p>'))
            return
        if entry['expires_at'] < datetime.datetime.utcnow().isoformat():
            self._send_html(410, self._wrap('有効期限切れ',
                '<p>この結果の有効期限（保存から30日）が切れています。</p>'))
            return
        self._send_raw(200, 'text/html; charset=utf-8', entry['html'].encode('utf-8'))

    def _serve_admin_login(self, error=False):
        err = ('<p style="color:#dc2626;font-weight:700;margin:0 0 12px;">'
               'パスワードが違います。</p>') if error else ''
        html = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理ページ</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;
       justify-content:center;min-height:100vh;margin:0;background:#f1f5f9}}
  .box{{background:#fff;border-radius:14px;padding:36px 44px;
        box-shadow:0 4px 24px rgba(0,0,0,.1);min-width:320px}}
  h2{{margin:0 0 20px;font-size:19px}}
  input{{width:100%;padding:9px 12px;border:1px solid #d1d5db;border-radius:7px;
         font-size:14px;box-sizing:border-box;margin-top:6px}}
  button{{margin-top:14px;width:100%;padding:10px;background:#4f46e5;color:#fff;
          border:none;border-radius:7px;font-size:14px;font-weight:700;cursor:pointer}}
  button:hover{{background:#4338ca}}label{{font-size:13px;font-weight:600}}
</style></head>
<body><div class="box">
  <h2>🔒 管理ページ</h2>{err}
  <form method="get" action="/admin">
    <label>パスワード</label>
    <input type="password" name="pw" placeholder="パスワードを入力" autofocus>
    <button type="submit">ログイン</button>
  </form>
</div></body></html>'''
        self._send_html(200, html)

    def _serve_admin_dashboard(self, pw):
        results = db_list()
        rows = ''
        for e in results:
            created = e['created_at'][:10]
            expires = e['expires_at'][:10]
            host    = self.headers.get('Host', f'localhost:{PORT}')
            scheme  = 'https' if not host.startswith('localhost') else 'http'
            url     = f'{scheme}://{host}/r/{e["id"]}'
            rows += f'''<tr>
              <td>{e["title"]}</td>
              <td style="white-space:nowrap">{created}</td>
              <td style="white-space:nowrap">{expires}</td>
              <td><a href="{url}" target="_blank"
                     style="word-break:break-all">{url}</a></td>
              <td><button onclick="del('{e["id"]}')">🗑</button></td>
            </tr>'''
        if not rows:
            rows = ('<tr><td colspan="5" style="text-align:center;color:#94a3b8;'
                    'padding:28px;">保存された結果はありません</td></tr>')
        html = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理ページ - 結果一覧</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:system-ui,sans-serif;margin:0;padding:28px;
       background:#f1f5f9;color:#1e293b}}
  h1{{font-size:20px;margin:0 0 20px}}
  .card{{background:#fff;border-radius:12px;padding:24px;
         box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  .meta{{font-size:13px;color:#64748b;margin-bottom:14px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:10px 12px;text-align:left;
         border-bottom:1px solid #e2e8f0;font-size:13px}}
  th{{font-size:11px;text-transform:uppercase;color:#64748b;
      font-weight:700;background:#f8fafc}}
  tr:hover td{{background:#f8fafc}}
  a{{color:#4f46e5;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  button{{padding:4px 10px;border:1px solid #fca5a5;background:#fff;
          color:#dc2626;border-radius:5px;cursor:pointer;font-size:12px}}
  button:hover{{background:#fef2f2}}
</style></head>
<body>
<h1>📋 保存済み翻訳チェック結果一覧</h1>
<div class="card">
  <div class="meta">全 {len(results)} 件（有効期限切れは自動削除）</div>
  <table>
    <thead>
      <tr><th>タイトル</th><th>保存日</th><th>有効期限</th>
          <th>URL</th><th>削除</th></tr>
    </thead>
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
  if(r.ok)location.reload();
  else alert('削除に失敗しました');
}}
</script>
</body></html>'''
        self._send_html(200, html)

    # ── 送信ヘルパー ──────────────────────────────────────────

    def _wrap(self, title, body):
        return (f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
                f'<title>{title}</title><style>body{{font-family:system-ui,sans-serif;'
                f'padding:48px;max-width:600px;margin:auto}}</style></head>'
                f'<body><h2>{title}</h2>{body}</body></html>')

    def _send_html(self, status, html):
        self._send_raw(status, 'text/html; charset=utf-8', html.encode('utf-8'))

    def _send_json(self, status, data):
        self._send_raw(status, 'application/json',
                       json.dumps(data, ensure_ascii=False).encode('utf-8'))

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


# ── エントリポイント ──────────────────────────────────────────

def main():
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print('=' * 52)
    print(f'  翻訳チェック結果サーバー 起動完了')
    print(f'  ポート : {PORT}')
    print(f'  管理   : http://localhost:{PORT}/admin')
    print(f'  停止   : Ctrl + C')
    print('=' * 52)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました。')
        sys.exit(0)

if __name__ == '__main__':
    main()
