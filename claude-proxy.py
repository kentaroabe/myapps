#!/usr/bin/env python3
"""
Claude API ローカルプロキシサーバー
translation-checker.html から Claude (Anthropic) API を呼び出すためのプロキシです。

【起動方法】
  python claude-proxy.py

【停止方法】
  Ctrl + C

起動後、translation-checker.html の Claude 設定で
「ローカルプロキシを使用」をオンにしてください。
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error
import json
import sys

PROXY_PORT = 3001
ANTHROPIC_BASE = "https://api.anthropic.com"

CORS_HEADERS = [
    ("Access-Control-Allow-Origin",  "*"),
    ("Access-Control-Allow-Methods", "POST, OPTIONS"),
    ("Access-Control-Allow-Headers",
     "Content-Type, x-api-key, anthropic-version, anthropic-dangerous-request-allow-browser"),
    ("Access-Control-Max-Age", "86400"),
]

class ProxyHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        """CORSプリフライトリクエストへの応答"""
        self.send_response(200)
        for k, v in CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        """AnthropicAPIへのリクエストを中継"""
        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = self.rfile.read(length)
            api_key  = self.headers.get("x-api-key", "")
            version  = self.headers.get("anthropic-version", "2023-06-01")

            target_url = ANTHROPIC_BASE + self.path
            req = urllib.request.Request(
                target_url,
                data=body,
                headers={
                    "Content-Type":  "application/json",
                    "x-api-key":     api_key,
                    "anthropic-version": version,
                },
                method="POST",
            )

            with urllib.request.urlopen(req) as resp:
                resp_body = resp.read()
                status    = resp.status

            self._send(status, resp_body)

        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as e:
            err = json.dumps({"error": {"message": str(e), "type": "proxy_error"}}).encode()
            self._send(500, err)

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  [{self.address_string()}] {fmt % args}")


def main():
    server = HTTPServer(("localhost", PROXY_PORT), ProxyHandler)
    print("=" * 50)
    print(f"  Claude プロキシサーバー起動完了")
    print(f"  URL: http://localhost:{PROXY_PORT}")
    print(f"  停止: Ctrl + C")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nプロキシサーバーを停止しました。")
        sys.exit(0)

if __name__ == "__main__":
    main()
