from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from .h2h import fetch_issue_schedule, fetch_titan_h2h
from .h2h import match_no_to_tian_match
from .http import Fetcher
from .jczq import JCZQ_URL, parse_jczq_html
from .model import build_model_input, predict_match
from .odds import market_implied_from_odds
from .odds_analysis import analyze_odds
from .odds_movement import fetch_nowscore_movement
from .odds_xml import ODDS_XML_URL, company_label, parse_odds_xml
from .odds_pages import (
    build_pages_movements,
    PAGES_STALE_HOURS,
    load_odds_pages_raw,
    page_failures,
    pages_age_hours,
    parse_odds_pages,
)
from .sfc import ISSUE_URL, TRADE_URL, parse_sfc_html
from .sfc import extract_match_score
from .team import TEAM_PAGE_URL, parse_team_recent_html


LIVE_DETAIL_URL = "https://live.500.com/detail.php?fid={fixture_id}&r=1"


def collect_sfc_matches(issue: str, fetcher: Fetcher) -> tuple[list[dict], str]:
    auto_latest = not bool(issue)
    url = TRADE_URL if auto_latest else ISSUE_URL.format(issue=issue)
    html = fetcher.fetch(
        url,
        cache_key=f"sfc:{url}",
        use_cache=not auto_latest,
    )
    rows = parse_sfc_html(html, source_url=url)
    if not rows:
        raise RuntimeError("没有解析到任何胜负彩场次")
    actual_issue = rows[0].get("issue") or issue
    if not actual_issue:
        raise RuntimeError("未能从当前胜负彩页面识别期号")
    return rows, actual_issue


def _parse_detail_ids(html: str, fixture_id: str) -> tuple[str, str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    team_links: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        match = re.search(r"/team/(\d+)/", anchor.get("href", ""))
        if not match:
            continue
        name = anchor.get_text(" ", strip=True)
        if not name:
            continue
        pair = (match.group(1), name)
        if pair not in team_links:
            team_links.append(pair)
        if len(team_links) >= 2:
            break
    if len(team_links) < 2:
        raise RuntimeError(f"detail-{fixture_id} 未解析到双方球队 ID")
    home_id, home_name = team_links[0]
    away_id, away_name = team_links[1]
    return home_id, away_id, home_name, away_name


def analyze_issue(
    issue: str = "",
    *,
    fetcher: Fetcher | None = None,
    max_workers: int = 4,
) -> tuple[list[dict], str, str]:
    fetcher = fetcher or Fetcher()
    matches, actual_issue = collect_sfc_matches(issue, fetcher)
    cache_current_sources = bool(issue)

    jczq_error = ""
    jczq_map: dict[str, dict] = {}
    try:
        jczq_html = fetcher.fetch(
            JCZQ_URL,
            cache_key="jczq:current",
            use_cache=cache_current_sources,
        )
        jczq_map = parse_jczq_html(jczq_html)
    except Exception as exc:  # noqa: BLE001
        jczq_error = f"竞彩列表不可用：{exc}"

    odds_error = ""
    odds_map: dict[str, dict] = {}
    try:
        xml_html = fetcher.fetch(
            ODDS_XML_URL,
            kind="xml",
            cache_key="odds_xml:current",
            use_cache=cache_current_sources,
        )
        odds_map = parse_odds_xml(xml_html)
        if not odds_map:
            # 500 会返回合法但空的 <matches/>，不抛异常；不显式提示的话报告会静默缺盘口。
            odds_error = "odds.xml 返回空 matches（500 未发布本期盘口）"
    except Exception as exc:  # noqa: BLE001
        odds_error = f"odds.xml 不可用：{exc}"

    pages_raw = load_odds_pages_raw(fetcher.cache.root, actual_issue)
    pages_map = parse_odds_pages(pages_raw)
    # 抓取失败与「上游没开盘」是两回事，必须分开报，否则报告会缺盘口却看不出原因
    pages_warnings: list[str] = []
    failed_pages = page_failures(pages_raw)
    if failed_pages:
        preview = "、".join(failed_pages[:5])
        more = f" 等 {len(failed_pages)} 个" if len(failed_pages) > 5 else ""
        pages_warnings.append(
            f"单场页有 {len(failed_pages)} 个页面抓取失败，盘口可能缺失：{preview}{more}"
        )
    age_hours = pages_age_hours(fetcher.cache.root, actual_issue)
    if pages_map and age_hours is not None and age_hours > PAGES_STALE_HOURS:
        pages_warnings.append(
            f"单场页盘口产物已过去 {age_hours:.0f} 小时"
            f"（阈值 {PAGES_STALE_HOURS:.0f}h），盘口可能不是最新；"
            f"重抓：python tools/scrape_odds_pages.py --issue {actual_issue}"
        )

    enriched: list[dict] = []
    needs_detail: list[dict] = []
    for row in matches:
        detail = dict(row)
        detail["warnings"] = []
        detail["final_score"] = row.get("final_score", "")
        if jczq_error:
            detail["warnings"].append(jczq_error)
        if odds_error:
            detail["warnings"].append(odds_error)
        detail["warnings"].extend(pages_warnings)
        enrichment = jczq_map.get(row["fixture_id"])
        if enrichment:
            display_time = enrichment["match_time"]
            if enrichment["match_date"] and "-" not in display_time:
                display_time = f"{enrichment['match_date'][5:]} {display_time}"
            detail.update(
                {
                    "info_match_id": enrichment["info_match_id"],
                    "home_id": enrichment["home_id"],
                    "away_id": enrichment["away_id"],
                    "home_name": enrichment["home_name"],
                    "away_name": enrichment["away_name"],
                    "league": enrichment["league"] or row["league"],
                    "match_date": enrichment["match_date"],
                    "match_time": display_time,
                    "match_no_display": enrichment["match_no"],
                }
            )
        else:
            needs_detail.append(row)
        enriched.append(detail)

    if needs_detail:
        detail_htmls: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    fetcher.fetch,
                    LIVE_DETAIL_URL.format(fixture_id=row["fixture_id"]),
                    cache_key=f"detail:{row['fixture_id']}",
                ): row["fixture_id"]
                for row in needs_detail
            }
            for future in as_completed(future_map):
                fixture_id = future_map[future]
                detail_htmls[fixture_id] = future.result()
        for row in enriched:
            if row.get("home_id") and row.get("away_id"):
                pass
            try:
                home_id, away_id, home_name, away_name = _parse_detail_ids(
                    detail_htmls[row["fixture_id"]], row["fixture_id"]
                )
                row["home_id"] = home_id
                row["away_id"] = away_id
                row["home_name"] = home_name
                row["away_name"] = away_name
            except Exception as exc:  # noqa: BLE001
                row["warnings"].append(f"详情页未取到球队：{exc}")

        for row in enriched:
            if row.get("fixture_id") in detail_htmls:
                row["final_score"] = extract_match_score(detail_htmls[row["fixture_id"]])

    _attach_titan_h2h(enriched, actual_issue, fetcher, max_workers=max_workers)
    _attach_odds_movement(
        enriched, fetcher, max_workers=max_workers, pages_raw=pages_raw
    )

    team_ids = {
        row.get("home_id", "")
        for row in enriched
        if row.get("home_id")
    } | {
        row.get("away_id", "")
        for row in enriched
        if row.get("away_id")
    }
    records = _fetch_team_records(team_ids, fetcher, max_workers=max_workers)

    results: list[dict] = []
    for row in enriched:
        odds = odds_map.get(row.get("info_match_id", ""), {})
        # jczq 失效时 info_match_id 为空，odds.xml 永远匹配不上；单场页产物按 fixture_id 索引。
        pages = pages_map.get(row.get("fixture_id", ""), {})
        if pages:
            merged = dict(odds)
            merged.update({k: v for k, v in pages.items() if v not in (0.0, "")})
            odds = merged
        home_id = row.get("home_id", "")
        away_id = row.get("away_id", "")
        analysis = _build_single_analysis(
            row,
            odds,
            home_record=records.get(home_id, {}),
            away_record=records.get(away_id, {}),
        )
        analysis["final_score"] = row.get("final_score", "")
        results.append(analysis)
    return results, actual_issue, ISSUE_URL.format(issue=actual_issue)

def _attach_titan_h2h_schedules(rows: list[dict], issue: str, fetcher: Fetcher, *, max_workers: int) -> None:
    """Legacy schedule attachment retained for API compatibility."""
    _attach_titan_h2h(rows, issue, fetcher, max_workers=max_workers)


def _fetch_team_records(
    team_ids: set[str],
    fetcher: Fetcher,
    *,
    max_workers: int,
) -> dict[str, dict]:
    if not team_ids:
        return {}
    urls = {team_id: TEAM_PAGE_URL.format(team_id=team_id) for team_id in sorted(team_ids)}
    records: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetcher.fetch, url, cache_key=f"team:{team_id}"): team_id
            for team_id, url in urls.items()
        }
        for future in as_completed(future_map):
            team_id = future_map[future]
            try:
                html = future.result()
                records[team_id] = parse_team_recent_html(html, team_id=team_id).to_dict()
            except Exception as exc:  # noqa: BLE001
                records[team_id] = {"_error": str(exc)}
    return records


def _attach_titan_h2h(
    rows: list[dict],
    issue: str,
    fetcher: Fetcher,
    *,
    max_workers: int,
) -> None:
    """Attach H2H + schedule + odds-movement data rows by row."""
    if not rows:
        return
    schedule_error = ""
    try:
        schedule_map, schedule_date, schedule_error = fetch_issue_schedule(
            fetcher,
            rows,
            issue,
        )
    except Exception as exc:  # noqa: BLE001
        schedule_map = {}
        schedule_date = ""
        schedule_error = str(exc)

    by_fixture: dict[str, str] = {}
    no_map = match_no_to_tian_match(rows, schedule_map=schedule_map, schedules=[])
    for row in rows:
        match_id = int(no_map.get(str(row.get("match_no", "")), 0))
        if match_id:
            row["titan_match_id"] = str(match_id)
            by_fixture[row["fixture_id"]] = str(match_id)
        elif not schedule_map and not row.get("home_id"):
            row["warnings"].append(f"Titan 赛程未映射本场：{schedule_error}" if schedule_error else "Titan 赛程未映射本场")

    if not by_fixture:
        return

    h2h_by_fixture: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_titan_h2h, fetcher, match_id): fixture_id
            for fixture_id, match_id in by_fixture.items()
        }
        for future in as_completed(future_map):
            fixture_id = future_map[future]
            try:
                h2h_by_fixture[fixture_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                h2h_by_fixture[fixture_id] = {"_error": str(exc)}

    for row in rows:
        payload = h2h_by_fixture.get(row.get("fixture_id", ""))
        if payload is None:
            continue
        row["h2h"] = payload


def _attach_odds_movement(
    rows: list[dict],
    fetcher: Fetcher,
    *,
    max_workers: int,
    pages_raw: dict | None = None,
) -> None:
    by_fixture = {
        str(row.get("fixture_id", "")): row["titan_match_id"]
        for row in rows
        if row.get("titan_match_id")
    }
    if not by_fixture:
        return

    teams_by_fixture: dict[str, dict] = {}
    for row in rows:
        fid = str(row.get("fixture_id", ""))
        if fid in by_fixture:
            teams_by_fixture[fid] = {
                "home": row.get("home_name") or row.get("home_team") or "",
                "away": row.get("away_name") or row.get("away_team") or "",
            }
    movements: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                fetch_nowscore_movement,
                fetcher,
                match_id,
                teams_by_fixture.get(fixture_id),
            ): fixture_id
            for fixture_id, match_id in by_fixture.items()
        }
        for future in as_completed(future_map):
            fixture_id = future_map[future]
            try:
                movements[fixture_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                movements[fixture_id] = {
                    "available": False,
                    "source": "nowscore_prematch",
                    "attention": False,
                    "level": "无数据",
                    "reasons": [],
                    "_error": str(exc),
                }

    if pages_raw:
        from_pages = build_pages_movements(
            pages_raw,
            {fid: teams_by_fixture[fid] for fid in teams_by_fixture},
        )
        for fixture_id, payload in from_pages.items():
            current = movements.get(fixture_id) or {}
            if (current.get("asian") or {}).get("available"):
                # 分歧来自单场页各公司口径，与盘口采用 Nowscore 还是单场页无关，始终保留
                if payload.get("split"):
                    current["split"] = payload["split"]
                continue
            movements[fixture_id] = payload

    for row in rows:
        payload = movements.get(str(row.get("fixture_id", "")))
        if payload is not None:
            row["odds_movement"] = payload
            if row.get("titan_match_id") and not payload.get("titan_match_id"):
                payload["titan_match_id"] = row["titan_match_id"]


def _total_from_movement(movement: dict | None) -> tuple[float, str, float, float, str] | None:
    """odds.xml 无 dxq 时，回落到 Nowscore 赛前指数的即时大小球盘。"""
    current = (((movement or {}).get("total") or {}).get("current")) or {}
    line = float(current.get("line") or 0)
    text = str(current.get("line_text") or "").strip()
    over_water = float(current.get("over_water") or 0)
    under_water = float(current.get("under_water") or 0)
    if line <= 0 and not text:
        return None
    return line, text or f"{line:g}", over_water, under_water, "赛前指数"


def _asian_from_movement(movement: dict | None) -> tuple[float, str, float, float, str] | None:
    """odds.xml 无 asian 时，回落到 Nowscore 赛前指数的即时亚盘。

    line_text 与 line 的正负约定相反，这里只取已转成主队视角的 line_display。
    """
    current = (((movement or {}).get("asian") or {}).get("current")) or {}
    line = float(current.get("line") or 0)
    text = str(current.get("line_display") or "").strip()
    home_water = float(current.get("home_water") or 0)
    away_water = float(current.get("away_water") or 0)
    if not line and not text:
        return None
    return line, text, home_water, away_water, "赛前指数"

def _build_single_analysis(
    row: dict,
    odds: dict,
    *,
    home_record: dict,
    away_record: dict,
) -> dict:
    warnings = list(row.get("warnings", []) or [])
    home_team = row.get("home_name") or row.get("home_team") or ""
    away_team = row.get("away_name") or row.get("away_team") or ""
    home_id = row.get("home_id", "")
    away_id = row.get("away_id", "")
    h2h = row.get("h2h") or {}
    if h2h.get("_error"):
        warnings.append(f"历史交锋缺失：{h2h['_error']}")
    if home_record.get("_error"):
        warnings.append(f"主队往绩缺失：{home_record['_error']}")
    if away_record.get("_error"):
        warnings.append(f"客队往绩缺失：{away_record['_error']}")

    europe_home = float(odds.get("europe_home") or row.get("europe_home") or 0)
    europe_draw = float(odds.get("europe_draw") or row.get("europe_draw") or 0)
    europe_away = float(odds.get("europe_away") or row.get("europe_away") or 0)

    market_home = float(odds.get("market_home") or 0)
    market_draw = float(odds.get("market_draw") or 0)
    market_away = float(odds.get("market_away") or 0)
    if not (market_home or market_draw or market_away) and any(
        x > 1.01 for x in (europe_home, europe_draw, europe_away)
    ):
        implied = market_implied_from_odds(europe_home, europe_draw, europe_away)
        market_home, market_draw, market_away = (
            implied["home"],
            implied["draw"],
            implied["away"],
        )

    if float(odds.get("asian_home_line") or 0) != 0 or odds.get("asian_line_text"):
        asian_line = float(odds.get("asian_home_line") or 0)
        asian_text = odds.get("asian_line_text", "")
        asian_home_water = float(odds.get("asian_home_water") or 0)
        asian_away_water = float(odds.get("asian_away_water") or 0)
        asian_source = company_label(odds.get("asian_company") or "")
    elif row.get("asian_line_text") or row.get("asian_home_water") or row.get("asian_away_water"):
        asian_line = float(row.get("asian_home_line") or 0)
        asian_text = row.get("asian_line_text", "")
        asian_home_water = float(row.get("asian_home_water") or 0)
        asian_away_water = float(row.get("asian_away_water") or 0)
        asian_source = ""
    else:
        fallback = _asian_from_movement(row.get("odds_movement"))
        if fallback:
            asian_line, asian_text, asian_home_water, asian_away_water, asian_source = fallback
        else:
            asian_line = 0.0
            asian_text = ""
            asian_home_water = asian_away_water = 0.0
            asian_source = ""
    has_asian = bool(asian_text or asian_home_water or asian_away_water)

    total_source = ""
    if float(odds.get("total_line") or 0) != 0 or odds.get("total_line_text"):
        total_line = float(odds.get("total_line") or 0)
        total_text = odds.get("total_line_text", "")
        over_water = float(odds.get("over_water") or 0)
        under_water = float(odds.get("under_water") or 0)
        has_total = True
        total_source = company_label(odds.get("total_company") or "")
    else:
        fallback = _total_from_movement(row.get("odds_movement"))
        if fallback:
            total_line, total_text, over_water, under_water, total_source = fallback
            has_total = True
        else:
            total_line = 2.5
            total_text = ""
            over_water = under_water = 0.0
            has_total = False

    model_input = build_model_input(
        home_record=_record_from_dict(home_record),
        away_record=_record_from_dict(away_record),
        h2h_total=int(h2h.get("total") or 0),
        h2h_home_wins=int(h2h.get("home_wins") or 0),
        h2h_draws=int(h2h.get("draws") or 0),
        h2h_away_wins=int(h2h.get("away_wins") or 0),
        market_home=market_home,
        market_draw=market_draw,
        market_away=market_away,
        europe_home=europe_home,
        europe_draw=europe_draw,
        europe_away=europe_away,
        asian_home_line=asian_line,
        asian_home_water=asian_home_water,
        asian_away_water=asian_away_water,
        has_asian=has_asian,
        total_line=total_line,
        has_total=has_total,
        over_water=over_water,
        under_water=under_water,
    )
    prediction = predict_match(model_input)
    if not home_id or not away_id:
        warnings.append("球队 ID 不完整，历史维度未参与")

    market = {
        "europe": {
            "home": europe_home,
            "draw": europe_draw,
            "away": europe_away,
        },
        "asian": {
            "home_line": asian_line,
            "line_text": asian_text,
            "home_water": asian_home_water,
            "away_water": asian_away_water,
            "source": asian_source,
        },
        "total": {
            "line": total_line,
            "line_text": total_text,
            "over_water": over_water,
            "under_water": under_water,
            "source": total_source,
        },
        "probability_3way": {
            "home": market_home,
            "draw": market_draw,
            "away": market_away,
        },
        "has_asian": has_asian,
        "has_total": has_total,
    }
    odds_analysis = analyze_odds(market, prediction)
    return {
        "match_no": row.get("match_no", ""),
        "jczq_no": row.get("match_no_display", ""),
        "league": row.get("league", ""),
        "match_time": row.get("match_time", ""),
        "home_team": home_team,
        "away_team": away_team,
        "home_id": home_id,
        "away_id": away_id,
        "titan_match_id": row.get("titan_match_id", ""),
        "analysis_url": row.get("analysis_url", ""),
        "market": market,
        "odds_analysis": odds_analysis,
        "odds_movement": row.get("odds_movement") or {
            "available": False,
            "source": "",
            "attention": False,
            "level": "无数据",
            "reasons": [],
        },
        "history": {
            "home": home_record,
            "away": away_record,
            "h2h": h2h,
        },
        "prediction": prediction,
        "warnings": warnings,
    }


def _record_from_dict(payload: dict):
    from .team import TeamRecord

    if not payload or payload.get("_error"):
        return None
    try:
        return TeamRecord(
            team_id=str(payload.get("team_id", "")),
            team_name=str(payload.get("team_name", "")),
            matches=int(payload.get("matches", 0)),
            wins=int(payload.get("wins", 0)),
            draws=int(payload.get("draws", 0)),
            losses=int(payload.get("losses", 0)),
            goals_for=int(payload.get("goals_for", 0)),
            goals_against=int(payload.get("goals_against", 0)),
            home_matches=int(payload.get("home_matches", 0)),
            home_wins=int(payload.get("home_wins", 0)),
            home_draws=int(payload.get("home_draws", 0)),
            home_losses=int(payload.get("home_losses", 0)),
            home_goals_for=int(payload.get("home_goals_for", 0)),
            home_goals_against=int(payload.get("home_goals_against", 0)),
            away_matches=int(payload.get("away_matches", 0)),
            away_wins=int(payload.get("away_wins", 0)),
            away_draws=int(payload.get("away_draws", 0)),
            away_losses=int(payload.get("away_losses", 0)),
            away_goals_for=int(payload.get("away_goals_for", 0)),
            away_goals_against=int(payload.get("away_goals_against", 0)),
            handicap_win=int(payload.get("handicap_win", 0)),
            handicap_push=int(payload.get("handicap_push", 0)),
            handicap_loss=int(payload.get("handicap_loss", 0)),
            over=int(payload.get("over", 0)),
            under=int(payload.get("under", 0)),
        )
    except (TypeError, ValueError):
        return None
