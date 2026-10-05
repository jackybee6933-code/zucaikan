from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .http import Fetcher
from .pipeline import analyze_issue
from .query_app import serve_query
from .report_index import build_issue_report_index
from .open_browser import open_in_chrome
from .query_report import render_query_html
from .report import print_console, write_report_files
from .store import default_db_path, list_issues, query_movements, save_issue_movements
from .store import update_match_scores

# 查询页默认端口；analyze→serve 的组合流程没有 query 子命令的 Namespace 可取，直接用这个常量。
DEFAULT_QUERY_PORT = 8765



def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--issue",
        default="",
        help="胜负彩期号，例如 26120；分析时缺省自动抓取最新一期",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite 路径；缺省 ./data/football_analysis.sqlite",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="足球胜负彩数据分析 MVP：采集对阵/亚盘/大小盘/往绩并输出胜平负概率",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="采集并分析一期比赛")
    _add_common_args(analyze)
    analyze.add_argument(
        "--cache-dir",
        default=None,
        help="原始页面缓存目录；不传则每次实时抓取",
    )
    analyze.add_argument(
        "--output-dir",
        default="reports",
        help="报告输出目录，缺省 ./reports",
    )
    analyze.add_argument("--workers", type=int, default=4, help="球队页并发抓取数")
    analyze.add_argument("--no-cache", action="store_true", help="不使用本地缓存")
    analyze.add_argument("--no-db", action="store_true", help="不写入本地异动数据库")
    analyze.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    analyze.add_argument("--narrate", action="store_true", help="调用大模型为变盘异动场次生成解读（需设置 LLM_API_KEY）")

    query = subparsers.add_parser("query", help="查询已入库的变盘异动")
    _add_common_args(query)
    query.add_argument("--team", default="", help="按主客队或联赛模糊查询")
    query.add_argument("--level", default="", help="重点关注 / 关注 / 稳定 / 无数据")
    query.add_argument("--kind", default="", help="升盘 / 降盘 / 水位反转 / 水位摆动")
    query.add_argument("--attention-only", action="store_true", help="只看达到关注线的异动")
    query.add_argument("--all-runs", action="store_true", help="不过滤为每期最新一次抓取")
    query.add_argument("--limit", type=int, default=200, help="最多返回条数")
    query.add_argument("--output-dir", default="reports", help="查询 HTML 输出目录")
    query.add_argument("--serve", action="store_true", help="启动本地查询页")
    query.add_argument("--port", type=int, default=DEFAULT_QUERY_PORT, help="查询页端口")
    query.add_argument("--no-open", action="store_true", help="启动查询页时不自动打开浏览器")
    query.add_argument("--refresh-scores", action="store_true", help="先刷新指定期的最终比分再输出")

    refresh = subparsers.add_parser(
        "refresh", help="按产物年龄决定是否重抓单场页盘口"
    )
    refresh.add_argument("--issue", default="", help="期号；留空则用库里最新一期")
    refresh.add_argument("--cache-dir", default="cache", help="产物所在目录，缺省 cache")
    refresh.add_argument("--delay", type=float, default=8.0, help="抓取时页面间隔秒数")
    refresh.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome 调试端口")
    refresh.add_argument("--force", action="store_true", help="即使未过期也重抓")
    refresh.add_argument("--db-path", default=None, help="仅用于确定期号")

    review = subparsers.add_parser("review", help="赛后复盘: 刷新比分并统计推荐命中率")
    _add_common_args(review)
    review.add_argument("--cache-dir", default=None, help="原始页面缓存目录, 缺省不使用缓存")
    review.add_argument("--refresh", action="store_true", help="复盘前先重新抓取最终比分")
    review.add_argument("--trend", action="store_true", help="跨期命中率趋势统计")
    return parser


def _resolve_db_path(value: str | None) -> Path:
    return Path(value) if value else default_db_path()

def _backfill_previous_issue(cache_dir: Path | None, db_path: Path, fetcher: Fetcher, workers: int) -> None:
    """If running auto-latest mode, backfill the previous issue's final scores from cache."""
    if not cache_dir:
        return
    try:
        issues = [row["issue"] for row in list_issues(db_path)]
    except Exception:  # noqa: BLE001
        return
    if not issues:
        return
    latest = issues[0]  # list_issues returns last_captured_at DESC
    try:
        prev_num = int(latest[-3:]) - 1
        prev_issue = latest[:2] + str(prev_num).zfill(3)
    except ValueError:
        return
    if prev_issue not in issues:
        return
    print(f"回填上期 {prev_issue} 比分（使用缓存）...")
    try:
        analyses, prev_issue_out, _ = analyze_issue(prev_issue, fetcher=fetcher, max_workers=workers)
        saved = save_issue_movements(db_path, analyses, prev_issue_out, "")
        print(f"  上期 DB 更新：run_id={saved['run_id']}  异动={saved['attention_count']}/{saved['match_count']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  上期回填失败（不影响最新期）：{exc}")


def _run_analyze(args: argparse.Namespace, *, open_report: bool = True) -> int:
    cache_dir = None if args.no_cache or not args.cache_dir else Path(args.cache_dir)
    fetcher = Fetcher(cache_dir=cache_dir)
    if not args.issue and not args.no_cache:
        _backfill_previous_issue(cache_dir, _resolve_db_path(args.db_path), fetcher, args.workers)
    analyses, issue, source_url = analyze_issue(
        args.issue,
        fetcher=fetcher,
        max_workers=args.workers,
    )
    if args.narrate:
        from .llm import narrate_movements

        narrate_movements(analyses)
    print_console(analyses, issue)
    html_path = write_report_files(
        args.output_dir,
        analyses,
        issue,
        source_url,
    )
    print(f"\nHTML 报告: {html_path}")
    if open_report and not args.no_open:
        open_in_chrome(html_path)
    if not args.no_db:
        saved = save_issue_movements(
            _resolve_db_path(args.db_path),
            analyses,
            issue,
            source_url,
        )
        print(f"  DB:   {saved['db_path']}  run_id={saved['run_id']}  异动={saved['attention_count']}/{saved['match_count']}")
    return 0


def _print_query_rows(rows: list[dict]) -> None:
    if not rows:
        print("没有符合条件的异动记录。")
        return
    print(f"共 {len(rows)} 条")
    print("-" * 120)
    for row in rows:
        print(
            f"{row.get('issue','')} {str(row.get('match_no') or ''):>2} "
            f"{row.get('home_team','')} vs {row.get('away_team','')} "
            f"[{row.get('level','')}] {row.get('kind','')} "
            f"{row.get('initial_line','')} -> {row.get('current_line','')} "
            f"{row.get('reason','')}"
        )


def _refresh_scores(db_path: Path, issue: str, cache_dir: Path | None) -> dict:
    """抓取最终比分写回 DB，返回统计摘要（不打印，CLI 与查询页共用）。"""
    from .pipeline import collect_sfc_matches, LIVE_DETAIL_URL, extract_match_score, _parse_detail_ids
    from .http import Fetcher

    fetcher = Fetcher(cache_dir=cache_dir)
    try:
        matches, actual = collect_sfc_matches(issue, fetcher)
    except Exception as exc:
        return {"issue": issue, "error": f"无法抓取 {issue} 场次列表: {exc}"}
    scores: dict[str, str] = {}
    errors: list[str] = []
    for row in matches:
        try:
            html = fetcher.fetch(
                LIVE_DETAIL_URL.format(fixture_id=row["fixture_id"]),
                cache_key=f"detail:{row['fixture_id']}",
            )
            scores[str(row["match_no"])] = extract_match_score(html)
        except Exception as exc:
            errors.append(f"  {row['match_no']} 场次比分抓取失败: {exc}")
    updated = update_match_scores(db_path, actual, scores)
    filled = sum(1 for v in scores.values() if v)
    return {
        "issue": actual,
        "filled": filled,
        "match_count": len(matches),
        "updated": updated,
        "errors": errors,
    }


def _print_refresh(result: dict) -> None:
    """把 _refresh_scores 的摘要打印出来。"""
    if result.get("error"):
        print(result["error"])
        return
    for line in result.get("errors") or []:
        print(line)
    print(
        f"刷新 {result['issue']} 最终比分: "
        f"{result['filled']}/{result['match_count']} 场有值, DB 更新 {result['updated']} 条"
    )


def review_issue(
    db_path: Path,
    issue: str,
    *,
    refresh: bool,
    cache_dir: Path | None = None,
) -> dict:
    """赛后复盘核心：返回该期命中率统计，不打印，供 CLI 与查询页共用。"""
    from .store import load_issue_snapshot

    refreshed: dict = {}
    if refresh:
        refreshed = _refresh_scores(db_path, issue, cache_dir)
    analyses = load_issue_snapshot(db_path, issue)
    done = [a for a in analyses if a.get("final_score")]
    return {
        "issue": issue,
        "match_count": len(analyses),
        "scored": len(done),
        "refreshed": refreshed,
        **_issue_hit_rates(done),
    }


def _issue_hit_rates(analyses: list[dict]) -> dict:
    """Count asian / over-under recommendation hits for one issue snapshot."""
    asian_hits = asian_total = 0
    total_hits = total_total = 0
    for item in analyses:
        score = str(item.get("final_score") or "")
        if "-" not in score:
            continue
        try:
            hg, ag = (int(part.strip() or "0") for part in score.split("-", 1))
        except ValueError:
            continue
        goal_diff = hg - ag
        total_goals = hg + ag
        odds = item.get("odds_analysis") or {}
        asian_rec = odds.get("asian_handicap") or {}
        if asian_rec.get("available") and asian_rec.get("action") in ("主推", "轻仓"):
            line = float((item.get("market", {}).get("asian") or {}).get("home_line") or 0)
            if line < 0:
                home_handicap = goal_diff + line
            elif line > 0:
                home_handicap = goal_diff - line
            else:
                home_handicap = goal_diff
            is_home_upper = asian_rec.get("recommended_side") == "主队赢盘"
            win_side = home_handicap if is_home_upper else -home_handicap
            asian_total += 1
            asian_hits += 1 if win_side > 0 else 0
        total_rec = odds.get("total_goals") or {}
        if total_rec.get("available") and total_rec.get("action") in ("主推", "轻仓"):
            line = float((item.get("market", {}).get("total") or {}).get("line") or 0)
            is_over = "大" in str(total_rec.get("recommended_side", ""))
            hit = total_goals > line if is_over else total_goals < line
            total_total += 1
            total_hits += 1 if hit else 0
    return {
        "asian_hits": asian_hits,
        "asian_total": asian_total,
        "total_hits": total_hits,
        "total_total": total_total,
    }


def _print_trend(db_path: Path) -> None:
    """Print cross-issue hit-rate trend from the DB, ordered by issue ascending."""
    from .store import load_issue_snapshot

    issues = sorted(list_issues(db_path), key=lambda row: row["issue"])

    def _pct(hits: int, total: int) -> str:
        return f"{hits / total * 100:.0f}%" if total else "-"

    agg = {"asian_hits": 0, "asian_total": 0, "total_hits": 0, "total_total": 0}
    prev: dict | None = None
    print("=" * 70)
    print("命中率趋势（仅主推/轻仓，走水不计入命中率）")
    print("=" * 70)
    print(f"{'期号':<8}{'已完成':<10}{'亚盘命中':<10}{'亚盘率':<8}{'大小命中':<12}{'大小率':<8}")
    for info in issues:
        snapshot = load_issue_snapshot(db_path, info["issue"])
        done = [a for a in snapshot if a.get("final_score")]
        rates = _issue_hit_rates(done)
        for key in agg:
            agg[key] += rates[key]
        delta_text = ""
        if prev is not None:
            deltas = []
            for label, cur, before in (
                ("亚", rates["asian_hits"], prev["asian_hits"]),
                ("大", rates["total_hits"], prev["total_hits"]),
            ):
                d = cur - before
                if d > 0:
                    deltas.append(f"{label}盘+{d}")
                elif d < 0:
                    deltas.append(f"{label}盘{d}")
            if deltas:
                delta_text = f"环比: {', '.join(deltas)}"
        scored = f"{len(done)}/{len(snapshot)}"
        asian_cell = f"{rates['asian_hits']}/{rates['asian_total']}"
        total_cell = f"{rates['total_hits']}/{rates['total_total']}"
        print(
            f"{info['issue']:<8}{scored:<10}{asian_cell:<10}"
            f"{_pct(rates['asian_hits'], rates['asian_total']):<8}{total_cell:<12}"
            f"{_pct(rates['total_hits'], rates['total_total']):<8}"
        )
        if delta_text:
            print(f"{'':<8}{delta_text}")
        prev = rates
    print("-" * 70)
    print(
        f"{'合计':<8}{'':<10}"
        f"{str(agg['asian_hits']) + '/' + str(agg['asian_total']):<10}"
        f"{_pct(agg['asian_hits'], agg['asian_total']):<8}"
        f"{str(agg['total_hits']) + '/' + str(agg['total_total']):<12}"
        f"{_pct(agg['total_hits'], agg['total_total']):<8}"
    )
    print("口径: 仅统计主推/轻仓推荐, 走水(盘口后净胜=0)不计入命中")
    
    return None




def _run_refresh(args: argparse.Namespace) -> int:
    """产物还新就直接返回；过期了才启动抓取。"""
    import subprocess
    import urllib.request

    from .odds_pages import PAGES_STALE_HOURS, pages_age_hours

    cache_dir = Path(args.cache_dir)
    issue = args.issue
    if not issue:
        rows = list_issues(_resolve_db_path(args.db_path))
        if not rows:
            print("库中暂无数据，先用 analyze 抓取一期。")
            return 1
        issue = rows[0]["issue"]

    age = pages_age_hours(cache_dir, issue)
    if age is None:
        print(f"{issue} 尚无盘口产物，需要抓取。")
    elif age <= PAGES_STALE_HOURS:
        if not args.force:
            print(
                f"{issue} 盘口产物是 {age:.1f} 小时前抓的，"
                f"未超过 {PAGES_STALE_HOURS:.0f}h 阈值，跳过。"
            )
            return 0
        print(f"{issue} 产物仅 {age:.1f} 小时，但 --force 指定重抓。")
    else:
        print(
            f"{issue} 盘口产物已过期 {age:.1f} 小时"
            f"（阈值 {PAGES_STALE_HOURS:.0f}h），开始重抓。"
        )

    endpoint = args.cdp.rstrip("/")
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=5) as resp:
            resp.read()
    except OSError:
        print(f"\n连不上 {endpoint}，请先把 Chrome 启动到调试模式：")
        print("  1) 完全退出 Chrome（托盘图标也要退）")
        print("  2) python setup_cdp_profile.py")
        return 1

    script = Path(__file__).resolve().parent.parent / "tools" / "scrape_odds_pages.py"
    if not script.is_file():
        print(f"找不到抓取脚本 {script}")
        return 2

    cmd = [
        sys.executable, str(script),
        "--issue", issue,
        "--cache-dir", str(cache_dir),
        "--delay", str(args.delay),
        "--cdp", endpoint,
    ]
    code = subprocess.call(cmd)
    if code != 0:
        print(f"\n抓取未完成（退出码 {code}），原产物未被覆盖。")
        return code

    new_age = pages_age_hours(cache_dir, issue)
    if new_age is None:
        print("\u4ea7物仍不存在，请查看上方输出。")
        return 1
    print(f"\u5b8c成，产物年龄 {new_age:.1f} 小时。")
    return 0


def _run_review(args: argparse.Namespace) -> int:
    """Compute hit-rate statistics for completed matches of an issue."""
    from .store import load_issue_snapshot

    db_path = _resolve_db_path(args.db_path)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    issue = args.issue
    if not issue:
        rows = list_issues(db_path)
        if not rows:
            print("库中暂无数据,先用 analyze 抓取一期")
            return 1
        issue = rows[0]["issue"]
    if args.trend:
        if args.refresh:
            _print_refresh(_refresh_scores(db_path, issue, cache_dir))
        _print_trend(db_path)
        return 0

    summary = review_issue(db_path, issue, refresh=args.refresh, cache_dir=cache_dir)
    if summary["refreshed"]:
        _print_refresh(summary["refreshed"])
    analyses = load_issue_snapshot(db_path, issue)
    done = [a for a in analyses if a.get("final_score")]
    print(f"复盘 {issue}: 已完成 {summary['scored']}/{summary['match_count']} 场")
    if not done:
        return 0

    rates = summary
    asian_hits, asian_total = rates["asian_hits"], rates["asian_total"]
    total_hits, total_total = rates["total_hits"], rates["total_total"]
    print("-" * 60)
    print(f"亚盘推荐: {asian_hits}/{asian_total} 命中" if asian_total else "亚盘推荐: 无数据")
    print(f"大小球推荐: {total_hits}/{total_total} 命中" if total_total else "大小球推荐: 无数据")
    print("-" * 60)
    for item in done:
        odds = item.get("odds_analysis") or {}
        asian_rec = odds.get("asian_handicap") or {}
        total_rec = odds.get("total_goals") or {}
        asian_txt = f"{asian_rec.get('recommended_side','?')}({asian_rec.get('action','')})" if asian_rec.get("available") else "-"
        total_txt = f"{total_rec.get('recommended_side','?')}({total_rec.get('action','')})" if total_rec.get("available") else "-"
        print(f"{item.get('match_no',''):>2} {item.get('home_team','')} vs {item.get('away_team','')} {item.get('final_score','')}  亚盘:{asian_txt}  大小:{total_txt}")
    return 0


def _app_root() -> Path:
    """exe 所在目录；开发态为当前目录。cache/data/reports 都挂在它下面。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _default_cache_dir() -> Path:
    return _app_root() / "cache"


def _run_analyze_then_serve(parser: argparse.ArgumentParser) -> int:
    """exe 双击的默认流程：抓取入库后直接起异动查询服务，并打开查询页。"""
    # cache / db / reports 三者都必须挂 exe 目录：
    # - cache_dir 为 None 时 Fetcher 没有 cache.root，odds_pages_<期号>.json 加载不到
    # - db_path 缺省走 Path.cwd()，换个方式启动 exe 就会写到别处去
    # - output_dir 同理，报告与查询页的「分析报告」链接会指到别处
    db_path = _app_root() / "data" / "football_analysis.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    args = parser.parse_args(
        [
            "analyze",
            "--cache-dir",
            str(_default_cache_dir()),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(_app_root() / "reports"),
        ]
    )
    try:
        result = _run_analyze(args, open_report=False)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n分析失败，未启动异动查询服务。")
        if getattr(sys, "frozen", False):
            input("按回车键退出...")
        return 1
    if result:
        return result

    port = DEFAULT_QUERY_PORT
    print(f"\n即将启动异动查询服务： http://127.0.0.1:{port}/")
    print("关闭本窗口或按 Ctrl+C 即可停止服务。\n")
    try:
        serve_query(
            db_path,
            port=port,
            open_browser=not args.no_open,
            reports_dir=Path(args.output_dir),
        )
    except KeyboardInterrupt:
        print("\n已停止查询服务。")
    return 0


def _run_query(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db_path)
    if args.serve:
        serve_query(db_path, port=args.port, open_browser=not args.no_open, reports_dir=Path(args.output_dir))
        return 0
    rows = query_movements(
        db_path,
        issue=args.issue,
        team=args.team,
        level=args.level,
        kind=args.kind,
        attention_only=args.attention_only,
        latest_only=not args.all_runs,
        limit=args.limit,
    )
    _print_query_rows(rows)
    report_links = build_issue_report_index(Path(args.output_dir))
    html = render_query_html(
        rows,
        issues=list_issues(db_path),
        issue=args.issue,
        team=args.team,
        level=args.level,
        kind=args.kind,
        attention_only=args.attention_only,
        latest_only=not args.all_runs,
        db_path=str(db_path),
        report_links=report_links,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "movement_query.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n查询页：{html_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    if not raw:
        return _run_analyze_then_serve(parser)
    if raw[0] not in {"analyze", "query", "review", "refresh"}:
        raw = ["query", "--serve", *raw]
    args = parser.parse_args(raw)
    if args.command == "query":
        return _run_query(args)
    if args.command == "review":
        return _run_review(args)
    if args.command == "refresh":
        return _run_refresh(args)
    result = _run_analyze(args)
    if getattr(sys, "frozen", False):
        input("\nPress Enter to close this window...")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
