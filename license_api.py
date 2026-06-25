"""
로컬 설치형 라이선스 검증 HTTP 엔드포인트 (stdlib only, Flask 불필요).
서버에서 systemd 서비스로 127.0.0.1:8600 에 띄우고, nginx가 /license/ 로 프록시.

엔드포인트:
  GET  /license/ping            → {"ok": true}
  POST /license/verify  {key, machine_id}  → verify_and_bind 결과(JSON)
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_license import verify_and_bind


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/license/ping"):
            self._send({"ok": True})
        else:
            self._send({"ok": False, "error": "not_found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/").endswith("/license/verify"):
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(ln) or b"{}")
            except Exception:
                data = {}
            try:
                result = verify_and_bind(data.get("key", ""), data.get("machine_id", ""))
            except Exception as e:
                result = {"ok": False, "code": "server_error", "message": str(e)}
            self._send(result)
        else:
            self._send({"ok": False, "error": "not_found"}, 404)

    def log_message(self, *args):
        pass  # 액세스 로그 비활성


def main():
    port = int(os.environ.get("LICENSE_PORT", "8600"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"license_api listening on 127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
