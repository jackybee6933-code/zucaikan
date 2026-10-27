from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

from .http import Fetcher
from .bets import bet_trend, export_bets_csv, list_bets, summarize_bets
from .backup import backup_summary, restore_backup, write_backup
from .app_settings import load_scrape_delay
from .ev_ledger import save_ev_ledger
from .pipeline import analyze_issue, collect_sfc_matches
from .query_app import serve_query
from .report_index import build_issue_report_index
from .open_browser import open_in_chrome
from .query_report import render_query_html
from .settlement import profit_units, settle_asian, settle_total, summarize
from .store import list_leagues
from .report import print_console, write_report_files
from .store import (
    default_db_path,
    list_issues,
    load_issue_scores,
    query_movements,
    save_issue_movements,
)
from .store import update_match_scores

# 查询页默认端口；analyze→serve 的组合流程没有 query 子命令的 Namespace 可取，直接用这个常量。
DEFAULT_QUERY_PORT = 8765
PARTIAL_CACHE_TTL_HOURS = 6.0
_ODDS_CACHE_RE = re.compile(r"^odds_pages_(\d+)\.json$")
_ODDS_META_RE = re.compile(r"^odds_pages_(\d+)\.meta\.json$")


def _enable_ctrl_c() -> None:
    """恢复 Windows 新进程组默认的 Ctrl+C 处理。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    except (AttributeError, OSError):
        return



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
    analyze.add_argument("--scout", action="store_true", help="对观望场次联网检索伤停/战意等软信息（需 LLM_API_KEY，默认开启检索）")
    analyze.add_argument(
        "--team-read",
        action="store_true",
        help="读 500 数据分析页的球队资料，用大模型给出亚盘赢盘倾向（需 LLM_API_KEY，只展示不进决策）",
    )

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
    refresh.add_argument(
        "--cache-dir", default="", help="产物所在目录，缺省用 exe 所在目录下的 cache"
    )
    refresh.add_argument("--delay", type=float, default=None, help="抓取时页面间隔秒数，缺省读 data/app_settings.json")
    refresh.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome 调试端口")
    refresh.add_argument("--force", action="store_true", help="即使未过期也重抓")
    refresh.add_argument("--db-path", default=None, help="仅用于确定期号")

    review = subparsers.add_parser("review", help="赛后复盘: 刷新比分并统计推荐命中率")
    _add_common_args(review)
    review.add_argument("--cache-dir", default=None, help="原始页面缓存目录, 缺省不使用缓存")
    review.add_argument("--refresh", action="store_true", help="复盘前先重新抓取最终比分")
    review.add_argument("--trend", action="store_true", help="跨期命中率趋势统计")

    ledger = subparsers.add_parser("ledger", help="ev 差排序快照的对账（只记录，不改推荐）")
    _add_common_args(ledger)
    ledger.add_argument("--trend", action="store_true", help="跨期对账趋势")
    ledger.add_argument("--limit", type=int, default=10, help="列出排序最高的前 N 场")

    bets = subparsers.add_parser("bets", help="投注统计与导出")
    _add_common_args(bets)
    bets.add_argument("--trend", action="store_true", help="跨期投注统计")
    bets.add_argument("--export", default="", help="导出 CSV 文件路径")

    cache = subparsers.add_parser("cache", help="查看或清理本地缓存")
    cache.add_argument(
        "--cache-dir",
        default="",
        help="缓存目录，缺省使用 exe/当前目录下的 cache",
    )
    cache.add_argument(
        "--clean",
        action="store_true",
        help="清理旧缓存，默认保留最新 3 期盘口产物",
    )
    cache.add_argument(
        "--keep",
        type=int,
        default=3,
        help="保留最新多少期 odds_pages 产物（默认 3）",
    )
    cache.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示待清理内容，不实际删除",
    )
    backup = subparsers.add_parser("backup", help="备份或恢复本地数据")
    backup.add_argument("--db-path", default=None, help="SQLite 路径")
    backup.add_argument(
        "--file",
        default="",
        help="备份 ZIP 路径；备份时缺省写入 data/backups",
    )
    backup.add_argument("--restore", action="store_true", help="从 ZIP 恢复")
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
        known_scores = load_issue_scores(db_path, prev_issue)
        analyses, prev_issue_out, _ = analyze_issue(prev_issue, fetcher=fetcher, max_workers=workers)
        for item in analyses:
            known = known_scores.get(str(item.get("match_no") or ""))
            if known:
                item["final_score"] = known
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
        team_read=bool(getattr(args, "team_read", False)),
    )
    if args.narrate:
        from .llm import narrate_movements

        narrate_movements(analyses)

    if args.scout:
        from .scout import scout_matches

        scout_matches(analyses)
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
        ledger_n = save_ev_ledger(
            _resolve_db_path(args.db_path),
            analyses,
            issue,
            saved["run_id"],
            saved["captured_at"],
        )
        print(f"  对账: ev 排序快照 {ledger_n} 场（只记录，不改推荐）")
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
            # 赛前缓存的详情页会一直显示 0 - 0，必须踢掉重抓才能拿到赛后比分
            cache_key = f"detail:{row['fixture_id']}"
            fetcher.cache.evict(cache_key)
            html = fetcher.fetch(
                LIVE_DETAIL_URL.format(fixture_id=row["fixture_id"]),
                cache_key=cache_key,
            )
            scores[str(row["match_no"])] = extract_match_score(html)
        except Exception as exc:
            errors.append(f"  {row['match_no']} 场次比分抓取失败: {exc}")
    # 只写非空比分：空串意味着未开赛或抓取失败，写回会冲掉已有真比分
    filled_scores = {no: s for no, s in scores.items() if s}
    updated = update_match_scores(db_path, actual, filled_scores)
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
    from .store import connect

    refreshed: dict = {}
    if refresh:
        refreshed = _refresh_scores(db_path, issue, cache_dir)
    analyses = load_issue_snapshot(db_path, issue)
    done = [a for a in analyses if a.get("final_score")]
    conn = connect(db_path)
    run_id = conn.execute("SELECT MAX(run_id) FROM issue_runs WHERE issue = ?", (issue,)).fetchone()[0]
    conn.close()
    return {
        "issue": issue,
        "match_count": len(analyses),
        "scored": len(done),
        "refreshed": refreshed,
        "run_id": run_id,
        **_issue_hit_rates(done),
    }


def _issue_hit_rates(analyses: list[dict]) -> dict:
    """按赢/赢半/走/输半/输统计一场期内的推荐结算。"""
    asian_results: list[float] = []
    asian_waters: list[float] = []
    total_results: list[float] = []
    total_waters: list[float] = []
    for item in analyses:
        score = str(item.get("final_score") or "")
        if "-" not in score:
            continue
        try:
            hg, ag = (int(part.strip() or "0") for part in score.split("-", 1))
        except ValueError:
            continue
        total_goals = hg + ag
        market = item.get("market") or {}
        odds = item.get("odds_analysis") or {}
        asian_rec = odds.get("asian_handicap") or {}
        if asian_rec.get("available") and asian_rec.get("action") in ("主推", "轻仓"):
            side = asian_rec.get("recommended_side")
            picked = "home" if side == "主队赢盘" else "away" if side == "客队赢盘" else ""
            if picked:
                asian_market = market.get("asian") or {}
                result = settle_asian(
                    hg,
                    ag,
                    float(asian_market.get("home_line") or 0),
                    picked,
                )
                if result is not None:
                    asian_results.append(result)
                    water_key = "home_water" if picked == "home" else "away_water"
                    asian_waters.append(float(asian_market.get(water_key) or 0))
        total_rec = odds.get("total_goals") or {}
        if total_rec.get("available") and total_rec.get("action") in ("主推", "轻仓"):
            side = str(total_rec.get("recommended_side") or "")
            if "大" in side:
                over = True
            elif "小" in side:
                over = False
            else:
                continue
            total_market = market.get("total") or {}
            line = float(total_market.get("line") or 0)
            if line <= 0:
                continue
            total_results.append(settle_total(total_goals, line, over))
            water_key = "over_water" if over else "under_water"
            total_waters.append(float(total_market.get(water_key) or 0))

    asian = summarize(asian_results)
    total = summarize(total_results)
    out = {f"asian_{key}": value for key, value in asian.items()}
    out.update({f"total_{key}": value for key, value in total.items()})
    out["asian_profit"] = sum(
        profit_units(result, water)
        for result, water in zip(asian_results, asian_waters)
        if water > 0
    )
    out["asian_priced"] = sum(1 for water in asian_waters if water > 0)
    out["total_profit"] = sum(
        profit_units(result, water)
        for result, water in zip(total_results, total_waters)
        if water > 0
    )
    out["total_priced"] = sum(1 for water in total_waters if water > 0)
    return out


def _print_trend(db_path: Path) -> None:
    """Print cross-issue settlement trend from the DB, ordered by issue ascending."""
    from .store import load_issue_snapshot

    issues = sorted(list_issues(db_path), key=lambda row: row["issue"])

    def _pct(hits: int, total: int) -> str:
        return f"{hits / total * 100:.0f}%" if total else "-"

    def _money(rates: dict, key: str) -> float:
        if int(rates.get(f"{key}_priced") or 0):
            return float(rates.get(f"{key}_profit") or 0)
        return float(rates.get(f"{key}_units") or 0)

    agg = {
        "asian_hits": 0,
        "asian_total": 0,
        "asian_units": 0.0,
        "asian_profit": 0.0,
        "asian_bets": 0,
        "asian_priced": 0,
        "total_hits": 0,
        "total_total": 0,
        "total_units": 0.0,
        "total_profit": 0.0,
        "total_bets": 0,
        "total_priced": 0,
    }
    prev: dict | None = None
    print("=" * 70)
    print("命中率趋势（仅主推/轻仓，走水不入分母，赢半计入命中）")
    print("=" * 70)
    print(
        f"{'期号':<8}{'已完成':<10}{'亚盘命中':<10}{'亚盘率':<8}{'亚盘收益':<10}"
        f"{'大小命中':<12}{'大小率':<8}{'大小收益':<10}"
    )
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
        asian_units = f"{_money(rates, 'asian'):+.1f}"
        total_units = f"{_money(rates, 'total'):+.1f}"
        print(
            f"{info['issue']:<8}{scored:<10}{asian_cell:<10}"
            f"{_pct(rates['asian_hits'], rates['asian_total']):<8}{asian_units:<10}"
            f"{total_cell:<12}{_pct(rates['total_hits'], rates['total_total']):<8}"
            f"{total_units:<10}"
        )
        if delta_text:
            print(f"{'':<8}{delta_text}")
        prev = rates
    print("-" * 70)
    print(
        f"{'合计':<8}{'':<10}"
        f"{str(agg['asian_hits']) + '/' + str(agg['asian_total']):<10}"
        f"{_pct(agg['asian_hits'], agg['asian_total']):<8}{_money(agg, 'asian'):+.1f}{'':<10}"
        f"{str(agg['total_hits']) + '/' + str(agg['total_total']):<12}"
        f"{_pct(agg['total_hits'], agg['total_total']):<8}{_money(agg, 'total'):+.1f}"
    )
    print("口径: 仅统计主推/轻仓推荐; 走水不入分母, 赢半算命中; 有水位按水位计, 无水位按结果单位")

    return None




def _print_ledger_trend(db_path: Path) -> None:
    """跨期对账趋势：ev 差排序快照的前瞻性命中率（走水不计）。"""
    from .ev_ledger import ledger_trend

    trend = ledger_trend(db_path)
    print("=" * 70)
    print("ev 差排序对账趋势（只记录，未参与生产推荐）")
    print("=" * 70)
    if not trend:
        print("暂无快照。analyze 时会自动写入 ev_ledger。")
        return
    print(f"{'期号':<8}{'已对账':<10}{'命中':<10}{'命中率':<8}{'单位':<10}")
    agg_hits = agg_decided = 0
    agg_units = 0.0
    for row in trend:
        decided = row["settled"] - row["push"]
        rate = f"{row['hits'] / decided * 100:.0f}%" if decided else "-"
        units = float(row.get("units") or 0)
        print(f"{row['issue']:<8}{decided:<10}{row['hits']:<10}{rate:<8}{units:+.2f}")
        agg_hits += row["hits"]
        agg_decided += decided
        agg_units += units
    print("-" * 70)
    total_rate = f"{agg_hits / agg_decided * 100:.0f}%" if agg_decided else "-"
    print(f"{'合计':<8}{agg_decided:<10}{agg_hits:<10}{total_rate:<8}{agg_units:+.2f}")
    print("口径: 按 |ev_home - ev_away| 排序选边，走水不入分母，赢半算命中，单位按半注计且未乘水位")


def _bet_stats_line(stats: dict) -> str:
    return (
        f"注单 {stats.get('bets', 0)} · 已结算 {stats.get('settled', 0)} · "
        f"待结算 {stats.get('pending', 0)} · 投注积分 {stats.get('stake', 0):.2f} · "
        f"净收益 {stats.get('profit', 0):+.2f} · ROI {stats.get('roi', 0):+.1f}% · "
        f"命中率 {stats.get('hit_rate', 0):.1f}%"
    )


def _run_bets(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db_path)
    if args.export:
        path = Path(args.export)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\ufeff" + export_bets_csv(db_path, issue=args.issue),
            encoding="utf-8",
        )
        print(f"投注 CSV：{path}")
        return 0
    if args.trend:
        rows = bet_trend(db_path)
        print("=" * 78)
        print("投注跨期趋势")
        print("=" * 78)
        print(f"{'期号':<8}{'注数':<8}{'已结算':<8}{'投注积分':<12}{'净收益':<10}{'ROI':<9}{'命中率':<8}")
        for row in rows:
            print(
                f"{row['issue']:<8}{row['bets']:<8}{row['settled']:<8}"
                f"{row['stake']:<11.2f} 积分 {row['profit']:+.2f}    "
                f"{row['roi']:+.1f}%   {row['hit_rate']:.1f}%"
            )
        return 0
    rows = list_bets(db_path, issue=args.issue)
    stats = summarize_bets(rows)
    print(_bet_stats_line(stats))
    for market in ("asian", "total"):
        item = stats["markets"].get(market, {})
        print(
            f"{'亚盘' if market == 'asian' else '大小球'}: "
            f"{item.get('bets', 0)} 注，净收益 {item.get('profit', 0):+.2f}，"
            f"命中率 {item.get('hit_rate', 0):.1f}%"
        )
    print("-" * 78)
    for bet in rows:
        print(
            f"{bet.get('issue')} {bet.get('match_no')} "
            f"{bet.get('home_team')} vs {bet.get('away_team')} "
            f"{bet.get('market_label')} {bet.get('selection_label')} "
            f"{float(bet.get('stake') or 0):.2f} 积分 "
            f"{bet.get('status_label')} {bet.get('result_label')} "
            f"{float(bet.get('profit') or 0):+.2f}"
        )
    return 0


def _run_ledger(args: argparse.Namespace) -> int:
    """ev 差排序快照的对账：列出一期排序并对账比分；--trend 看跨期。"""
    from .ev_ledger import reconcile_ledger, top_picks

    db_path = _resolve_db_path(args.db_path)
    if args.trend:
        _print_ledger_trend(db_path)
        return 0

    issue = args.issue
    if not issue:
        rows = list_issues(db_path)
        if not rows:
            print("库中暂无数据，先用 analyze 抓取一期。")
            return 1
        issue = rows[0]["issue"]

    summary = reconcile_ledger(db_path, issue)
    print("=" * 70)
    print(f"{issue} ev 差排序快照（只记录，不改生产推荐）")
    print("=" * 70)
    decided = summary["settled"] - summary["push"]
    if decided:
        rate = summary["hits"] / decided * 100
        print(
            f"已对账 {summary['settled']}/{summary['rows']} 场，"
            f"命中 {summary['hits']}/{decided}（{rate:.0f}%），"
            f"净结果 {summary['units']:+.2f} 单位（未乘水位），走水 {summary['push']}，"
            f"赢半 {summary['half_wins']}，输半 {summary['half_losses']}"
        )
    else:
        print(f"快照 {summary['rows']} 场，尚无比分可对账（先用 review --issue {issue} --refresh 刷新比分）")
    print("-" * 70)
    for pick in top_picks(db_path, issue, limit=args.limit):
        side = {"home": "主队", "away": "客队", "": "-"}[pick["picked_side"]]
        score = pick["final_score"] or "-"
        print(
            f"{pick['match_no']:>2} {pick['home_team']} vs {pick['away_team']} "
            f"ev差 {pick['ev_diff']:+.2f}  选边 {side}  盘口 {pick['asian_line_text']}  比分 {score}"
        )
    return 0


def _run_refresh(args: argparse.Namespace) -> int:
    """产物还新就直接返回；过期了才启动抓取。"""
    import subprocess
    import urllib.request

    from .odds_pages import PAGES_STALE_HOURS, pages_age_hours

    # 不传时跟 app_root，否刹手动跑 exe refresh 会找不到产物
    cache_dir = Path(args.cache_dir) if args.cache_dir else _default_cache_dir()
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
        print("  2) 确认已安装 Chrome，并重试刷新")
        return 1

    from . import scrape_odds_pages as mod
    code = mod.main(
        [
            "--issue", issue,
            "--cache-dir", str(cache_dir),
            "--delay", str(
                args.delay
                if args.delay is not None
                else load_scrape_delay()
            ),
            "--cdp", endpoint,
        ]
    )
    if code != 0:
        print(f"\n抓取未完成（退出码 {code}），原产物未被覆盖。")
        return code

    new_age = pages_age_hours(cache_dir, issue)
    if new_age is None:
        print("\u4ea7物仍不存在，请查看上方输出。")
        return 1
    print(f"\u5b8c成，产物年龄 {new_age:.1f} 小时。")
    return 0


def _market_review_line(label: str, rates: dict, key: str) -> str:
    bets = int(rates.get(f"{key}_bets") or 0)
    if not bets:
        return f"{label}推荐: 无数据"
    total = int(rates.get(f"{key}_total") or 0)
    hits = int(rates.get(f"{key}_hits") or 0)
    pct = f"{hits / total * 100:.0f}%" if total else "-"
    priced = int(rates.get(f"{key}_priced") or 0)
    if priced:
        money = f"净收益 {float(rates.get(f'{key}_profit') or 0):+.2f} 单位"
        if priced < bets:
            money += f"（{priced}/{bets} 场有水位）"
    else:
        money = f"净结果 {float(rates.get(f'{key}_units') or 0):+.2f} 单位"
    detail = (
        f"{rates.get(f'{key}_wins', 0)}赢 / "
        f"{rates.get(f'{key}_half_wins', 0)}赢半 / "
        f"{rates.get(f'{key}_pushes', 0)}走 / "
        f"{rates.get(f'{key}_half_losses', 0)}输半 / "
        f"{rates.get(f'{key}_losses', 0)}输"
    )
    return f"{label}推荐: {hits}/{total} 命中 ({pct})，{money}（{detail}）"


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
    print("-" * 60)
    print(_market_review_line("亚盘", rates, "asian"))
    print(_market_review_line("大小球", rates, "total"))
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


def clean_cache(
    cache_dir: str | Path,
    *,
    keep: int = 3,
    dry_run: bool = False,
    now: float | None = None,
) -> dict:
    """Remove disposable cache files while retaining the newest odds artifacts."""
    if keep < 0:
        raise ValueError("keep must be >= 0")

    root = Path(cache_dir)
    result = {
        "cache_dir": str(root),
        "deleted_files": 0,
        "deleted_bytes": 0,
        "kept_odds": [],
        "skipped": [],
    }
    if not root.is_dir():
        return result

    now = time.time() if now is None else now
    odds_files: list[tuple[int, Path]] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        match = _ODDS_CACHE_RE.fullmatch(path.name)
        if match:
            odds_files.append((int(match.group(1)), path))

    kept = {
        path
        for _, path in sorted(odds_files, key=lambda item: item[0], reverse=True)[:keep]
    }
    kept_issues = {
        match.group(1)
        for path in kept
        if (match := _ODDS_CACHE_RE.fullmatch(path.name))
    }
    result["kept_odds"] = [path.name for _, path in sorted(odds_files, reverse=True)[:keep]]

    def remove(path: Path) -> None:
        result["deleted_files"] += 1
        result["deleted_bytes"] += path.stat().st_size
        if not dry_run:
            path.unlink()

    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".raw":
            remove(path)
            continue
        should_remove = False
        if _ODDS_CACHE_RE.fullmatch(path.name):
            should_remove = path not in kept
        elif (match := _ODDS_META_RE.fullmatch(path.name)):
            should_remove = match.group(1) not in kept_issues
        elif path.name == ".ai_scrape_progress":
            active = False
            try:
                progress = json.loads(path.read_text(encoding="utf-8"))
                phase = str(progress.get("phase") or "")
                total = int(progress.get("total") or 0)
                done = int(progress.get("done") or 0)
                active = phase in {"collect", "scrape", "llm"} and (
                    total <= 0 or done < total
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                result["skipped"].append(f"{path.name}: 无法判断状态")
                continue
            if active:
                result["skipped"].append(f"{path.name}: 任务进行中")
                continue
            should_remove = True
        elif path.name.startswith(".odds_partial_"):
            age_hours = max(0.0, (now - path.stat().st_mtime) / 3600)
            if age_hours > PARTIAL_CACHE_TTL_HOURS:
                should_remove = True
            else:
                result["skipped"].append(
                    f"{path.name}: 未超过 {PARTIAL_CACHE_TTL_HOURS:.0f}h"
                )
                continue
        else:
            continue
        if should_remove:
            remove(path)

    return result


def _run_cache(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir) if args.cache_dir else _default_cache_dir()
    if args.keep < 0:
        print("--keep 不能小于 0")
        return 2
    if not args.clean:
        print(f"缓存目录：{cache_dir}")
        print("未执行清理。需要清理时请使用：cache --clean --keep 3")
        return 0
    try:
        result = clean_cache(
            cache_dir,
            keep=args.keep,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"缓存清理失败：{exc}")
        return 1
    action = "预计清理" if args.dry_run else "已清理"
    size_mb = result["deleted_bytes"] / 1024 / 1024
    print(f"{action} {result['deleted_files']} 个文件，释放 {size_mb:.2f} MB")
    print(f"保留盘口产物：{', '.join(result['kept_odds']) or '无'}")
    for item in result["skipped"]:
        print(f"跳过：{item}")
    if not Path(cache_dir).is_dir():
        print("缓存目录不存在，无需清理")
    return 0


def _run_backup(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args.db_path)
    settings_path = db_path.parent / "llm_settings.json"
    if args.restore:
        if not args.file:
            print("恢复时必须指定 --file")
            return 2
        try:
            safety = restore_backup(args.file, db_path, settings_path)
        except (OSError, ValueError) as exc:
            print(f"恢复失败：{exc}")
            return 1
        print(f"恢复完成：{db_path}")
        if safety:
            print(f"恢复前安全备份：{safety}")
        return 0
    target = Path(args.file) if args.file else (
        db_path.parent / "backups" / f"backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    )
    try:
        write_backup(db_path, target, settings_path)
        summary = backup_summary(target)
    except (OSError, ValueError) as exc:
        print(f"备份失败：{exc}")
        return 1
    included = "、".join(
        name for name, present in (("数据库", summary["database"]), ("AI 设置", summary["settings"])) if present
    ) or "空状态"
    print(f"备份完成：{target}")
    print(f"包含：{included}")
    return 0


def _determine_latest_issue(cache_dir: Path) -> str:
    """Fetch the current SFC page and return the issue published there."""
    fetcher = Fetcher(cache_dir=cache_dir)
    _, issue = collect_sfc_matches("", fetcher)
    # Pin the confirmed issue to fresh current-source pages during analyze.
    from .sfc import ISSUE_URL

    for key in (
        f"sfc:{ISSUE_URL.format(issue=issue)}",
        "jczq:current",
        "odds_xml:current",
    ):
        fetcher.cache.evict(key)
    return issue


def _previous_issue(issue: str) -> str:
    try:
        number = int(issue[-3:]) - 1
    except (TypeError, ValueError):
        return ""
    return issue[:2] + str(number).zfill(3)


def _refresh_previous_issue_scores(
    db_path: Path, latest_issue: str, cache_dir: Path
) -> None:
    previous = _previous_issue(latest_issue)
    if not previous:
        return
    known_issues = {
        str(row.get("issue") or "")
        for row in list_issues(db_path)
        if int(row.get("match_count") or 0) > 0
    }
    if previous not in known_issues:
        print(f"上期 {previous} 没有本地比赛数据，跳过比分刷新。")
        return
    print(f"刷新上期 {previous} 最终比分...")
    try:
        refreshed = _refresh_scores(db_path, previous, cache_dir=cache_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"上期 {previous} 比分刷新失败，将继续抓取最新期：{exc}")
        return
    if refreshed.get("error"):
        print(refreshed["error"])
    for line in refreshed.get("errors") or []:
        print(line)
    print(
        f"上期 {previous} 比分刷新："
        f"{refreshed.get('filled', 0)}/{refreshed.get('match_count', 0)} 场有值，"
        f"DB 更新 {refreshed.get('updated', 0)} 条"
    )


def _run_analyze_then_serve(parser: argparse.ArgumentParser) -> int:
    """exe 双击的默认流程：抓取入库后直接起异动查询服务，并打开查询页。"""
    # cache / db / reports 三者都必须挂 exe 目录：
    # - cache_dir 为 None 时 Fetcher 没有 cache.root，odds_pages_<期号>.json 加载不到
    # - db_path 缺省走 Path.cwd()，换个方式启动 exe 就会写到别处去
    # - output_dir 同理，报告与查询页的「分析报告」链接会指到别处
    db_path = _app_root() / "data" / "football_analysis.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = _default_cache_dir()
    args = parser.parse_args(
        [
            "analyze",
            "--cache-dir",
            str(cache_dir),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(_app_root() / "reports"),
        ]
    )
    try:
        latest_issue = _determine_latest_issue(cache_dir)
        _refresh_previous_issue_scores(db_path, latest_issue, cache_dir)
        args.issue = latest_issue
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
        issues = list_issues(db_path)
        default_issue = str(issues[0].get("issue") or "") if issues else ""
        serve_query(
            db_path,
            port=port,
            open_browser=not args.no_open,
            reports_dir=Path(args.output_dir),
            default_issue=default_issue,
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
    bet_rows = list_bets(db_path, issue=args.issue)
    html = render_query_html(
        rows,
        issues=list_issues(db_path),
        leagues=list_leagues(db_path, args.issue),
        issue=args.issue,
        team=args.team,
        level=args.level,
        kind=args.kind,
        attention_only=args.attention_only,
        latest_only=not args.all_runs,
        db_path=str(db_path),
        report_links=report_links,
        bets=bet_rows,
        bet_stats=summarize_bets(bet_rows),
        bet_trend=bet_trend(db_path),
        cache_dir=_default_cache_dir(),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "movement_query.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n查询页：{html_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _enable_ctrl_c()
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    if not raw:
        return _run_analyze_then_serve(parser)
    if raw[0] not in {
        "analyze",
        "query",
        "review",
        "refresh",
        "ledger",
        "bets",
        "cache",
        "backup",
    }:
        raw = ["query", "--serve", *raw]
    args = parser.parse_args(raw)
    if args.command == "query":
        return _run_query(args)
    if args.command == "review":
        return _run_review(args)
    if args.command == "refresh":
        return _run_refresh(args)
    if args.command == "ledger":
        return _run_ledger(args)
    if args.command == "bets":
        return _run_bets(args)
    if args.command == "cache":
        return _run_cache(args)
    if args.command == "backup":
        return _run_backup(args)
    result = _run_analyze(args)
    if getattr(sys, "frozen", False):
        input("\nPress Enter to close this window...")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
