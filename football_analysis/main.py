from __future__ import annotations

import argparse
from pathlib import Path

from .http import Fetcher
from .pipeline import analyze_issue
from .query_app import serve_query
from .query_report import render_query_html
from .report import print_console, write_report_files
from .store import default_db_path, list_issues, query_movements, save_issue_movements
from .store import update_match_scores


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
    query.add_argument("--port", type=int, default=8765, help="查询页端口")
    query.add_argument("--no-open", action="store_true", help="启动查询页时不自动打开浏览器")
    query.add_argument("--refresh-scores", action="store_true", help="先刷新指定期的最终比分再输出")

    review = subparsers.add_parser("review", help="赛后复盘: 刷新比分并统计推荐命中率")
    _add_common_args(review)
    review.add_argument("--cache-dir", default=None, help="原始页面缓存目录, 缺省不使用缓存")
    review.add_argument("--refresh", action="store_true", help="复盘前先重新抓取最终比分")
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


def _run_analyze(args: argparse.Namespace) -> int:
    cache_dir = None if args.no_cache or not args.cache_dir else Path(args.cache_dir)
    fetcher = Fetcher(cache_dir=cache_dir)
    if not args.issue and not args.no_cache:
        _backfill_previous_issue(cache_dir, _resolve_db_path(args.db_path), fetcher, args.workers)
    analyses, issue, source_url = analyze_issue(
        args.issue,
        fetcher=fetcher,
        max_workers=args.workers,
    )
    print_console(analyses, issue)
    html_path = write_report_files(
        args.output_dir,
        analyses,
        issue,
        source_url,
    )
    print(f"\nHTML 报告: {html_path}")
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


def _refresh_scores(db_path: Path, issue: str, cache_dir: Path | None) -> None:
    """Fetch final scores for the latest-run rows of an issue and write them back to the DB."""
    from .pipeline import collect_sfc_matches, LIVE_DETAIL_URL, extract_match_score, _parse_detail_ids
    from .http import Fetcher

    fetcher = Fetcher(cache_dir=cache_dir)
    try:
        matches, actual = collect_sfc_matches(issue, fetcher)
    except Exception as exc:
        print(f"无法抓取 {issue} 场次列表: {exc}")
        return
    scores: dict[str, str] = {}
    for row in matches:
        try:
            html = fetcher.fetch(
                LIVE_DETAIL_URL.format(fixture_id=row["fixture_id"]),
                cache_key=f"detail:{row['fixture_id']}",
            )
            scores[str(row["match_no"])] = extract_match_score(html)
        except Exception as exc:
            print(f"  {row['match_no']} 场次比分抓取失败: {exc}")
    updated = update_match_scores(db_path, actual, scores)
    filled = sum(1 for v in scores.values() if v)
    print(f"刷新 {issue} 最终比分: {filled}/{len(matches)} 场有值, DB 更新 {updated} 条")


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
    if args.refresh:
        _refresh_scores(db_path, issue, cache_dir)
    analyses = load_issue_snapshot(db_path, issue)
    done = [a for a in analyses if a.get("final_score")]
    print(f"复盘 {issue}: 已完成 {len(done)}/{len(analyses)} 场")
    if not done:
        return 0

    def _score(item: dict) -> tuple[str, str]:
        score = str(item.get("final_score") or "")
        home, away = "0", "0"
        if "-" in score:
            home, away = score.split("-", 1)
        return (home.strip() or "0"), (away.strip() or "0")

    asian_hits = asian_total = 0
    total_hits = total_total = 0
    for item in done:
        home_g, away_g = _score(item)
        try:
            hg, ag = int(home_g), int(away_g)
        except ValueError:
            continue
        goal_diff = hg - ag
        total_goals = hg + ag
        odds = item.get("odds_analysis") or {}
        asian_rec = odds.get("asian_handicap") or {}
        if asian_rec.get("available") and asian_rec.get("action") in ("主推", "轻仓"):
            line = float((item.get("market", {}).get("asian") or {}).get("home_line") or 0)
            # home_line 符号: 负 = 主队让球(如 -1 为主让一球), 正 = 主队受让
            # 上盘 = 让球方: line<0 时主队为让球方, line>0 时客队为让球方
            if line < 0:
                home_handicap = goal_diff + line  # 主队让 |line| 球
            elif line > 0:
                home_handicap = goal_diff - line  # 客队让 line 球(主队受让 line)
            else:
                home_handicap = goal_diff
            is_home_upper = asian_rec.get("recommended_side") == "主队赢盘"
            # 上盘赢: 让球方盘口后净胜 > 0; 下盘赢: < 0; 走水: == 0
            win_side = home_handicap if is_home_upper else -home_handicap
            hit = win_side > 0
            asian_total += 1
            asian_hits += 1 if hit else 0
        total_rec = odds.get("total_goals") or {}
        if total_rec.get("available") and total_rec.get("action") in ("主推", "轻仓"):
            line = float((item.get("market", {}).get("total") or {}).get("line") or 0)
            is_over = "大" in str(total_rec.get("recommended_side", ""))
            hit = total_goals > line if is_over else total_goals < line
            total_total += 1
            total_hits += 1 if hit else 0
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


def _run_query(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db_path)
    if args.serve:
        serve_query(db_path, port=args.port, open_browser=not args.no_open)
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
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "movement_query.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n查询页：{html_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else None
    if raw is None:
        import sys
        raw = sys.argv[1:]
    if not raw or raw[0] not in {"analyze", "query", "review"}:
        raw = ["analyze", *raw]
    args = parser.parse_args(raw)
    if args.command == "query":
        return _run_query(args)
    if args.command == "review":
        return _run_review(args)
    return _run_analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())
