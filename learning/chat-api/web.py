"""本地网页入口，复用 context.py 和 model.py 的上下文组装与模型调用。"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from config import load_settings
from loop import AgentLoop
from observability import ObservedAgent
from telemetry import read_traces
from tools import TOOL_DEFINITIONS


STATIC_DIR = Path(__file__).parent / "web"
HOST = "127.0.0.1"
PORT = 8766


class AgentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, RequestHandler)
        self.settings = load_settings()
        self.turn_lock = Lock()
        self.agent = ObservedAgent(AgentLoop(self.settings))

    def run_turn(self, prompt, on_context=None):
        """网页只负责串行调度，执行逻辑统一交给 loop.py。"""
        with self.turn_lock:
            return self.agent.run_turn(prompt, on_context=on_context)


class RequestHandler(BaseHTTPRequestHandler):
    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def send_bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        assets = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"), "/app.js": ("app.js", "text/javascript")}
        if self.path in assets:
            filename, mime = assets[self.path]
            self.send_bytes((STATIC_DIR / filename).read_bytes(), mime + "; charset=utf-8")
        elif self.path == "/api/state":
            with self.server.turn_lock:
                state = {
                    "turns": list(self.server.agent.turns), "model": self.server.settings.model,
                    "session_id": self.server.agent.session_id,
                    "tools": TOOL_DEFINITIONS,
                }
            self.send_json(state)
        elif self.path == "/api/traces":
            self.send_json(read_traces())
        else:
            self.send_json({"error": "页面不存在"}, 404)

    def do_POST(self):
        allowed_origins = {f"http://{HOST}:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Origin") not in allowed_origins | {None}:
            self.send_json({"error": "只接受本地网页请求"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_json({"error": "请求必须为 JSON"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self.send_json({"error": "请求 JSON 无效或过大"}, 400)
            return
        if self.path == "/api/chat":
            prompt = body.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                self.send_json({"error": "请输入问题"}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                turn = self.server.run_turn(
                    prompt.strip(),
                    on_context=lambda messages: self.send_event({"type": "context", "messages": messages}),
                )
            except ValueError as exception:
                self.send_event({"type": "error", "error": str(exception)})
                return
            self.send_event({"type": "result", "turn": turn})
        elif self.path == "/api/reset":
            with self.server.turn_lock:
                self.server.agent = ObservedAgent(AgentLoop(self.server.settings))
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "接口不存在"}, 404)

    def send_event(self, value):
        try:
            self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with AgentServer((HOST, PORT)) as server:
        print(f"本地 Agent 网页已启动：http://{HOST}:{PORT}", flush=True)
        print("Trace 保存在 traces/spans.jsonl，Ctrl+C 停止。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止。")


if __name__ == "__main__":
    main()
