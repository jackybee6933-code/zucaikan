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
    latest_captured_at,
    list_issues,
    list_leagues,
    query_movements,
    rebuild_analyses_from_db,
    save_issue_movements,
)


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key) or []
    return values[0] if values else default


CDP_DEFAULT = "http://127.0.0.1:9222"
CDP_HINT = (
    "\u8fde\u4e0d\u4e0a Chrome \u8c03\u8bd5\u7aef\u53e3\u3002\u8bf7\u6309\u987a\u5e8f\u64cd\u4f5c\uff1a"
    "\n  1) \u5148\u5b8c\u5168\u9000\u51fa Chrome\uff08\u7a97\u53e3\u5173\u6389\u8fd8\u4e0d\u591f\uff0c"
    "\u6258\u76d8\u56fe\u6807\u4e5f\u8981\u9000\uff09"
    "\n  2) \u8fd0\u884c  python setup_cdp_profile.py  "
    "\u2014\u2014 \u5b83\u4f1a\u590d\u5236\u5df2\u9a8c\u8bc1 cookie \u5e76\u542f\u52a8\u5e26\u8c03\u8bd5\u7aef\u53e3\u7684 Chrome"
    "\n  3) \u786e\u8ba4 http://127.0.0.1:9222/json/version \u80fd\u6253\u5f00\uff0c\u518d\u91cd\u8dd1"
    "Cookie \u590d\u5236\u4e0d\u80fd\u7701\uff1a\u6ca1\u5b83\u4f1a\u4e00\u76f4\u5361\u5728\u9a8c\u8bc1\u9875\u3002"
)

FETCH_DEDUP_MINUTES = 30


def format_review(summary: dict) -> str:
    """把 review_issue 的统计格式化成查询页展示的一行文案。"""
    first = f"{summary['issue']} 已完成 {summary['scored']}/{summary['match_count']} 场"
    if summary.get("run_id") is not None:
        first += f"（run #{summary['run_id']}）"
    parts = [first]
    for label, key in (("亚盘", "asian"), ("大小球", "total")):
        hits = summary[f"{key}_hits"]
        count = summary[f"{key}_total"]
        if count:
            parts.append(f"{label} {hits}/{count} ({hits / count * 100:.0f}%)")
        else:
            parts.append(f"{label} 无推荐")
    refreshed = summary.get("refreshed") or {}
    refresh_errors: list[str] = []
    if refreshed.get("error"):
        refresh_errors.append(refreshed["error"])
    refresh_errors.extend(refreshed.get("errors") or [])
    if refresh_errors:
        parts.append("比分刷新失败：" + "；".join(refresh_errors))
    return " · ".join(parts)


def run_review(db_path: str | Path, issue: str, refresh: bool) -> str:
    """跑一次复盘并返回展示文案；异常向上抛给调用方记录。"""
    from .main import review_issue

    return format_review(review_issue(Path(db_path), issue, refresh=refresh, cache_dir=None))


def run_ai_analyze(issue: str, *, db_path: str | Path = "") -> str:
    """Manual AI team-read analysis: reuse the issue's latest DB run, ask the
    model to read the team materials, and write the tendency back in place
    (no new run, no re-fetch). Returns a short user-facing summary.

    The 500 data-analysis pages (shuju) must exist as a scraped artifact
    before the LLM has anything to read; missing artifact triggers the same
    CDP-Chrome scrape path as "刷新盘口分歧" (off-screen window, auto-revealed
    only when a human checkbox is required).
    """
    from .llm import TeamReader, enrich_team_reads
    from .cdp_profile import shutdown_cdp_chrome
    from .odds_pages import (
        PAGES_STALE_HOURS,
        has_odds_rows,
        load_odds_pages_raw,
        pages_age_hours,
        shuju_text,
    )
    from .store import (
        connect,
        default_db_path,
        rebuild_analyses_from_db,
        update_team_reads,
    )

    target_db = Path(db_path) if db_path else Path(default_db_path())
    conn = connect(target_db)
    row = conn.execute(
        "SELECT MAX(run_id) FROM issue_runs WHERE issue = ?", (issue,)
    ).fetchone()
    conn.close()
    if row is None or row[0] is None:
        return f"{issue} 未入库，请先「抓取并入库」再做 AI 分析"
    actual_issue = issue

    # Ensure the per-match analysis pages are available and fresh enough for
    # the model to read team data. Skip the Chrome step entirely when the
    # artifact exists and is within PAGES_STALE_HOURS, exactly like
    # run_refresh_odds. Missing / stale → same CDP path (off-screen window,
    # auto-reveal for EdgeOne checkbox), then re-read from the fresh artifact.
    # has_odds_rows() is intentionally not part of this condition: an artifact
    # that carries yazhi odds but no shuju text still serves the refresh
    # semantics, and the LLM simply reads whatever shuju text is present.
    from .main import _default_cache_dir
    cache_dir = _default_cache_dir()
    age = pages_age_hours(cache_dir, actual_issue)
    needed_pages = 0
    if age is not None and age <= PAGES_STALE_HOURS:
        pass
    else:
        cdp_error = _ensure_cdp(CDP_DEFAULT)
        if cdp_error:
            return cdp_error
        try:
            scrape_error = _run_odds_scraper(actual_issue, cache_dir, CDP_DEFAULT, kinds=["shuju"])
        finally:
            shutdown_cdp_chrome()
        if scrape_error:
            return f"分析页抓取失败：{scrape_error}"

    analyses = rebuild_analyses_from_db(target_db, actual_issue)
    raw = load_odds_pages_raw(cache_dir, actual_issue)
    fixture_ids = [str(a.get("fixture_id") or "") for a in analyses]
    # Old DB payloads predate the fixture_id column; when every id is empty but
    # the artifact does carry shuju keys, derive fixture ids from the artifact
    # key order (one shuju entry per match, in scraper order).
    if not any(fixture_ids) and raw:
        shuju_fids: list[str] = []
        seen: set[str] = set()
        for key in raw:
            if key.endswith("|shuju"):
                fid = key.rsplit("|", 1)[0]
                if fid not in seen:
                    seen.add(fid)
                    shuju_fids.append(fid)
        fixture_ids = shuju_fids
        # analyses 与 fixture_ids 数量一致时按 index 对齐 page_texts；
        # 不一致时用 match_no 从 fixture_ids 里找对应 fid（若有的话）。
    page_texts: list[str] = []
    for analysis in analyses:
        fid = str(analysis.get("fixture_id") or "")
        if not fid:
            # 老库无 fixture_id：用 match_no 作为第 N 个 fid 的索引
            try:
                idx = int(analysis.get("match_no", "0")) - 1
            except ValueError:
                idx = -1
            fid = fixture_ids[idx] if 0 <= idx < len(fixture_ids) else ""
        page_texts.append(shuju_text(raw, fid))
    needed_pages = sum(1 for t in page_texts if t)
    if needed_pages == 0:
        return f"{actual_issue}：500 数据分析页缺失或抓取失败，AI 无资料可读，请先确认单场页产物 cache/odds_pages_{actual_issue}.json 含 shuju 页"
    reader = TeamReader()
    if not reader.enabled:
        return f"{actual_issue}：未配置 LLM_API_KEY（或主备均未配置），AI 分析不可用"
    progress_file = cache_dir / ".ai_scrape_progress"
    count = enrich_team_reads(
        analyses,
        reader=reader,
        page_texts=page_texts,
        progress_path=progress_file,
        issue=actual_issue,
    )
    if not count:
        return f"{actual_issue}：AI 分析全部失败（已读 {needed_pages}/{len(fixture_ids)} 场数据页），倾向未更新"
    # When fixture_ids came from the artifact (not the DB), analyses[i] may not
    # correspond 1:1 with fixture_ids[i] by index — but team_read is still
    # keyed by match_no, which is what update_team_reads writes.  The count
    # returned by enrich_team_reads is the number of items that got a result.
    team_reads = {
        str(a.get("match_no") or ""): a["team_read"]
        for a in analyses
        if a.get("team_read")
    }
    updated = update_team_reads(target_db, actual_issue, team_reads)
    return (
        f"{actual_issue}：AI 分析完成 {count}/{len(analyses)} 场"
        f"（读 {needed_pages} 场数据页、更新 {updated} 行），刷新页面查看「倾向」"
    )
def _ensure_cdp(endpoint: str) -> str | None:
    """Probe the CDP port; if unreachable, launch Chrome with the CDP profile.

    Returns an error string on failure, or None on success.
    """
    import urllib.request
    from urllib.parse import urlparse

    from .cdp_profile import ensure_cdp_chrome, shutdown_cdp_chrome

    def probe() -> bool:
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=3) as resp:
                resp.read()
            return True
        except OSError:
            return False

    if probe():
        return None
    # 端口没开：复制已验证 cookie 并启动带调试端口的 Chrome（包内函数，exe 也能直接调）。
    port = urlparse(endpoint).port or 9222
    err = ensure_cdp_chrome(port)
    if probe():
        return None
    return err or CDP_HINT


def run_refresh_odds(issue: str, *, cdp: str = CDP_DEFAULT, db_path: str | Path = "") -> str:
    """Refresh the per-match odds pages artifact and re-run movement ingestion.

    Skip if the artifact is fresh (<= PAGES_STALE_HOURS). Otherwise probe the
    Chrome CDP port; if unreachable return CDP_HINT. If reachable, run
    tools/scrape_odds_pages.py to re-scrape and overwrite the artifact.
    """
    from .cdp_profile import shutdown_cdp_chrome
    from .main import _default_cache_dir
    from .odds_pages import PAGES_STALE_HOURS, has_odds_rows, pages_age_hours

    cache_dir = _default_cache_dir()
    age = pages_age_hours(cache_dir, issue)
    # mtime 只说产物文件“存在且很新”，不说它里面有盘口。AI 分析补抓的
    # shuju 页会把文件合并刷新 mtime，产物里却没有任何 yazhi 公司行，
    # 这类残缺产物若跳过，盘口数据就永远补不上——所以内容也要过一道。
    if (
        age is not None
        and age <= PAGES_STALE_HOURS
        and has_odds_rows(cache_dir, issue)
    ):
        return (
            f"{issue} \u76d8\u53e3\u4ea7\u7269\u662f {age:.1f} \u5c0f\u65f6\u524d\u7684\uff0c"
            f"\u672a\u8d85 {PAGES_STALE_HOURS:.0f}h \u9608\u503c\uff0c\u8df3\u8fc7\u91cd\u6293"
        )
    endpoint = cdp.rstrip("/")
    # Ensure a Chrome debug port is reachable; auto-launch setup_cdp_profile.py if not.
    cdp_error = _ensure_cdp(endpoint)
    if cdp_error:
        return cdp_error
    try:
        scrape_error = _run_odds_scraper(issue, cache_dir, endpoint)
    finally:
        shutdown_cdp_chrome()
    if scrape_error:
        return scrape_error
    new_age = pages_age_hours(cache_dir, issue)
    if new_age is None:
        return f"{issue} \u4ea7\u7269\u4ecd\u672a\u751f\u6210\uff0c\u8bf7\u67e5\u770b\u540e\u53f0\u65e5\u5fd7"
    # Re-run ingestion so the fresh split data lands in the DB as a new run.
    # LLM 倾向不再随刷新自动跑：「AI 分析」按钮在查询页按需触发（复用最新 run 的数据）。
    fetcher = Fetcher(cache_dir=None)
    fetcher.cache.root = cache_dir
    analyses, actual_issue, source_url = analyze_issue(
        issue, fetcher=fetcher, max_workers=4, team_read=False
    )
    target_db = Path(db_path) if db_path else Path(default_db_path())
    saved = save_issue_movements(target_db, analyses, actual_issue, source_url)
    return (
        f"{issue} \u76d8\u53e3\u4ea7\u7269\u5df2\u5237\u65b0\uff08{new_age:.1f} h\uff09\uff0c"
        f"\u672c\u671f\u5171 {saved['match_count']} \u573a\u3001\u5f02\u52a8 {saved['attention_count']} \u6761"
    )


def _odds_scraper_script() -> Path:
    from .main import _app_root

    script = _app_root() / "tools" / "scrape_odds_pages.py"
    if script.is_file():
        return script
    return Path(__file__).resolve().parent.parent / "tools" / "scrape_odds_pages.py"


def _run_odds_scraper(
    issue: str, cache_dir: Path, endpoint: str, *, kinds: list[str] | None = None
) -> str | None:
    """Run tools/scrape_odds_pages.py. Return an error string, or None on success."""
    script = _odds_scraper_script()
    if not script.is_file():
        return f"找不到抓取脚本 {script}"
    import importlib.util

    spec = importlib.util.spec_from_file_location("scrape_odds_pages", script)
    if spec is None or spec.loader is None:
        return f"无法加载抓取脚本 {script}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    argv = ["--issue", issue, "--cache-dir", str(cache_dir), "--cdp", endpoint]
    if kinds:
        argv.extend(["--kinds", *kinds])
    code = mod.main(argv)
    if code != 0:
        return f"{issue} 抓取未完成（退出码 {code}），原产物未被覆盖"
    return None


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
        skip = _dedup_message(db_path, issue)
        if skip:
            fetch_state["result"] = skip
            fetch_state["running"] = False
            return
        try:
            # 手动抓取必须走网络：舍缓存里的 sfc/detail 页面在开赛前抓取时是空比分、
            # 过期即时盘，重抓时必须重新请求。cache_dir=None 表示不读也不写缓存。
            fetcher = Fetcher(cache_dir=None)
            # 模型倾向改由查询页「AI 分析」按钮手动触发，抓取入库不再自动跑 LLM，
            # 否则每期都要等十几场模型调用，用户感知就是「开得很慢」。
            analyses, actual_issue, source_url = analyze_issue(
                issue, fetcher=fetcher, max_workers=4, team_read=False
            )
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
        with review_lock:
            if review_state["running"]:
                review_state["error"] = "复盘任务进行中"
                return
            review_state.update(running=True, issue=issue, result="", error="")
        try:
            review_state["result"] = run_review(db_path, issue, refresh)
        except Exception as exc:  # noqa: BLE001
            review_state["error"] = f"复盘失败：{exc}"
        finally:
            review_state["running"] = False

    # State for the background refresh-odds job
    refresh_odds_state: dict = {"running": False, "issue": "", "result": "", "error": ""}
    refresh_odds_lock = threading.Lock()

    def _do_refresh_odds(issue: str) -> None:
        # running 标志由 do_POST 在锁内提前置位：POST 响应返回时轮询就能看到
        # running=true，重复点击在进入本函数前就被 do_POST 拒绝，不再污染 error。
        try:
            refresh_odds_state["result"] = run_refresh_odds(issue, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            refresh_odds_state["error"] = f"盘口刷新失败：{exc}"
        finally:
            refresh_odds_state["running"] = False

    # State for the background AI-analyze job
    ai_state: dict = {"running": False, "issue": "", "result": "", "error": ""}
    ai_lock = threading.Lock()

    def _do_ai(issue: str) -> None:
        try:
            ai_state["result"] = run_ai_analyze(issue, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            ai_state["error"] = f"AI 分析失败：{exc}"
        finally:
            ai_state["running"] = False

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

            if parsed.path == "/refresh-odds-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(refresh_odds_state, ensure_ascii=False).encode("utf-8"))
                return

            if parsed.path == "/ai-analyze-status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = dict(ai_state)
                # 抓取阶段把进度文件快照进去；文件不存在（LLM 阶段/没抓过）就不带 progress 字段
                from .main import _default_cache_dir as _cache_dir
                progress_file = _cache_dir() / ".ai_scrape_progress"
                if progress_file.is_file():
                    try:
                        progress = json.loads(progress_file.read_text(encoding="utf-8"))
                        if progress.get("issue") == payload.get("issue"):
                            payload["progress"] = progress
                    except (OSError, json.JSONDecodeError):
                        pass
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
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
                    leagues=list_leagues(Path(db_path), issue),
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
                    leagues=list_leagues(Path(db_path)),
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

            elif parsed.path == "/refresh-odds":
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
                # 在锁内完成「检查 + 置位」再起线程：POST 响应返回时 running 已为
                # true，前端首次轮询不会误判完成；重复点击返回 busy，不污染 error。
                with refresh_odds_lock:
                    if refresh_odds_state["running"]:
                        payload = json.dumps(
                            {"status": "busy", "issue": refresh_odds_state["issue"]},
                            ensure_ascii=False,
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(payload.encode("utf-8"))
                        return
                    refresh_odds_state.update(running=True, issue=issue, result="", error="")
                threading.Thread(target=_do_refresh_odds, args=(issue,), daemon=True).start()
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"accepted"}')
            elif parsed.path == "/ai-analyze":
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
                # Same lock-first pattern as refresh-odds: mark running before
                # spawning the thread so the poller sees it on the first tick.
                with ai_lock:
                    if ai_state["running"]:
                        payload = json.dumps(
                            {"status": "busy", "issue": ai_state["issue"]},
                            ensure_ascii=False,
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(payload.encode("utf-8"))
                        return
                    ai_state.update(running=True, issue=issue, result="", error="")
                threading.Thread(target=_do_ai, args=(issue,), daemon=True).start()
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


def _dedup_message(db_path: str | Path, issue: str) -> str | None:
    """若该期在 FETCH_DEDUP_MINUTES 内已入过库，返回提示文案；否则返回 None。"""
    from datetime import datetime

    last = latest_captured_at(Path(db_path), issue)
    if not last:
        return None
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return None
    if (datetime.now() - last_dt).total_seconds() < FETCH_DEDUP_MINUTES * 60:
        return f"{issue} 刚抓取过（{last}），{FETCH_DEDUP_MINUTES} 分钟内不重复入库"
    return None
