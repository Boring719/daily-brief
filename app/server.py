"""本地网页服务：打开浏览器就能看今天的简报。"""
from __future__ import annotations

import json
import socket
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import REPORT_DIR, ensure_dirs, load_settings
from .render import render_empty
from .store import latest_report

STATE = {"state": "idle", "message": "", "percent": 0, "started": 0.0, "elapsed": 0}
LOCK = threading.Lock()


def _set_state(state: str, message: str, percent: int | None = None) -> None:
    STATE["state"] = state
    STATE["message"] = message
    if percent is not None:
        STATE["percent"] = percent
    if state == "running" and not STATE["started"]:
        STATE["started"] = time.time()
    if state in ("done", "error"):
        STATE["elapsed"] = int(time.time() - STATE["started"]) if STATE["started"] else 0
        STATE["started"] = 0.0


def _worker() -> None:
    from .pipeline import run

    try:
        res = run(progress=lambda m, p=0: _set_state("running", m, p))
        _set_state("done" if res["ok"] else "error", res["message"], 100 if res["ok"] else 0)
    except Exception as e:  # noqa: BLE001
        _set_state("error", f"{type(e).__name__}: {e}", 0)


class Handler(BaseHTTPRequestHandler):
    server_version = "DailyBrief/1.0"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            today = date.today().isoformat()
            row = latest_report()
            if row and row["date"] == today and (REPORT_DIR / f"{today}.html").exists():
                return self._send(200, (REPORT_DIR / f"{today}.html").read_bytes())
            if row:
                return self._send(200, (REPORT_DIR / f"{row['date']}.html").read_bytes())
            return self._send(200, render_empty().encode("utf-8"))

        if path.startswith("/r/") and path.endswith(".html"):
            name = path[3:-5]
            f = REPORT_DIR / f"{name}.html"
            if f.exists():
                return self._send(200, f.read_bytes())
            return self._send(404, "<h1>404</h1><p>没有这一天的简报</p>".encode("utf-8"))

        if path == "/api/status":
            data = dict(STATE)
            if data["state"] == "running" and data["started"]:
                data["elapsed"] = int(time.time() - data["started"])
            return self._json(data)

        if path == "/api/dates":
            from .store import list_dates

            return self._json({"dates": list_dates()})

        if path == "/health":
            return self._json({"ok": True, "state": STATE["state"]})

        if path == "/favicon.ico":
            return self._send(204, b"")

        return self._send(404, "<h1>404</h1><p>页面不存在</p>".encode("utf-8"))

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/generate":
            with LOCK:
                if STATE["state"] == "running":
                    return self._json({"ok": False, "message": "已经在生成了"}, 409)
                _set_state("running", "准备中……")
                threading.Thread(target=_worker, daemon=True).start()
            return self._json({"ok": True, "message": "已开始"})
        return self._send(404, b"not found")


def _free_port(start: int) -> int:
    for port in range(start, start + 12):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> None:
    ensure_dirs()
    settings = load_settings()
    port = _free_port(int(settings.get("port", 8765)))
    url = f"http://127.0.0.1:{port}/"
    print(f"每日国际简报已启动：{url}")
    print("（关闭这个黑窗口就会停止服务）")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
