"""独立实验网页：python injection_web.py，然后打开 http://127.0.0.1:8767。"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from config import load_settings
from injection_lab import ATTACKS, DEFENSES, TOOLS, read_results, run_experiment

HOST, PORT = "127.0.0.1", 8767
ASSETS = Path(__file__).parent / "injection-ui"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.settings = load_settings()
        self.run_lock = Lock()


class Handler(BaseHTTPRequestHandler):
    def reply(self, value, status=200, mime="application/json; charset=utf-8"):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/conversation.js": ("conversation.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if self.path in assets:
            name, mime = assets[self.path]
            self.reply((ASSETS / name).read_bytes(), mime=mime + "; charset=utf-8")
        elif self.path == "/api/catalog":
            self.reply({"attacks": ATTACKS, "defenses": DEFENSES, "tools": TOOLS, "model": self.server.settings.model})
        elif self.path == "/api/runs":
            self.reply(read_results())
        else:
            self.reply({"error": "页面不存在"}, 404)

    def do_POST(self):
        origins = {None, f"http://{HOST}:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in origins:
            self.reply({"error": "只接受本地网页请求"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.reply({"error": "请求必须为 JSON"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/api/run" or not 0 < length < 10000:
                raise ValueError("请求地址或长度无效")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body.get("attack") not in ATTACKS or body.get("defense") not in DEFENSES:
                raise ValueError("请选择有效的场景和防御层级")
            if not self.server.settings.api_key:
                raise ValueError("请先在 .env 配置 DEEPSEEK_API_KEY")
        except (ValueError, TypeError) as exception:
            self.reply({"error": str(exception)}, 400)
            return
        if not self.server.run_lock.acquire(blocking=False):
            self.reply({"error": "另一个实验正在运行，请稍后再试"}, 409)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True
            run_experiment(self.server.settings, body["attack"], body["defense"], self.event)
        finally:
            self.server.run_lock.release()

    def event(self, value):
        try:
            self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 浏览器离开后仍完成并保存当前实验。


def main():
    with Server((HOST, PORT)) as server:
        print(f"提示注入实验台：http://{HOST}:{PORT}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
