"""实验 2-8 本地网页入口；使用 .status-venv 启动，无云端模型调用。"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from status_attention import RUN_DIR, run_experiment
from status_scenario import prepare_arms
from status_setup import MODEL_DIR, MODEL_ID

HOST, PORT = "127.0.0.1", 8768
ASSETS = Path(__file__).parent / "status-ui"


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address):
        super().__init__(address, Handler)
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
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if self.path in assets:
            name, mime = assets[self.path]
            self.reply((ASSETS / name).read_bytes(), mime=mime + "; charset=utf-8")
        elif self.path == "/api/catalog":
            arms, _ = prepare_arms()
            self.reply({"model": MODEL_ID, "model_ready": (MODEL_DIR / "model.safetensors").exists(), "arms": arms})
        elif self.path == "/api/runs":
            records = (json.loads(path.read_text(encoding="utf-8")) for path in RUN_DIR.glob("*/result.json"))
            summaries = ({key: record[key] for key in ("id", "created_at", "model", "max_tokens")} for record in records)
            self.reply(sorted(summaries, key=lambda record: record["created_at"], reverse=True)[:10])
        else:
            match = re.fullmatch(r"/api/(run|heatmap|weights)/([0-9a-f]{32})", self.path)
            if not match:
                self.reply({"error": "页面不存在"}, 404)
                return
            name, mime = {"run": ("result.json", "application/json"), "heatmap": ("attention.png", "image/png"), "weights": ("attention.npz", "application/octet-stream")}[match[1]]
            path = RUN_DIR / match[2] / name
            if path.exists():
                self.reply(path.read_bytes(), mime=mime)
            else:
                self.reply({"error": "结果尚不存在"}, 404)

    def do_POST(self):
        origins = {None, f"http://{HOST}:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in origins or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.reply({"error": "只接受本地 JSON 请求"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if self.path != "/api/run" or not 0 < length < 1000:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body.get("trials") not in (1, 3) or body.get("max_tokens") not in (128, 256, 512):
                raise ValueError
        except (ValueError, TypeError):
            self.reply({"error": "请选择有效的试验次数和生成上限"}, 400)
            return
        if not self.server.run_lock.acquire(blocking=False):
            self.reply({"error": "本地模型正在运行，请稍候"}, 409)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                run_experiment(body["trials"], body["max_tokens"], self.event)
            except Exception as exception:
                self.event({"type": "error", "message": f"本地实验失败：{type(exception).__name__}: {exception}"})
        finally:
            self.server.run_lock.release()

    def event(self, value):
        try:
            self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    with Server((HOST, PORT)) as server:
        print(f"状态栏注意力实验：http://{HOST}:{PORT}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
