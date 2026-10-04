from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .http import Fetcher
from .open_browser import open_in_chrome
from .pipeline import analyze_issue
from .report_index import build_issue_report_index
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
    reports_dir: str | Path | None = None,
) -> None:
    db_path = str(Path(db_path))
    reports_dir = Path(reports_dir) if reports_dir else Path(db_path).parent.parent / "reports"
    report_links = build_issue_report_index(reports_dir)

    def _pick_port(preferred: int) -> int:
        """Prefer the requested port; fall back to the next free one upwards."""
        for candidate in range(preferred, preferred + 40):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((host, candidate))
                except OSError:
                    continue
                return candidate
        # Nothing free: hand the decision to the OS with port 0.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            return s.getsockname()[1]

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

    # State for the background review job
    review_state: dict = {"running": False, "issue": "", "result": "", "error": ""}
    review_lock = threading.Lock()

    def _do_review(issue: str, refresh: bool) -> None:
        from .main import review_issue

        with review_lock:
            if review_state["running"]:
                review_state["error"] = "复盘任务进行中"
                return
            review_state.update(running=True, issue=issue, result="", error="")
        try:
            summary = review_issue(Path(db_path), issue, refresh=refresh, cache_dir=None)
            parts = [f"{summary['issue']} 已完成 {summary['scored']}/{summary['match_count']} 场"]
            for label, key in (("亚盘", "asian"), ("大小球", "total")):
                hits = summary[f"{key}_hits"]
                total_n = summary[f"{key}_total"]
                if total_n:
                    pct = f" ({hits / total_n * 100:.0f}%)"
                    parts.append(f"{label} {hits}/{total_n}{pct}")
                else:
                    parts.append(f"{label} 无推荐")
            review_state["result"] = " · ".join(parts)
        except Exception as exc:  # noqa: BLE001
            review_state["error"] = f"复盘失败：{exc}"
        finally:
            review_state["running"] = False

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
            if parsed.path == "/review-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(review_state, ensure_ascii=False).encode("utf-8"))
                return
            # Serve report HTML files by filename
            if parsed.path.startswith("/report/"):
                filename = parsed.path[len("/report/"):]
                # Validate: only allow issue_*.html files in the reports directory
                target = (reports_dir / filename).resolve()
                safe = bool(filename) and "/" not in filename and "\\" not in filename
                if not safe:
                    self.send_response(400)
                    self.end_headers()
                    return
                if target.parent != reports_dir.resolve() or not target.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
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
                    port=str(port),
                    analyses=analyses,
                    source_url=source_url,
                    report_links=report_links,
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
                    port=str(port),
                    report_links=report_links,
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
            elif parsed.path == "/review":
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
                threading.Thread(
                    target=_do_review, args=(issue, bool(data.get("refresh"))), daemon=True
                ).start()
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"accepted"}')
            elif parsed.path == "/review-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(review_state, ensure_ascii=False).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    try:
        port = _pick_port(port)
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"无法监听 {host}:{port}，请检查端口是否被占用或更换端口。错误: {exc}")
        return
    url = f"http://{host}:{port}/"
    print(f"异动查询页已启动：{url}")
    print(f"数据库：{db_path}")
    print("按 Ctrl+C 停止服务。")
    if open_browser:
        open_in_chrome(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止查询服务。")
    finally:
        server.server_close()
