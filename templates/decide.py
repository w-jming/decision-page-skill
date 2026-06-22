#!/usr/bin/env python3
"""decision-page 本地决策服务（仅 Python 标准库，零依赖）。

用法：
    python3 decide.py                          # 启动服务并自动打开浏览器
    python3 decide.py --port 8888 --no-browser
    python3 decide.py --dir docs/decisions     # 数据目录不在脚本旁时指定
    python3 decide.py --idle-timeout 7200      # 空闲自动退出秒数（默认 3600；0 = 不退出）
                                               # 页面开着（SSE 连接在）不算空闲，关掉页面后计时
    python3 decide.py reply "回答内容"          # 以智能体身份向聊天面板追加一条回复
    python3 decide.py reply - <<'EOF'          # 多行回复从 stdin 读
    第一行
    第二行
    EOF
    python3 decide.py watch                     # 持续输出新事件（提问/保存），供值守；与 agent 无关
    python3 decide.py poll                      # 打印自上次以来的新事件后退出（任何 agent 可周期调用）

数据目录中的文件（默认为脚本所在目录，--dir 可改）：
    decisions.json    决策数据，由 Claude 维护；文件一变页面即热更新
    chat.jsonl        聊天记录；页面提问追加 user 行，reply 子命令追加 assistant 行
    decisions-log.md  决策日志；页面「保存决策」追加写入，是跨会话的持久契约
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "decisions.html"

DATA_DIR = HERE  # main() 里按 --dir 覆盖
_LOCK = threading.Lock()
LAST_ACTIVITY = time.time()  # 任何请求或存活的 SSE 连接都会刷新；供空闲看门狗使用
IDLE_TIMEOUT = 3600  # serve() 按 --idle-timeout 覆盖；0 = 不自动退出


def _touch_activity() -> None:
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()

LOG_HEADER = """# 决策日志

本文件由决策页（`decide.py` 启动的 `decisions.html`）**追加写入**，是用户决策的持久记录。

智能体职责：发现新条目后回填项目决策表并解锁任务，然后把条目的 `待智能体回填` 注释改为 `已回填（日期）`。
"""


def _f(name: str) -> Path:
    return DATA_DIR / name


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _versions() -> dict:
    out = {}
    for key, name in (("decisions", "decisions.json"), ("chat", "chat.jsonl"), ("log", "decisions-log.md")):
        p = _f(name)
        try:
            st = p.stat()
            out[key] = f"{st.st_mtime_ns}-{st.st_size}"
        except FileNotFoundError:
            out[key] = "0"
    return out


def _read_decisions() -> dict:
    p = _f("decisions.json")
    if not p.exists():
        return {"title": "", "subtitle": "", "decisions": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"title": "decisions.json 解析失败", "subtitle": str(exc), "decisions": []}


def _write_decisions(data: dict) -> None:
    p = _f("decisions.json")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _read_chat() -> list:
    p = _f("chat.jsonl")
    if not p.exists():
        return []
    msgs = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return msgs


def chat_append(role: str, text: str) -> dict:
    with _LOCK:
        msgs = _read_chat()
        msg = {"id": (msgs[-1]["id"] + 1) if msgs else 1, "role": role, "text": text, "time": _now()}
        with _f("chat.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def _append_log(items: list, global_note: str, data: dict) -> None:
    log = _f("decisions-log.md")
    if not log.exists():
        log.write_text(LOG_HEADER, encoding="utf-8")
    by_id = {d.get("id"): d for d in data.get("decisions", [])}
    lines = [f"## 决策结果 {_now()}", "", "<!-- 待智能体回填 -->", ""]
    for it in items:
        d = by_id.get(it.get("id"), {})
        title = d.get("title", "")
        note = (it.get("notes") or "").strip()
        lines.append(f"- **{it.get('id')} {title}**：{it.get('label', '')}" + (f"；备注：{note}" if note else ""))
    if (global_note or "").strip():
        lines += ["", f"补充说明：{global_note.strip()}"]
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(lines) + "\n")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:  # noqa: N802
        _touch_activity()
        if self.path in ("/", "/decisions.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(200, {"decisions": _read_decisions(), "chat": _read_chat(), "versions": _versions()})
        elif self.path == "/api/events":
            self._sse()
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        _touch_activity()
        try:
            if self.path == "/api/chat":
                text = (self._payload().get("text") or "").strip()
                if not text:
                    self._json(400, {"ok": False, "error": "empty"})
                    return
                msg = chat_append("user", text)
                print(f"[decide] 用户提问 #{msg['id']}：{text[:80]}")
                self._json(200, {"ok": True, "id": msg["id"]})
            elif self.path == "/api/save":
                payload = self._payload()
                items = payload.get("items") or []
                if not items:
                    self._json(400, {"ok": False, "error": "没有可保存的选择"})
                    return
                with _LOCK:
                    data = _read_decisions()
                    by_id = {d.get("id"): d for d in data.get("decisions", [])}
                    for it in items:
                        d = by_id.get(it.get("id"))
                        if d is None:
                            continue
                        d["status"] = "decided"
                        d["result"] = {
                            "choice": it.get("choice"),
                            "label": it.get("label", ""),
                            "notes": (it.get("notes") or "").strip(),
                            "time": _now(),
                        }
                    _write_decisions(data)
                    _append_log(items, payload.get("globalNote", ""), data)
                print(f"[decide] 已保存 {len(items)} 项决策 → {_f('decisions-log.md')}")
                self._json(200, {"ok": True, "saved": len(items), "path": "decisions-log.md"})
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(exc)})

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last: dict | None = None
        last_beat = 0.0
        # 心跳是发现“页面已关”的唯一手段（死连接首次写入未必报错），
        # 探活粒度必须细于空闲预算，否则看门狗会被死连接误保活
        ping_iv = min(15.0, max(1.0, IDLE_TIMEOUT / 4)) if IDLE_TIMEOUT > 0 else 15.0
        try:
            while True:
                _touch_activity()  # 页面开着就不算空闲
                cur = _versions()
                now = time.time()
                if cur != last:
                    self.wfile.write(f"data: {json.dumps(cur)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last, last_beat = cur, now
                elif now - last_beat > ping_iv:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_beat = now
                time.sleep(0.5)
        except OSError:
            pass

    def log_message(self, *args: object) -> None:  # 静默默认访问日志
        pass


def serve(args: argparse.Namespace) -> None:
    _f("chat.jsonl").touch(exist_ok=True)  # 让值守端的 tail -F 立即可用
    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    global IDLE_TIMEOUT
    IDLE_TIMEOUT = max(0, args.idle_timeout)
    print(f"[decide] 决策页：{url}（Ctrl+C 退出）")
    print(f"[decide] 数据目录：{DATA_DIR}")
    if IDLE_TIMEOUT > 0:
        print(f"[decide] 空闲 {IDLE_TIMEOUT}s 自动退出（页面开着不计时；--idle-timeout 0 可关闭）")

        def watchdog() -> None:
            poll = max(1, min(30, IDLE_TIMEOUT // 4))
            while True:
                time.sleep(poll)
                idle = time.time() - LAST_ACTIVITY
                if idle > IDLE_TIMEOUT:
                    print(f"[decide] 已空闲 {int(idle)}s（无页面连接与请求），自动退出")
                    server.shutdown()
                    return

        threading.Thread(target=watchdog, daemon=True).start()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[decide] 已退出")


def reply(args: argparse.Namespace) -> None:
    text = args.text
    if text in (None, "-"):
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("[decide] 回复内容为空")
    msg = chat_append("assistant", text)
    print(f"[decide] 已回复 #{msg['id']} → {_f('chat.jsonl')}")


# ── 值守事件流（与具体 agent 无关）─────────────────────────────────────────
# watch/poll 把"用户提问"和"保存决策"归一化成事件行，供任何能跑命令、读其输出
# 的 agent 值守。游标存在数据目录的 .decide-watch.json，只认新增——原地修改日志
# 不会重放（这正是旧 `tail -F` 流的痛点）。
WATCH_STATE_FILE = ".decide-watch.json"


def _log_headers() -> list:
    p = _f("decisions-log.md")
    if not p.exists():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]


def _current_cursor() -> dict:
    msgs = _read_chat()
    max_id = max((m.get("id", 0) for m in msgs), default=0)
    return {"chat_id": max_id, "log_entries": len(_log_headers())}


def _load_cursor() -> "dict | None":
    try:
        return json.loads(_f(WATCH_STATE_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cursor(cur: dict) -> None:
    _f(WATCH_STATE_FILE).write_text(json.dumps(cur), encoding="utf-8")


def _collect_events(cur: dict) -> tuple:
    """按游标取新事件；返回 (事件行列表, 新游标)。"""
    events = []
    msgs = _read_chat()
    last_id = cur.get("chat_id", 0)
    max_id = last_id
    for m in msgs:
        mid = m.get("id", 0)
        if mid > last_id and m.get("role") == "user":
            text = " ".join((m.get("text") or "").split())
            events.append(f"QUESTION #{mid}: {text[:120]}")
        max_id = max(max_id, mid)
    headers = _log_headers()
    for h in headers[cur.get("log_entries", 0):]:
        events.append(f"SAVED: {h.lstrip('# ').strip()}")
    return events, {"chat_id": max_id, "log_entries": len(headers)}


def poll(args: argparse.Namespace) -> None:
    if args.all:
        cur = {"chat_id": 0, "log_entries": 0}
    else:
        cur = _load_cursor()
        if cur is None:  # 首次：从当前末尾开始，不回放历史（同 tail -F -n 0）
            _save_cursor(_current_cursor())
            return
    events, new_cur = _collect_events(cur)
    _save_cursor(new_cur)
    for e in events:
        print(e)
        sys.stdout.flush()


def watch(args: argparse.Namespace) -> None:
    cur = {"chat_id": 0, "log_entries": 0} if args.all else (_load_cursor() or _current_cursor())
    _save_cursor(cur)
    interval = max(0.2, args.interval)
    try:
        while True:
            events, new_cur = _collect_events(cur)
            if new_cur != cur:
                cur = new_cur
                _save_cursor(cur)
            for e in events:
                print(e)
                sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def _extract_dir(argv: list) -> tuple:
    """把 --dir 从 argv 中取出，使其在子命令前后均可使用。"""
    rest, data_dir, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--dir" and i + 1 < len(argv):
            data_dir = argv[i + 1]
            i += 2
        elif argv[i].startswith("--dir="):
            data_dir = argv[i].split("=", 1)[1]
            i += 1
        else:
            rest.append(argv[i])
            i += 1
    return rest, data_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="decision-page 本地决策服务",
        epilog="通用选项：--dir <数据目录>（decisions.json 等所在处，默认脚本所在目录），可放在任意位置",
    )
    sub = parser.add_subparsers(dest="cmd")
    p_serve = sub.add_parser("serve", help="启动服务（默认命令）")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument("--idle-timeout", type=int, default=3600,
                         help="空闲多少秒后自动退出（页面开着不计时）；0 = 不自动退出（默认 3600）")
    p_reply = sub.add_parser("reply", help="向聊天面板追加一条智能体回复（'-' 或省略则读 stdin）")
    p_reply.add_argument("text", nargs="?", default=None)
    p_poll = sub.add_parser("poll", help="打印自上次以来的新事件后退出（提问/保存；任何 agent 可周期调用）")
    p_poll.add_argument("--all", action="store_true", help="忽略游标，输出全部历史事件")
    p_watch = sub.add_parser("watch", help="持续输出新事件（每行一个，供值守）；Ctrl+C 退出")
    p_watch.add_argument("--all", action="store_true", help="忽略游标，先输出全部历史事件再持续")
    p_watch.add_argument("--interval", type=float, default=1.0, help="轮询间隔秒（默认 1.0）")

    argv, data_dir = _extract_dir(sys.argv[1:])
    # 无子命令时默认 serve（python3 decide.py --port 8888 等价于 serve --port 8888）
    if argv[:1] not in (["serve"], ["reply"], ["poll"], ["watch"]) and "-h" not in argv and "--help" not in argv:
        argv = ["serve"] + argv
    args = parser.parse_args(argv)

    global DATA_DIR
    if data_dir:
        DATA_DIR = Path(data_dir).resolve()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.cmd == "reply":
        reply(args)
    elif args.cmd == "poll":
        poll(args)
    elif args.cmd == "watch":
        watch(args)
    else:
        serve(args)


if __name__ == "__main__":
    main()
