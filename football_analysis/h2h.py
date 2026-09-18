from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .http import Fetcher


TITAN_ANALYSIS_URL = "https://zq.titan007.com/analysis/{match_id}cn.htm"
NOWSCORE_ANALYSIS_URL = "https://m.nowscore.com/Analy/Analysis/{match_id}.htm"
BF_NEXT_URL = "https://bf.titan007.com/football/Next_{yyyymmdd}.htm"


def _clean_team_html(value: object) -> str:
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.find_all(class_="hp"):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _to_int(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def _score_summary(rows: list[dict]) -> dict:
    total = len(rows)
    home_wins = sum(
        1
        for row in rows
        if row["current_home_score"] > row["current_away_score"]
    )
    draws = sum(
        1
        for row in rows
        if row["current_home_score"] == row["current_away_score"]
    )
    away_wins = total - home_wins - draws
    goals_for = sum(row["current_home_score"] for row in rows)
    goals_against = sum(row["current_away_score"] for row in rows)
    return {
        "total": total,
        "matches": total,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "home_win_rate": home_wins / total if total else 0.0,
        "draw_rate": draws / total if total else 0.0,
        "away_win_rate": away_wins / total if total else 0.0,
    }


def parse_desktop_h2h_html(
    html: str,
    *,
    home_team_id: str = "",
    away_team_id: str = "",
) -> dict:
    """Parse zq.titan007.com analysis page's v_data H2H block."""
    home_match = re.search(r"var h2h_home\s*=\s*(\d+)", html)
    away_match = re.search(r"var h2h_away\s*=\s*(\d+)", html)
    data_match = re.search(r"var v_data\s*=\s*(\[.*?\]);", html, re.S)
    if not data_match:
        raise ValueError("分析页未找到 var v_data")

    current_home = int(home_team_id) if home_team_id.isdigit() else 0
    current_away = int(away_team_id) if away_team_id.isdigit() else 0
    if home_match:
        current_home = current_home or int(home_match.group(1))
    if away_match:
        current_away = current_away or int(away_match.group(1))

    try:
        raw_rows = ast.literal_eval(data_match.group(1))
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"v_data 解析失败: {exc}") from exc

    rows: list[dict] = []
    for raw in raw_rows or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 20:
            continue
        home_side_id = _to_int(raw[4])
        away_side_id = _to_int(raw[6])
        row_home_score = _to_int(raw[8])
        row_away_score = _to_int(raw[9])
        if current_home and home_side_id == current_home:
            same_home_side = True
            current_home_score = row_home_score
            current_away_score = row_away_score
        elif current_home and away_side_id == current_home:
            same_home_side = False
            current_home_score = row_away_score
            current_away_score = row_home_score
        else:
            # The v_data block is for the two matched teams and normally has
            # h2h_home/h2h_away nearby; keep the row as-is as a last resort.
            same_home_side = True
            current_home_score = row_home_score
            current_away_score = row_away_score

        rows.append(
            {
                "date": str(raw[0] or ""),
                "league": str(raw[2] or ""),
                "home": _clean_team_html(raw[5]),
                "away": _clean_team_html(raw[7]),
                "home_score": row_home_score,
                "away_score": row_away_score,
                "score": f"{row_home_score}-{row_away_score}",
                "current_home_score": current_home_score,
                "current_away_score": current_away_score,
                "half_score": str(raw[10] or ""),
                "match_id": str(raw[15] or ""),
                "current_home_was_home_side": same_home_side,
            }
        )

    summary = _score_summary(rows)
    return {
        "source": "titan_desktop",
        "match_id": "",
        "home_team_id": str(current_home),
        "away_team_id": str(current_away),
        **summary,
        "rows": rows,
    }


def parse_mobile_h2h_html(html: str) -> dict:
    """Parse m.nowscore.com vsMatches table, oriented by data-ishome."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find(id="vsMatches")
    if box is None:
        raise ValueError("移动分析页未找到 vsMatches")
    table = box.find("table")
    if table is None:
        raise ValueError("移动分析页未找到 H2H 表格")

    rows: list[dict] = []
    for row in table.find_all("tr"):
        match_id = str(row.get("data-scheid") or "").strip()
        if not match_id:
            continue
        home_cell = row.find("td", class_="homeTeam")
        away_cell = row.find("td", class_="awayTeam")
        if home_cell is None or away_cell is None:
            continue
        home_score = _to_int(row.get("data-homescore"))
        away_score = _to_int(row.get("data-awayscore"))
        is_home = str(row.get("data-ishome") or "").strip() == "1"
        if is_home:
            current_home_goals = home_score
            current_away_goals = away_score
        else:
            current_home_goals = away_score
            current_away_goals = home_score

        first_cell = row.find_all("td")[0]
        league_node = first_cell.find(class_="leagueName") if first_cell else None
        date_node = first_cell.find(class_="teamdate") if first_cell else None
        rows.append(
            {
                "date": date_node.get_text(" ", strip=True) if date_node else "",
                "league": league_node.get_text(" ", strip=True) if league_node else "",
                "home": home_cell.get_text(" ", strip=True),
                "away": away_cell.get_text(" ", strip=True),
                "home_score": home_score,
                "away_score": away_score,
                "score": f"{home_score}-{away_score}",
                "current_home_score": current_home_goals,
                "current_away_score": current_away_goals,
                "half_score": (
                    f"{_to_int(row.get('data-halfhome'))}-"
                    f"{_to_int(row.get('data-halfaway'))}"
                ),
                "match_id": match_id,
                "current_home_was_home_side": is_home,
            }
        )

    summary = _score_summary(rows)
    return {
        "source": "nowscore_mobile",
        "match_id": "",
        "home_team_id": "",
        "away_team_id": "",
        **summary,
        "rows": rows,
    }


def fetch_titan_h2h(fetcher: Fetcher, match_id: str) -> dict:
    """Fetch H2H from the desktop page, then try the mobile mirror on failure."""
    last_error: Exception | None = None
    try:
        html = fetcher.fetch(
            TITAN_ANALYSIS_URL.format(match_id=match_id),
            cache_key=f"h2h_desktop:{match_id}",
        )
        return parse_desktop_h2h_html(html)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
    try:
        html = fetcher.fetch(
            NOWSCORE_ANALYSIS_URL.format(match_id=match_id),
            cache_key=f"h2h_mobile:{match_id}",
        )
        return parse_mobile_h2h_html(html)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"H2H 抓取失败 {match_id}: desktop={last_error}; mobile={exc}"
        ) from exc


def parse_bf_next_schedule(html: str) -> list[dict]:
    """Parse schedule variables embedded in BF Next date pages."""
    records: list[dict] = []
    pattern = re.compile(r"\b(zuCaiSchedule|jinZuSchedule|beiDanSchedule)\s*=\s*\"([^\"]*)\"")
    for kind, value in pattern.findall(html):
        if "|" not in value:
            continue
        ids_part, labels_part = value.split("|", 1)
        match_ids = [item.strip() for item in ids_part.split(",") if item.strip()]
        labels = [item.strip() for item in labels_part.split(",") if item.strip()]
        for match_id, label in zip(match_ids, labels):
            issue = ""
            match_no = label
            if "-" in label:
                issue, match_no = label.split("-", 1)
            records.append(
                {
                    "kind": kind,
                    "match_id": match_id,
                    "label": label,
                    "issue": issue,
                    "match_no": match_no,
                }
            )
    return records


def build_sfc_match_map(html: str, issue: str) -> dict[str, str]:
    """Map SFC row number to the Titan match id from zuCaiSchedule."""
    digits = re.sub(r"\D", "", issue)
    if len(digits) < 3:
        return {}
    expected_issue = digits[-3:]
    result: dict[str, str] = {}
    for record in parse_bf_next_schedule(html):
        if record["kind"] != "zuCaiSchedule" or record["issue"] != expected_issue:
            continue
        try:
            match_no = str(int(record["match_no"]))
        except ValueError:
            continue
        result[match_no] = record["match_id"]
    return result


def _row_kickoff(row: dict, issue: str) -> tuple[date, datetime] | None:
    match_date = str(row.get("match_date") or "").strip()
    match_time = str(row.get("match_time") or "").strip()
    parsed_date: date | None = None
    hour = minute = 12

    date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", match_date)
    if date_match:
        parsed_date = date(*(int(part) for part in date_match.groups()))
    time_match = re.search(r"(\d{1,2}):(\d{2})", match_time)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
    if parsed_date is None:
        short_match = re.match(r"(\d{2})-(\d{2})", match_time)
        if issue[:2].isdigit():
            year = 2000 + int(re.sub(r"\D", "", issue)[:2])
        else:
            year = datetime.now().year
        if short_match:
            parsed_date = date(
                year,
                int(short_match.group(1)),
                int(short_match.group(2)),
            )
    if parsed_date is None:
        return None
    return parsed_date, datetime(
        parsed_date.year,
        parsed_date.month,
        parsed_date.day,
        hour,
        minute,
    )


def candidate_schedule_dates(rows: list[dict], issue: str) -> list[str]:
    kickoffs = [
        item
        for row in rows
        if (item := _row_kickoff(row, issue)) is not None
    ]
    if not kickoffs:
        return [datetime.now().strftime("%Y%m%d")]
    first_day, first_kickoff = min(kickoffs, key=lambda item: item[1])
    previous = first_day - timedelta(days=1)
    next_day = first_day + timedelta(days=1)
    if first_kickoff.hour < 12:
        ordered = [previous, first_day, next_day]
    else:
        ordered = [first_day, previous, next_day]
    seen: set[str] = set()
    result: list[str] = []
    for day in ordered:
        value = day.strftime("%Y%m%d")
        if value not in seen:
            seen.add(value)
            result.append(value)
        if len(result) == 3:
            break
    return result


def fetch_issue_schedule(
    fetcher: Fetcher,
    rows: list[dict],
    issue: str,
) -> tuple[dict[str, str], str, str]:
    errors: list[str] = []
    for yyyymmdd in candidate_schedule_dates(rows, issue):
        url = BF_NEXT_URL.format(yyyymmdd=yyyymmdd)
        try:
            html = fetcher.fetch(url, cache_key=f"bf_next:{yyyymmdd}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{yyyymmdd}: {exc}")
            continue
        schedule = build_sfc_match_map(html, issue)
        if schedule:
            return schedule, yyyymmdd, ""
    return {}, "", "; ".join(errors)
