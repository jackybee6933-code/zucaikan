from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .http import Fetcher
from .open_browser import open_in_chrome
from .pipeline import analyze_issue
from .query_report import render_query_html
from .store import (
    default_db_path,
    list_issues,
    query_movements,
    rebuild_analyses_from_db,
    save_issue_movements,
)


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key) or []
    return values[0] if values else default


def serve_query(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    db_path = str(Path(db_path))

    # State for the background fetch job
    fetch_state: dict = {"running": False, "issue": "", "result": "", "error": ""}
    fetch_lock = threading.Lock()

    def _do_fetch(issue: str) -> None:
        with fetch_lock:
            if fetch_state["running"]:
                fetch_state["error"] = "抓取任务进行中"
                return
            fetch_state.update(running=True, issue=issue, result="", error="")
        try:
            # 手动抓取必须走网络：舍缓存里的 sfc/detail 页面在开赛前抓取时是空比分、
            # 过期即时盘，重抓时必须重新请求。cache_dir=None 表示不读也不写缓存。
            fetcher = Fetcher(cache_dir=None)
            analyses, actual_issue, source_url = analyze_issue(issue, fetcher=fetcher, max_workers=4)
            saved = save_issue_movements(Path(db_path), analyses, actual_issue, source_url)
            fetch_state["result"] = f"{saved['issue']} 入库 {saved['match_count']} 场，异动 {saved['attention_count']} 场"
        except Exception as exc:  # noqa: BLE001
            fetch_state["error"] = f"抓取失败：{exc}"
        finally:
            fetch_state["running"] = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            # fetch-status is a JSON endpoint handled via GET in JS polling
            if parsed.path == "/fetch-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(fetch_state, ensure_ascii=False).encode("utf-8"))
                return
            params = parse_qs(parsed.query)
            issue = _first(params, "issue")
            team = _first(params, "team")
            level = _first(params, "level")
            kind = _first(params, "kind")
            attention_only = _first(params, "attention") == "1"
            latest_only = "latest" not in params or _first(params, "latest") == "1"

            if issue:
                rows = query_movements(
                    db_path,
                    issue=issue,
                    team=team,
                    level=level,
                    kind=kind,
                    attention_only=attention_only,
                    latest_only=latest_only,
                )
                analyses = rebuild_analyses_from_db(Path(db_path), issue)
                source_url = (analyses[0].get("source_url") or "") if analyses else ""
                html = render_query_html(
                    rows,
                    issues=list_issues(Path(db_path)),
                    issue=issue,
                    team=team,
                    level=level,
                    kind=kind,
                    attention_only=attention_only,
                    latest_only=latest_only,
                    db_path=db_path,
                    analyses=analyses,
                    source_url=source_url,
                )
            else:
                rows = query_movements(
                    db_path,
                    team=team,
                    level=level,
                    kind=kind,
                    attention_only=attention_only,
                    latest_only=latest_only,
                )
                html = render_query_html(
                    rows,
                    issues=list_issues(Path(db_path)),
                    db_path=db_path,
                )
            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/fetch":
                content_length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(content_length).decode("utf-8")
                data = json.loads(body) if body else {}
                issue = data.get("issue", "")
                if not issue:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"issue is required"}')
                    return
                threading.Thread(target=_do_fetch, args=(issue,), daemon=True).start()
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"accepted"}')
            elif parsed.path == "/fetch-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(fetch_state, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"异动查询页已启动：{url}")
    print(f"数据库：{db_path}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        open_in_chrome(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止查询服务。")
    finally:
        server.server_close()
