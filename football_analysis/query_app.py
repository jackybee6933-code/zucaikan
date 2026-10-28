from __future__ import annotations

import json
import base64
import binascii
from datetime import datetime, timedelta
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .bets import (
    BetError,
    bet_trend,
    bet_options,
    create_bet,
    parse_kickoff,
    delete_bet,
    export_bets_csv,
    list_bets,
    summarize_bets,
    update_bet,
)
from .backup import build_backup_bytes, restore_backup_bytes
from .http import Fetcher
from .llm import test_endpoint
from .llm_settings import public_settings, save_settings
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
import importlib.util
from pathlib import Path as _QPath


class QueryHTTPServer(ThreadingHTTPServer):
    """让 Ctrl+C 退出时不等待浏览器轮询线程。"""

    daemon_threads = True
    block_on_close = False


def _load_updater():
    """加载 updater 模块。

    单文件 exe 里 updater 通过 spec 的 hiddenimports 打进包内，直接按模块导入；
    源码运行时仓库根目录也在 sys.path 上。导入失败再按文件位置兜底。
    """
    try:
        import updater

        return updater
    except ImportError:
        pass
    base = _QPath(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("updater", base / "updater.py")
    if spec is None or spec.loader is None:
        raise ImportError("找不到 updater.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_UPDATER_LOADER = _load_updater


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key) or []
    return values[0] if values else default


CDP_DEFAULT = "http://127.0.0.1:9222"
CDP_HINT = (
    "已自动尝试启动 Chrome（独立 profile，cookie 复制），但仍连不上调试端口。请检查："
    "\n  1) 防火墙/杀毒软件是否拦截了 127.0.0.1:9222 或 Chrome"
    "\n  2) 企业策略（组策略/MDM）是否禁用了远程调试"
    "\n  3) 是否有别的程序占用了 9222 端口"
    "\n  4) 是否安装 Google Chrome（默认路径）"
    "Cookie 复制不能省：没有它页面会一直卡在 EdgeOne 验证页。"
)

FETCH_DEDUP_MINUTES = 30


def _format_market_review(label: str, summary: dict, key: str) -> str:
    total = int(summary.get(f"{key}_total") or 0)
    bets = int(summary.get(f"{key}_bets") or total)
    if not bets:
        return f"{label} 无推荐"
    hits = int(summary.get(f"{key}_hits") or 0)
    pct = f"{hits / total * 100:.0f}%" if total else "-"
    units = float(summary.get(f"{key}_units") or 0)
    priced = int(summary.get(f"{key}_priced") or 0)
    if priced:
        money = f"净收益 {float(summary.get(f'{key}_profit') or 0):+.1f}单位"
        if priced < bets:
            money += f"（{priced}/{bets}场有水位）"
    else:
        money = f"净结果 {units:+.1f}单位"
    detail = (
        f"赢{summary.get(f'{key}_wins', 0)}"
        f"/赢半{summary.get(f'{key}_half_wins', 0)}"
        f"/走{summary.get(f'{key}_pushes', 0)}"
        f"/输半{summary.get(f'{key}_half_losses', 0)}"
        f"/输{summary.get(f'{key}_losses', 0)}"
    )
    return f"{label} {hits}/{total} ({pct})，{money}（{detail}）"


def format_review(summary: dict) -> str:
    """把 review_issue 的统计格式化成查询页展示的一行文案。"""
    first = f"{summary['issue']} 已完成 {summary['scored']}/{summary['match_count']} 场"
    if summary.get("run_id") is not None:
        first += f"（run #{summary['run_id']}）"
    parts = [first]
    for label, key in (("亚盘", "asian"), ("大小球", "total")):
        parts.append(_format_market_review(label, summary, key))
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
        load_odds_pages_raw,
        pages_kind_age_hours,
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
    _clear_progress(cache_dir)
    needed_pages = 0
    # mtime 龄只说"文件很新"，不说里面有 shuju。产物存在但缺 shuju
    # （比如刚被"刷新盘口分歧"全量抓过、只带了 yazhi/daxiao），也要
    # 补抓一次 shuju，否则模型没有资料可读。
    has_shuju = any(
        isinstance(v, dict)
        and str(v.get("status") or "") in ("ok", "empty")
        for k, v in (load_odds_pages_raw(cache_dir, actual_issue) or {}).items()
        if k.rsplit("|", 1)[-1] == "shuju"
    )
    shuju_age = pages_kind_age_hours(cache_dir, actual_issue, "shuju")
    if shuju_age is not None and shuju_age <= PAGES_STALE_HOURS and has_shuju:
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


def _clear_progress(cache_dir: Path) -> None:
    """任务开头清掉上一轮留下的进度快照。

    查询页只能按 issue 匹配快照（不知道它属于哪一次任务），上一次 AI 分析
    留下的 `phase=llm 14/14` 会被下一次刷新读成「抓取 14/14 (100%)」闪一下。
    清掉之后到本次第一个快照写出之前，前端只显示"进行中"、不画进度条。
    """
    try:
        (cache_dir / ".ai_scrape_progress").unlink(missing_ok=True)
    except OSError:
        pass


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
    from .odds_pages import (
        PAGES_STALE_HOURS,
        load_odds_pages_raw,
        pages_age_hours,
        pages_kind_age_hours,
    )

    cache_dir = _default_cache_dir()
    _clear_progress(cache_dir)
    # 刷新盘口只负责亚盘/大小球；shuju（球队资料）由「AI 分析」按需补抓。
    missing = _missing_kinds(load_odds_pages_raw(cache_dir, issue))
    refresh_kinds = [
        kind
        for kind in ("yazhi", "daxiao")
        if kind in missing
        or (
            (kind_age := pages_kind_age_hours(cache_dir, issue, kind)) is None
            or kind_age > PAGES_STALE_HOURS
        )
    ]
    if not refresh_kinds:
        ages = [
            pages_kind_age_hours(cache_dir, issue, kind)
            for kind in ("yazhi", "daxiao")
        ]
        age_text = max(age for age in ages if age is not None)
        # "3h fresh" only proves odds are current for an already-started issue.
        # When fixtures are still in the future, compare them to the artifact
        # age and force a refresh if the artifact predates kickoff.
        stale_kickoffs: list[datetime] = []
        if db_path:
            matches = rebuild_analyses_from_db(Path(db_path), issue)
            artifact_at = datetime.now() - timedelta(hours=age_text)
            stale_kickoffs = [
                kickoff
                for match in matches
                if (
                    kickoff := parse_kickoff(
                        issue,
                        str(match.get("match_time") or ""),
                        str(match.get("captured_at") or ""),
                    )
                )
                and kickoff > artifact_at
            ]
        if not stale_kickoffs:
            return (
                f"{issue} \u76d8\u53e3\u4ea7\u7269\u662f {age_text:.1f} \u5c0f\u65f6\u524d\u7684\uff0c"
                f"\u672a\u8d85 {PAGES_STALE_HOURS:.0f}h \u9608\u503c\uff0c\u8df3\u8fc7\u91cd\u6293"
            )
        refresh_kinds = ["yazhi", "daxiao"]
        earliest = min(stale_kickoffs).strftime("%m-%d %H:%M")
        print(f"{issue} 盘口产物早于开赛时间 {earliest}，按未来期重抓")
    endpoint = cdp.rstrip("/")
    # Require a reachable Chrome debug port before starting the scraper.
    cdp_error = _ensure_cdp(endpoint)
    if cdp_error:
        return cdp_error
    try:
        scrape_error = _run_odds_scraper(issue, cache_dir, endpoint, kinds=refresh_kinds)
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


def _missing_kinds(raw: dict) -> list[str]:
    """返回亚盘/大小球缺哪些页种。

    页面整种缺失（一个 key 都没有）算缺失；key 状态为 timeout/error
    也算缺失需要重抓；empty 表示上游确实没开盘，不算缺失。
    """
    wanted = ["yazhi", "daxiao"]
    ok: set[str] = set()
    for key, page in (raw or {}).items():
        kind = key.rsplit("|", 1)[-1]
        if (
            kind in wanted
            and isinstance(page, dict)
            and str(page.get("status") or "") in ("ok", "empty")
        ):
            ok.add(kind)
    return [k for k in wanted if k not in ok]


def _run_odds_scraper(
    issue: str,
    cache_dir: Path,
    endpoint: str,
    *,
    kinds: list[str] | None = None,
    delay: float | None = None,
) -> str | None:
    """Run the packaged odds scraper. Return an error string, or None on success."""
    from . import scrape_odds_pages as mod
    if delay is None:
        from .app_settings import load_scrape_delay
        delay = load_scrape_delay()
    argv = [
        "--issue", issue,
        "--cache-dir", str(cache_dir),
        "--cdp", endpoint,
        "--delay", str(delay),
    ]
    if kinds:
        argv.extend(["--kinds", *kinds])
    code = mod.main(argv)
    if code != 0:
        return f"{issue} 抓取未完成（退出码 {code}），已抓页面已保存，重试会从断点继续"
    return None


def serve_query(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    reports_dir: str | Path | None = None,
    default_issue: str = "",
) -> None:
    db_path = str(Path(db_path))
    settings_path = Path(db_path).parent / "llm_settings.json"
    reports_dir = Path(reports_dir) if reports_dir else Path(db_path).parent.parent / "reports"
    from .main import _default_cache_dir

    cache_dir = _default_cache_dir()
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
    update_state: dict = {
        "running": False,
        "phase": "idle",
        "downloaded": 0,
        "total": 0,
        "attempt": 0,
        "attempts": 0,
        "result": "",
        "error": "",
    }
    update_lock = threading.Lock()

    def _do_update_install() -> None:
        try:
            _updater = _UPDATER_LOADER()
            info = _updater.check_update()
            if not info["update_available"]:
                with update_lock:
                    update_state.update(
                        running=False, phase="complete", result="已是最新版本"
                    )
                return

            def _progress(downloaded: int, total: int) -> None:
                with update_lock:
                    update_state.update(
                        downloaded=downloaded,
                        total=total,
                        phase="download",
                    )

            def _phase(value: str) -> None:
                with update_lock:
                    update_state["phase"] = value

            archive, package_type = _updater.download_package(
                info, progress=_progress, phase=_phase
            )
            with update_lock:
                update_state["phase"] = "installing"
            result = _updater.install_downloaded(
                archive, package_type=package_type
            )
            with update_lock:
                update_state.update(
                    running=False,
                    phase="complete",
                    result=(
                        f"升级包已校验，更新器将在 {result['wait_seconds']} 秒内完成替换并重启"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            with update_lock:
                update_state.update(
                    running=False,
                    phase="error",
                    error=f"升级失败：{exc}",
                )

    def _do_ai(issue: str) -> None:
        try:
            ai_state["result"] = run_ai_analyze(issue, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            ai_state["error"] = f"AI 分析失败：{exc}"
        finally:
            ai_state["running"] = False

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
                payload = dict(refresh_odds_state)
                # 抓取阶段把进度文件快照进去；文件不存在（跳过重抓/LLM 阶段/没抓过）就不带 progress 字段
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
            if parsed.path == "/update/install-status":
                with update_lock:
                    self._json(200, dict(update_state))
                return
            if parsed.path == "/bet-options":
                params = parse_qs(parsed.query)
                issue = _first(params, "issue")
                match_no = _first(params, "match_no")
                if not issue or not match_no:
                    self._json(400, {"error": "issue and match_no are required"})
                    return
                try:
                    self._json(200, bet_options(db_path, issue, match_no))
                except BetError as exc:
                    self._json(exc.status, {"error": str(exc)})
                return
            if parsed.path == "/settings":
                self._json(200, {
                    **public_settings(settings_path),
                })
                return
            if parsed.path == "/backup":
                try:
                    data = build_backup_bytes(db_path, settings_path)
                    self._json(200, {
                        "filename": f"football_analysis_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip",
                        "data": base64.b64encode(data).decode("ascii"),
                    })
                except (OSError, ValueError) as exc:
                    self._json(500, {"error": f"备份失败：{exc}"})
                return
            if parsed.path == "/update/check":
                _updater = _load_updater()
                started = time.monotonic()
                try:
                    info = _updater.check_update()
                    self._json(200, info)
                except _updater.UpdateError as exc:
                    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
                    try:
                        cached = _updater.load_cached_update()
                    except _updater.UpdateError:
                        self._json(502, {
                            "error": str(exc),
                            "manifest_url": _updater._manifest_url(),
                            "elapsed_ms": elapsed_ms,
                        })
                    else:
                        cached["offline_error"] = str(exc)
                        cached["elapsed_ms"] = elapsed_ms
                        self._json(200, cached)
                return
            if parsed.path == "/bets":
                params = parse_qs(parsed.query)
                issue = _first(params, "issue")
                self._json(200, {"bets": list_bets(db_path, issue=issue)})
                return
            if parsed.path == "/bets.csv":
                params = parse_qs(parsed.query)
                issue = _first(params, "issue")
                try:
                    text = export_bets_csv(db_path, issue=issue)
                    data = ("\ufeff" + text).encode("utf-8")
                    safe_issue = "".join(ch for ch in issue if ch.isdigit()) or "all"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="bets_{safe_issue}.csv"',
                    )
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except BetError as exc:
                    self._json(exc.status, {"error": str(exc)})
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
            # Root page (URL with no "issue" key at all) falls back to the
            # latest issue so the list shows only current-period matches;
            # explicit ?issue= (empty) from the form still means "all issues".
            issue = _first(params, "issue")
            if "issue" not in params and default_issue:
                issue = default_issue
            team = _first(params, "team")
            level = _first(params, "level")
            kind = _first(params, "kind")
            attention_only = _first(params, "attention") == "1"
            latest_only = "latest" not in params or _first(params, "latest") == "1"
            bet_rows = list_bets(Path(db_path), issue=issue)
            bet_stats = summarize_bets(bet_rows)
            bet_trend_rows = bet_trend(Path(db_path))

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
                    bets=bet_rows,
                    bet_stats=bet_stats,
                    bet_trend=bet_trend_rows,
                    cache_dir=cache_dir,
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
                    bets=bet_rows,
                    bet_stats=bet_stats,
                    bet_trend=bet_trend_rows,
                    cache_dir=cache_dir,
                )
            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            def _body() -> dict:
                content_length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(content_length).decode("utf-8") if content_length else ""
                return json.loads(raw) if raw else {}

            if parsed.path == "/settings":
                data = _body()
                payload: dict[str, dict[str, str]] = {}
                for name in ("primary", "fallback"):
                    incoming = data.get(name) if isinstance(data.get(name), dict) else {}
                    item: dict[str, str] = {}
                    for field in ("base_url", "model"):
                        if field in incoming:
                            item[field] = str(incoming.get(field) or "")
                    if incoming.get("clear_api_key"):
                        item["api_key"] = ""
                    elif str(incoming.get("api_key") or "").strip():
                        item["api_key"] = str(incoming["api_key"]).strip()
                    payload[name] = item
                try:
                    save_settings(payload, settings_path)
                    self._json(200, {
                        **public_settings(settings_path),
                    })
                except OSError as exc:
                    self._json(500, {"error": f"设置保存失败：{exc}"})
                return

            if parsed.path == "/backup/restore":
                data = _body()
                encoded = str(data.get("data") or "")
                if not encoded:
                    self._json(400, {"error": "备份文件不能为空"})
                    return
                try:
                    raw = base64.b64decode(encoded, validate=True)
                    safety = restore_backup_bytes(raw, db_path, settings_path)
                    self._json(200, {
                        "ok": True,
                        "safety_backup": str(safety) if safety else "",
                    })
                except (OSError, ValueError, binascii.Error) as exc:
                    self._json(400, {"error": f"恢复失败：{exc}"})
                return

            if parsed.path == "/settings/test":
                data = _body()
                name = str(data.get("name") or "")
                incoming = data.get(name) if name in ("primary", "fallback") else None
                if not isinstance(incoming, dict):
                    self._json(400, {"error": "接入点参数无效"})
                    return
                try:
                    result = test_endpoint(
                        name,
                        base_url=str(incoming.get("base_url") or ""),
                        api_key=str(incoming.get("api_key") or "").strip(),
                        model=str(incoming.get("model") or ""),
                        settings_path=settings_path,
                        use_saved_key=not bool(incoming.get("clear_api_key")),
                    )
                    self._json(200, result)
                except Exception as exc:  # noqa: BLE001
                    self._json(200, {"ok": False, "message": f"测试失败：{exc}"})
                return

            if parsed.path == "/update/install":
                with update_lock:
                    if update_state["running"]:
                        self._json(200, {"ok": True, "busy": True})
                        return
                    update_state.update({
                        "running": True,
                        "phase": "starting",
                        "downloaded": 0,
                        "total": 0,
                        "result": "",
                        "error": "",
                    })
                worker = threading.Thread(target=_do_update_install, daemon=True)
                worker.start()
                self._json(202, {"ok": True, "started": True})
                return

            if parsed.path == "/bets":
                data = _body()
                issue = str(data.get("issue") or "")
                match_no = str(data.get("match_no") or "")
                if not issue or not match_no:
                    self._json(400, {"error": "issue and match_no are required"})
                    return
                try:
                    self._json(
                        201,
                        create_bet(
                            db_path,
                            issue,
                            match_no,
                            str(data.get("market") or ""),
                            str(data.get("selection") or ""),
                            data.get("stake"),
                        ),
                    )
                except BetError as exc:
                    self._json(exc.status, {"error": str(exc)})
                return
            if parsed.path == "/bets/update":
                data = _body()
                try:
                    bet_id = int(data.get("id") or 0)
                except (TypeError, ValueError):
                    bet_id = 0
                if not bet_id:
                    self._json(400, {"error": "id is required"})
                    return
                try:
                    self._json(
                        200,
                        update_bet(
                            db_path,
                            bet_id,
                            selection=data.get("selection"),
                            stake=data.get("stake"),
                        ),
                    )
                except BetError as exc:
                    self._json(exc.status, {"error": str(exc)})
                return
            if parsed.path == "/bets/delete":
                data = _body()
                try:
                    bet_id = int(data.get("id") or 0)
                except (TypeError, ValueError):
                    bet_id = 0
                if not bet_id:
                    self._json(400, {"error": "id is required"})
                    return
                try:
                    self._json(200, delete_bet(db_path, bet_id))
                except BetError as exc:
                    self._json(exc.status, {"error": str(exc)})
                return
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
        server = QueryHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"无法监听 {host}:{port}，请检查端口是否被占用或更换端口。错误: {exc}")
        return
    url = f"http://{host}:{port}/"
    print(f"足彩分析系统1.3.22 已启动：{url}")
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

