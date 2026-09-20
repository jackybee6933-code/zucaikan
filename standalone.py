from __future__ import annotations

import argparse
import sys
import socket
import traceback
from football_analysis.open_browser import open_in_chrome
from pathlib import Path

import ctypes


def _minimize_console():
    if sys.platform != "win32":
        return
    SW_MINIMIZE = 6
    try:
        user32 = ctypes.windll.user32
        user32.GetConsoleWindow.restype = ctypes.c_void_p
        hwnd = user32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:
        pass

from football_analysis.http import Fetcher
from football_analysis.pipeline import analyze_issue
from football_analysis.query_app import serve_query
from football_analysis.query_report import render_query_html
from football_analysis.report import print_console, write_report_files
from football_analysis.store import default_db_path, list_issues, query_movements, save_issue_movements


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="足球胜负彩分析独立版：自动抓取最新一期、生成报告并打开浏览器",
    )
    subparsers = parser.add_subparsers(dest="command")
    analyze = subparsers.add_parser("analyze", help="采集并分析一期比赛")
    analyze.add_argument(
        "--issue",
        default="",
        help="胜负彩期号，例如 26120；缺省自动抓取最新一期",
    )
    analyze.add_argument(
        "--cache-dir",
        default=None,
        help="原始页面缓存目录；独立版缺省使用程序同目录 cache",
    )
    analyze.add_argument(
        "--output-dir",
        default=None,
        help="报告输出目录；独立版缺省使用程序同目录 reports",
    )
    analyze.add_argument(
        "--db-path",
        default=None,
        help="SQLite 路径；独立版缺省使用程序同目录 data/football_analysis.sqlite",
    )
    analyze.add_argument("--workers", type=int, default=4, help="球队页并发抓取数")
    analyze.add_argument("--no-cache", action="store_true", help="不使用本地缓存")
    analyze.add_argument("--no-db", action="store_true", help="不写入本地异动数据库")
    analyze.add_argument("--no-serve", action="store_true", help="分析后不启动本地查询服务")
    analyze.add_argument("--port", type=int, default=8765, help="查询服务端口")
    analyze.add_argument("--no-open", action="store_true", help="生成报告后不自动打开浏览器")

    query = subparsers.add_parser("query", help="查询已入库的变盘异动")
    query.add_argument("--issue", default="", help="按期号过滤")
    query.add_argument("--team", default="", help="按主客队或联赛模糊查询")
    query.add_argument("--level", default="", help="重点关注 / 关注 / 稳定 / 无数据")
    query.add_argument("--kind", default="", help="升盘 / 降盘 / 水位反转 / 水位摆动")
    query.add_argument("--attention-only", action="store_true", help="只看达到关注线的异动")
    query.add_argument("--all-runs", action="store_true", help="不过滤为每期最新一次抓取")
    query.add_argument("--limit", type=int, default=200, help="最多返回条数")
    query.add_argument("--db-path", default=None, help="SQLite 路径")
    query.add_argument("--output-dir", default=None, help="查询 HTML 输出目录")
    query.add_argument("--serve", action="store_true", help="启动本地查询页")
    query.add_argument("--port", type=int, default=8765, help="查询页端口")
    query.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    return parser


def _resolve_db_path(root: Path, value: str | None) -> Path:
    return Path(value) if value else default_db_path(root)


def _find_free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 11):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find a free port near {preferred}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    if not raw or raw[0] not in {"analyze", "query"}:
        raw = ["analyze", *raw]
    args = parser.parse_args(raw)
    root = app_root()
    frozen = bool(getattr(sys, "frozen", False))
    try:
        if args.command == "query":
            db_path = _resolve_db_path(root, args.db_path)
            output_dir = Path(args.output_dir) if args.output_dir else root
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
            if not rows:
                print("没有符合条件的异动记录。")
            else:
                print(f"共 {len(rows)} 条")
                for row in rows:
                    print(
                        f"{row.get('issue','')} {row.get('match_no','')} "
                        f"{row.get('home_team','')} vs {row.get('away_team','')} "
                        f"[{row.get('level','')}] {row.get('reason','')}"
                    )
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
            output_dir.mkdir(parents=True, exist_ok=True)
            html_path = output_dir / "movement_query.html"
            html_path.write_text(html, encoding="utf-8")
            print(f"\n查询页：{html_path}")
            if not args.no_open:
                open_in_chrome(html_path.resolve())
            return 0

        cache_dir = args.cache_dir
        output_dir = args.output_dir
        if cache_dir is None:
            cache_dir = None if args.no_cache else root / "cache"
        if output_dir is None:
            output_dir = root / "reports"
        fetcher = Fetcher(cache_dir=cache_dir)
        analyses, issue, source_url = analyze_issue(
            args.issue,
            fetcher=fetcher,
            max_workers=args.workers,
        )
        print_console(analyses, issue)
        html_path = write_report_files(
            output_dir,
            analyses,
            issue,
            source_url,
        )
        print(f"\nHTML 报告: {html_path}")
        if not args.no_db:
            saved = save_issue_movements(
                _resolve_db_path(root, args.db_path),
                analyses,
                issue,
                source_url,
            )
            print(
                f"  DB:   {saved['db_path']}  run_id={saved['run_id']}  "
                f"异动={saved['attention_count']}/{saved['match_count']}"
            )
        if not args.no_serve:
            port = _find_free_port(args.port)
            print(f"\n查询服务: http://127.0.0.1:{port}/  (Ctrl+C 停止)")
            _minimize_console()
            serve_query(
                _resolve_db_path(root, args.db_path),
                port=port,
                open_browser=not args.no_open,
                cache_dir=cache_dir,
            )
        elif not args.no_open:
            open_in_chrome(html_path.resolve())
        return 0
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        if frozen:
            input("\n程序运行失败，按回车键退出...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
