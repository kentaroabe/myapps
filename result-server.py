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
import json, uuid, os, datetime, hashlib, sys, secrets, string
from urllib.parse import urlparse, parse_qs

# ── 設定 ─────────────────────────────────────────────────────

PORT        = int(os.environ.get('PORT', 3002))
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'server-config.json')
EXPIRE_DAYS = 30

# DATABASE_URL が設定されていれば PostgreSQL、なければ SQLite
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# APIコスト単価テーブル（$／100万トークン）
_MODEL_PRICING = {
    'gpt-4o':                     (2.50,  10.00),
    'gpt-4o-mini':                (0.15,  0.60),
    'gpt-4.1':                    (2.00,  8.00),
    'gpt-4.1-mini':               (0.40,  1.60),
    'gpt-4.1-nano':               (0.10,  0.40),
    'gpt-5.4':                    (2.50,  10.00),
    'gpt-5.4-mini':               (0.40,  1.60),
    'gpt-5.4-nano':               (0.10,  0.40),
    'claude-3-5-sonnet-20241022': (3.00,  15.00),
    'claude-3-5-haiku-20241022':  (0.80,  4.00),
    'claude-3-opus-20240229':     (15.00, 75.00),
    'claude-sonnet-4-6':          (3.00,  15.00),
    'claude-opus-4':              (15.00, 75.00),
    'claude-haiku-4-5':           (0.80,  4.00),
    'gemini-1.5-pro':             (1.25,  5.00),
    'gemini-1.5-flash':           (0.075, 0.30),
    'gemini-2.5-pro':             (1.25,  10.00),
    'gemini-2.5-flash':           (0.15,  0.60),
    'gemini-2.5-flash-lite':      (0.075, 0.30),
}

def _calc_cost(model, inp, out):
    p = _MODEL_PRICING.get(model)
    if not p:
        return None
    return inp / 1_000_000 * p[0] + out / 1_000_000 * p[1]

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


# ── DB 抽象化（PostgreSQL / SQLite 自動切替）────────────────────

if DATABASE_URL:
    import psycopg2, psycopg2.extras
    def _pg():
        url = DATABASE_URL
        if 'sslmode=' not in url:
            url += ('&' if '?' in url else '?') + 'sslmode=require'
        return psycopg2.connect(url)
    def init_db():
        with _pg() as conn:
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS results (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                html       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                api_cost   TEXT NOT NULL DEFAULT '{}'
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS result_files (
                id           TEXT PRIMARY KEY,
                result_id    TEXT NOT NULL,
                filename     TEXT NOT NULL,
                content_type TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT '',
                data_b64     TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )''')
            cur.execute("""
                DO $$ BEGIN
                  IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='results' AND column_name='ip_address'
                  ) THEN
                    ALTER TABLE results ADD COLUMN ip_address TEXT NOT NULL DEFAULT '';
                  END IF;
                  IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='results' AND column_name='api_cost'
                  ) THEN
                    ALTER TABLE results ADD COLUMN api_cost TEXT NOT NULL DEFAULT '{}';
                  END IF;
                END $$;
            """)
    def db_save(rid, title, html, created_at, expires_at, ip_address='', api_cost='{}'):
        with _pg() as conn:
            conn.cursor().execute(
                'INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (rid, title, html, created_at, expires_at, ip_address, api_cost))
    def db_get(rid):
        with _pg() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM results WHERE id=%s', (rid,))
            return cur.fetchone()
    def db_list():
        now = datetime.datetime.utcnow().isoformat()
        with _pg() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('DELETE FROM results WHERE expires_at < %s', (now,))
            cur.execute('SELECT id,title,created_at,expires_at,ip_address,api_cost FROM results ORDER BY created_at DESC')
            return [dict(r) for r in cur.fetchall()]
    def db_delete(rid):
        with _pg() as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM result_files WHERE result_id=%s', (rid,))
            cur.execute('DELETE FROM results WHERE id=%s', (rid,))
    def db_save_files(result_id, files, created_at):
        with _pg() as conn:
            cur = conn.cursor()
            for f in files:
                fid = uuid.uuid4().hex[:16]
                cur.execute(
                    'INSERT INTO result_files VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (fid, result_id, f['name'], f['type'], f.get('role',''), f['data_b64'], created_at))
    def db_get_files(result_id):
        with _pg() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT id,filename,content_type,role FROM result_files WHERE result_id=%s ORDER BY role,filename', (result_id,))
            return [dict(r) for r in cur.fetchall()]
    def db_get_file(file_id):
        with _pg() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT id,filename,content_type,data_b64 FROM result_files WHERE id=%s', (file_id,))
            r = cur.fetchone()
            return dict(r) if r else None
else:
    import sqlite3
    _DB = os.path.join(BASE_DIR, 'results.db')
    def init_db():
        with sqlite3.connect(_DB) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS results (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, html TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                api_cost TEXT NOT NULL DEFAULT '{}')''')
            conn.execute('''CREATE TABLE IF NOT EXISTS result_files (
                id TEXT PRIMARY KEY, result_id TEXT NOT NULL,
                filename TEXT NOT NULL, content_type TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '', data_b64 TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            cols = [r[1] for r in conn.execute('PRAGMA table_info(results)').fetchall()]
            if 'ip_address' not in cols:
                conn.execute("ALTER TABLE results ADD COLUMN ip_address TEXT NOT NULL DEFAULT ''")
            if 'api_cost' not in cols:
                conn.execute("ALTER TABLE results ADD COLUMN api_cost TEXT NOT NULL DEFAULT '{}'")
    def db_save(rid, title, html, created_at, expires_at, ip_address='', api_cost='{}'):
        with sqlite3.connect(_DB) as conn:
            conn.execute('INSERT INTO results VALUES (?,?,?,?,?,?,?)',
                         (rid, title, html, created_at, expires_at, ip_address, api_cost))
    def db_get(rid):
        with sqlite3.connect(_DB) as conn:
            row = conn.execute(
                'SELECT id,title,html,created_at,expires_at,ip_address,api_cost FROM results WHERE id=?', (rid,)
            ).fetchone()
        return dict(zip(['id','title','html','created_at','expires_at','ip_address','api_cost'], row)) if row else None
    def db_list():
        now = datetime.datetime.utcnow().isoformat()
        with sqlite3.connect(_DB) as conn:
            conn.execute('DELETE FROM results WHERE expires_at < ?', (now,))
            rows = conn.execute(
                'SELECT id,title,created_at,expires_at,ip_address,api_cost FROM results ORDER BY created_at DESC'
            ).fetchall()
        return [dict(zip(['id','title','created_at','expires_at','ip_address','api_cost'], r)) for r in rows]
    def db_delete(rid):
        with sqlite3.connect(_DB) as conn:
            conn.execute('DELETE FROM result_files WHERE result_id=?', (rid,))
            conn.execute('DELETE FROM results WHERE id=?', (rid,))
    def db_save_files(result_id, files, created_at):
        with sqlite3.connect(_DB) as conn:
            for f in files:
                fid = uuid.uuid4().hex[:16]
                conn.execute('INSERT INTO result_files VALUES (?,?,?,?,?,?,?)',
                             (fid, result_id, f['name'], f['type'], f.get('role',''), f['data_b64'], created_at))
    def db_get_files(result_id):
        with sqlite3.connect(_DB) as conn:
            rows = conn.execute(
                'SELECT id,filename,content_type,role FROM result_files WHERE result_id=? ORDER BY role,filename', (result_id,)
            ).fetchall()
        return [dict(zip(['id','filename','content_type','role'], r)) for r in rows]
    def db_get_file(file_id):
        with sqlite3.connect(_DB) as conn:
            row = conn.execute(
                'SELECT id,filename,content_type,data_b64 FROM result_files WHERE id=?', (file_id,)
            ).fetchone()
        return dict(zip(['id','filename','content_type','data_b64'], row)) if row else None


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
        elif path.startswith('/file/'):
            self._serve_file(path[6:])
        elif path == '/admin':
            pw = qs.get('pw', [''])[0]
            if hashlib.sha256(pw.encode()).hexdigest() == ADMIN_PW_HASH:
                self._serve_admin_dashboard(pw)
            else:
                self._serve_admin_login(error=(pw != ''))
        elif path == '/health':
            self._send_json(200, {'status': 'ok'})
        elif path == '/debug':
            self._serve_debug()
        elif path == '/' or path == '':
            self._serve_root()
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
        elif path == '/api/save-files':
            self._handle_save_files(body)
        elif path == '/admin/delete':
            self._handle_delete(body, qs.get('pw', [''])[0])
        else:
            self._send_json(404, {'error': 'Not found'})

    # ── ハンドラ実装 ──────────────────────────────────────────

    def _handle_save(self, body):
        try:
            init_db()  # 未初期化なら初期化を試みる
        except Exception:
            pass
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

            # IPアドレス取得（Render/Cloudflare等のリバースプロキシ対応）
            ip = (self.headers.get('X-Forwarded-For', '') or '').split(',')[0].strip()
            if not ip:
                ip = self.client_address[0]

            # APIコスト情報
            api_cost = json.dumps(data.get('api_cost', {}), ensure_ascii=False)

            db_save(rid, title, html, now.isoformat(), expires_at, ip, api_cost)

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

    def _handle_save_files(self, body):
        try:
            data      = json.loads(body)
            result_id = data.get('result_id', '')
            files     = data.get('files', [])
            if not result_id or not files:
                self._send_json(400, {'error': 'result_id and files required'})
                return
            MAX_FILE_BYTES = 20 * 1024 * 1024  # base64 で約20MB
            valid = []
            for f in files:
                b64 = f.get('data_b64', '')
                # base64 の長さでサイズを推定
                if len(b64) > MAX_FILE_BYTES * 4 // 3:
                    continue
                valid.append({'name': f.get('name','file'), 'type': f.get('type','application/octet-stream'),
                               'role': f.get('role',''), 'data_b64': b64})
            now = datetime.datetime.utcnow().isoformat()
            db_save_files(result_id, valid, now)
            self._send_json(200, {'ok': True, 'saved': len(valid)})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _serve_file(self, file_id):
        import base64
        try:
            f = db_get_file(file_id)
            if not f:
                self._send_html(404, self._wrap('404', '<p>ファイルが見つかりません。</p>'))
                return
            raw = base64.b64decode(f['data_b64'])
            ct  = f['content_type'] or 'application/octet-stream'
            fname = f['filename'].encode('utf-8', errors='replace').decode('latin-1', errors='replace')
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Disposition', f'attachment; filename="{fname}"')
            for k, v in CORS_HEADERS:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
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

    def _serve_debug(self):
        db_mode = 'PostgreSQL (Supabase)' if DATABASE_URL else 'SQLite (一時的・再起動で消える)'
        db_url_masked = (DATABASE_URL[:30] + '...') if DATABASE_URL else '未設定'
        try:
            records = db_list()
            db_status = f'接続OK・{len(records)}件のレコード'
        except Exception as e:
            records = []
            db_status = f'接続エラー: {e}'
        rows = ''.join(
            f'<tr><td>{r["id"]}</td><td>{r["title"]}</td><td>{r["created_at"]}</td>'
            f'<td style="font-size:11px;max-width:400px;word-break:break-all">{(r.get("api_cost") or "")[:200]}</td></tr>'
            for r in records
        )
        html = f'''<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>デバッグ情報</title>
<style>body{{font-family:system-ui,sans-serif;max-width:800px;margin:32px auto;padding:0 20px;color:#1e293b}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #e2e8f0;padding:8px 10px;text-align:left;font-size:13px}}
th{{background:#f8fafc}}.ok{{color:#16a34a;font-weight:700}}.ng{{color:#dc2626;font-weight:700}}</style></head>
<body>
<h2>🔍 サーバーデバッグ情報</h2>
<table>
<tr><th>項目</th><th>値</th></tr>
<tr><td>DBモード</td><td class="{'ok' if DATABASE_URL else 'ng'}">{db_mode}</td></tr>
<tr><td>DATABASE_URL</td><td>{db_url_masked}</td></tr>
<tr><td>DB状態</td><td class="{'ok' if '接続OK' in db_status else 'ng'}">{db_status}</td></tr>
<tr><td>PORT</td><td>{PORT}</td></tr>
</table>
<h3 style="margin-top:24px">保存済みレコード一覧</h3>
<table><tr><th>ID</th><th>タイトル</th><th>作成日時</th><th>api_cost (raw)</th></tr>
{rows if rows else '<tr><td colspan="3">レコードなし</td></tr>'}
</table>
</body></html>'''
        self._send_html(200, html)

    def _serve_root(self):
        host   = self.headers.get('Host', f'localhost:{PORT}')
        scheme = 'https' if not host.startswith('localhost') else 'http'
        base   = f'{scheme}://{host}'
        html   = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>翻訳チェック結果サーバー</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 20px;color:#1e293b;line-height:1.6}}
h1{{font-size:1.35rem}}
p{{color:#475569;font-size:14px}}
ul{{padding-left:1.2rem}}
a{{color:#4f46e5}}
code{{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:13px}}
</style></head><body>
<h1>翻訳チェック結果サーバー</h1>
<p>このURLは <code>translation-checker.html</code> の「URLを発行して保存」用のバックエンドです。トップページにはコンテンツはありません。</p>
<ul>
  <li><a href="{base}/health">動作確認（/health）</a></li>
  <li><a href="{base}/admin">管理ページ（URL一覧・パスワード）</a></li>
</ul>
<p>発行された結果は <code>{base}/r/（ID）</code> で閲覧できます。</p>
</body></html>'''
        self._send_html(200, html)

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
        host   = self.headers.get('Host', f'localhost:{PORT}')
        scheme = 'https' if not host.startswith('localhost') else 'http'
        rows = ''
        for e in results:
            try:
                dt = datetime.datetime.fromisoformat(e['created_at'])
                dt_jst = dt + datetime.timedelta(hours=9)
                created = dt_jst.strftime('%Y-%m-%d %H:%M')
            except Exception:
                created = e['created_at'][:16].replace('T', ' ')
            expires = e['expires_at'][:10]
            ip      = e.get('ip_address') or '-'
            url     = f'{scheme}://{host}/r/{e["id"]}'

            # APIコスト情報を整形
            try:
                cost_data = json.loads(e.get('api_cost') or '{}')
            except Exception:
                cost_data = {}
            def _cost_cell(provider):
                c = cost_data.get(provider)
                if not c:
                    return '-'
                inp = c.get('input') or 0
                out = c.get('output') or 0
                if not inp and not out:
                    return '-'
                c_val = c.get('cost')
                if c_val is None:
                    c_val = _calc_cost(c.get('model', ''), inp, out)
                cost_str = f'${c_val:.4f}' if isinstance(c_val, (int, float)) else '?'
                detail = c.get('detail', f'{round(inp/1000,1)}K/{round(out/1000,1)}K')
                return f'<span title="{detail}" style="cursor:help">{cost_str}</span>'

            cell_gpt    = _cost_cell('gpt')
            cell_claude = _cost_cell('claude')
            cell_gemini = _cost_cell('gemini')

            recalc_total = 0.0
            has_any = False
            for k in ('gpt', 'claude', 'gemini'):
                c = cost_data.get(k)
                if not c: continue
                c_val = c.get('cost')
                if c_val is None:
                    c_val = _calc_cost(c.get('model',''), c.get('input',0), c.get('output',0))
                if isinstance(c_val, (int, float)) and (c.get('input') or c.get('output')):
                    recalc_total += c_val
                    has_any = True
            total_cost = cost_data.get('_total')
            if not isinstance(total_cost, (int, float)):
                total_cost = recalc_total if has_any else None
            cell_total = f'${total_cost:.4f}' if isinstance(total_cost, (int, float)) and has_any else '-'

            # 添付ファイル一覧
            try:
                file_list = db_get_files(e['id'])
            except Exception:
                file_list = []
            if file_list:
                icons = {'application/pdf': '📄', 'image/': '🖼'}
                file_links = ''
                for fi in file_list:
                    icon = '📄' if 'pdf' in fi['content_type'] else ('🖼' if fi['content_type'].startswith('image') else '📎')
                    role_label = '(原文)' if fi['role'] == 'jp' else '(翻訳)' if fi['role'] == 'en' else ''
                    file_links += (f'<a href="{scheme}://{host}/file/{fi["id"]}" '
                                   f'title="{fi["filename"]}" style="display:block;white-space:nowrap;margin:1px 0">'
                                   f'{icon} {fi["filename"][:28]}{"…" if len(fi["filename"])>28 else ""} '
                                   f'<span style="color:#94a3b8;font-size:11px">{role_label}</span></a>')
                cell_files = file_links
            else:
                cell_files = '<span style="color:#94a3b8">-</span>'

            rows += f'''<tr>
              <td style="white-space:nowrap">{created}</td>
              <td style="white-space:nowrap">{expires}</td>
              <td style="white-space:nowrap;font-family:monospace;font-size:12px">{ip}</td>
              <td style="text-align:right;white-space:nowrap">{cell_gpt}</td>
              <td style="text-align:right;white-space:nowrap">{cell_claude}</td>
              <td style="text-align:right;white-space:nowrap">{cell_gemini}</td>
              <td style="text-align:right;white-space:nowrap;font-weight:700">{cell_total}</td>
              <td><a href="{url}" target="_blank" style="word-break:break-all">{url}</a></td>
              <td style="min-width:160px">{cell_files}</td>
              <td><button onclick="del('{e["id"]}')">🗑</button></td>
            </tr>'''
        if not rows:
            rows = ('<tr><td colspan="10" style="text-align:center;color:#94a3b8;'
                    'padding:28px;">保存された結果はありません</td></tr>')
        html = f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理ページ - 結果一覧</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:system-ui,sans-serif;margin:0;padding:28px;
       background:#f1f5f9;color:#1e293b}}
  h1{{font-size:20px;margin:0 0 20px}}
  .card{{background:#fff;border-radius:12px;padding:24px;
         box-shadow:0 2px 8px rgba(0,0,0,.06);overflow-x:auto}}
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
      <tr><th>保存日</th><th>有効期限</th>
          <th>IPアドレス</th><th>GPT</th><th>Claude</th><th>Gemini</th><th>合計</th>
          <th>URL</th><th>添付ファイル</th><th>削除</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<script>
async function del(id){{
  if(!confirm('この結果を削除しますか？（添付ファイルも削除されます）'))return;
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
    # ポートを先にバインド（Render のポートスキャンが通るように）
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'[起動] ポート {PORT} をバインドしました')

    # DB 初期化（ポートバインド後に実行）
    print(f'[起動] DB初期化中... ({"PostgreSQL" if DATABASE_URL else "SQLite"})')
    try:
        init_db()
        print('[起動] DB初期化 完了')
    except Exception as e:
        print(f'[警告] DB初期化 失敗: {e}')
        print('[警告] DBなしで起動します（保存機能は使えません）')

    print('=' * 52)
    print(f'  翻訳チェック結果サーバー 起動完了')
    print(f'  ポート : {PORT}')
    print(f'  DB    : {"PostgreSQL (Supabase)" if DATABASE_URL else "SQLite (一時的)"}')
    if DATABASE_URL:
        print(f'  DB URL: {DATABASE_URL[:40]}...')
    print(f'  管理   : http://localhost:{PORT}/admin')
    print(f'  デバッグ: http://localhost:{PORT}/debug')
    print(f'  停止   : Ctrl + C')
    print('=' * 52)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました。')
        sys.exit(0)

if __name__ == '__main__':
    main()
