#!/usr/bin/env python3
"""本地决策服务（仅 Python 标准库，零依赖）。

用法：
    python3 decide.py              # 启动后自动打开浏览器
    python3 decide.py --port 8888 --no-browser

服务同目录的 decisions.html；页面点「保存决策」后，结果追加写入
同目录 decisions-log.md，无需复制粘贴回对话。
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "decisions.html"
LOG = HERE / "decisions-log.md"
LOG_HEADER = """# 决策日志

本文件由决策页（`decide.py` 启动的 `decisions.html`）**追加写入**，是用户决策的持久记录。

智能体职责：发现新条目后回填项目决策表并解锁任务，然后把条目的 `待智能体回填` 注释改为 `已回填（日期）`。
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/decisions.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/save":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            markdown = payload["markdown"].strip()
            if not LOG.exists():
                LOG.write_text(LOG_HEADER, encoding="utf-8")
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n---\n\n<!-- 保存于 {stamp}，待智能体回填 -->\n\n{markdown}\n"
            with LOG.open("a", encoding="utf-8") as f:
                f.write(entry)
            body = json.dumps({"ok": True, "path": LOG.name}).encode("utf-8")
            self._send(200, body, "application/json")
            print(f"[decide] 决策已追加写入 {LOG}")
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
            self._send(500, body, "application/json")

    def log_message(self, *args: object) -> None:  # 静默默认访问日志
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="本地决策服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[decide] 决策页：{url}（Ctrl+C 退出）")
    print(f"[decide] 保存目标：{LOG}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[decide] 已退出")


if __name__ == "__main__":
    main()
