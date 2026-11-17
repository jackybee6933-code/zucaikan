from __future__ import annotations

import re

from bs4 import BeautifulSoup
from datetime import datetime

from .odds import fmt_handicap, handicap_line_to_float, parse_floats, split_csv


TRADE_URL = "https://trade.500.com/sfc/"
ISSUE_URL = "https://trade.500.com/sfc/?expect={issue}"
LIVE_ISSUE_URL = "https://live.500.com/zucai.php?e={issue}"
ANALYSIS_URL = "https://odds.500.com/fenxi/shuju-{fixture_id}.shtml"


def extract_issue(html: str) -> str:
    selected = re.search(
        r"<option\b(?=[^>]*\bselected\b)(?=[^>]*\bvalue=[\"'](\d+)[\"'])[^>]*>",
        html,
    )
    if selected:
        return selected.group(1)
    fallback = re.search(r"expect=(\d+)", html)
    return fallback.group(1) if fallback else ""


def parse_sfc_html(html: str, source_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "vsTable"})
    if table is None:
        raise RuntimeError("未找到胜负彩对阵表 vsTable")
    issue = extract_issue(html)
    rows: list[dict] = []
    for row in table.find_all("tr", class_="bet-tb-tr"):
        data_td = row.find("td", class_="td-data")
        href = ""
        if data_td:
            link = data_td.find("a", href=True)
            if link:
                href = str(link.get("href", ""))
        fid = re.search(r"shuju-(\d+)\.shtml", href)
        if not fid:
            continue

        team_td = row.find("td", class_="td-team")
        # 结束的比赛在 team-vs 里是带比分的链接，未开赛的显示文本 VS
        team_vs = team_td.find("i", class_="team-vs") if team_td else None
        final_score = ""
        if team_vs and team_vs.get_text(" ", strip=True).upper() != "VS":
            score_link = team_vs.find("a")
            if score_link:
                final_score = score_link.get_text(" ", strip=True)
        home_node = team_td.find("a", class_="team-l") if team_td else None
        away_node = team_td.find("a", class_="team-r") if team_td else None
        home_team = home_node.get_text(" ", strip=True) if home_node else ""
        away_team = away_node.get_text(" ", strip=True) if away_node else ""
        if not home_team or not away_team:
            vs_text = (team_td.get_text(" ", strip=True) if team_td else "") or str(
                row.get("data-vs", "")
            )
            vs_text = vs_text.replace("VS", " vs ")
            parts = re.split(r"\s+vs\s+", vs_text, flags=re.IGNORECASE)
            if len(parts) >= 2:
                home_team = re.sub(r"[\[0-9\]]", "", parts[0]).strip()
                away_team = re.sub(r"[\[0-9\]]", "", parts[-1]).strip()

        asian = split_csv(row.get("data-asian", ""))
        asian_line_text = asian[1] if len(asian) > 1 else ""
        home_line = handicap_line_to_float(asian_line_text)
        bjpl = parse_floats(row.get("data-bjpl", ""))
        heat = parse_floats(row.get("data-pjgl", ""))
        match_time = ""
        if row.find("td", class_="td-endtime"):
            match_time = row.find("td", class_="td-endtime").get_text(" ", strip=True)
        league = ""
        league_color = ""
        evt_cell = row.find("td", class_="td-evt")
        if evt_cell:
            league = evt_cell.get_text(" ", strip=True)
            # 500 联赛标签是内联背景色（跨期固定），抄到报告页
            evt_link = evt_cell.find("a")
            if evt_link:
                bg = re.search(r"background\s*:\s*(#[0-9A-Fa-f]{6})", str(evt_link.get("style") or ""))
                league_color = bg.group(1) if bg else ""

        rows.append(
            {
                "issue": issue,
                "source_url": source_url or (ISSUE_URL.format(issue=issue) if issue else TRADE_URL),
                "fixture_id": fid.group(1),
                "analysis_url": ANALYSIS_URL.format(fixture_id=fid.group(1)),
                "match_no": row.find("td", class_="td-no").get_text(strip=True)
                if row.find("td", class_="td-no")
                else "",
                "league": league,
                "league_color": league_color,
                "match_time": match_time,
                "home_team": home_team,
                "away_team": away_team,
                "final_score": final_score,
                "europe_home": bjpl[0] if len(bjpl) > 0 else 0.0,
                "europe_draw": bjpl[1] if len(bjpl) > 1 else 0.0,
                "europe_away": bjpl[2] if len(bjpl) > 2 else 0.0,
                "bet_home_pct": heat[0] if len(heat) > 0 else 0.0,
                "bet_draw_pct": heat[1] if len(heat) > 1 else 0.0,
                "bet_away_pct": heat[2] if len(heat) > 2 else 0.0,
                "asian_home_water": float(asian[0]) if asian and asian[0] else 0.0,
                "asian_away_water": float(asian[2]) if len(asian) > 2 and asian[2] else 0.0,
                "asian_line_text": asian_line_text,
                "asian_home_line": home_line,
                "asian_line_display": fmt_handicap(home_line) if asian_line_text else "",
            }
        )
    return rows


def parse_live_sfc_html(
    html: str,
    issue: str = "",
    source_url: str = "",
) -> list[dict]:
    """Parse the live.500.com fallback page into the SFC row shape."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="table_match")
    if table is None:
        raise RuntimeError("未找到备用源胜负彩对阵表 table_match")

    if not issue:
        title = soup.find("title")
        title_text = title.get_text(" ", strip=True) if title else ""
        issue_match = re.search(r"(\d+)期", title_text)
        issue = issue_match.group(1) if issue_match else ""

    rows: list[dict] = []
    for row in table.find_all("tr", attrs={"fid": True}):
        cells = row.find_all("td", recursive=False)
        fixture_id = str(row.get("fid") or "")
        if len(cells) < 8 or not fixture_id:
            continue

        team_cells = row.find_all("td", class_="p_lr01")
        home_node = team_cells[0].find("a") if len(team_cells) > 0 else None
        away_node = team_cells[1].find("a") if len(team_cells) > 1 else None
        home_team = home_node.get_text(" ", strip=True) if home_node else ""
        away_team = away_node.get_text(" ", strip=True) if away_node else ""
        if not home_team or not away_team:
            continue

        data_cell = cells[6]
        line_node = data_cell.find(attrs={"data-ao": True}) or data_cell.find(
            attrs={"data-pb": True}
        )
        line_text = ""
        if line_node:
            line_text = str(
                line_node.get("data-ao") or line_node.get("data-pb") or ""
            ).strip()
            if line_text == "-":
                line_text = str(line_node.get("data-pb") or "").strip()

        analysis_link = row.find("a", href=re.compile(r"shuju-\d+\.shtml"))
        analysis_href = str(analysis_link.get("href") or "") if analysis_link else ""
        if analysis_href.startswith("//"):
            analysis_href = f"https:{analysis_href}"
        elif analysis_href.startswith("/"):
            analysis_href = f"https://odds.500.com{analysis_href}"
        if not analysis_href:
            analysis_href = ANALYSIS_URL.format(fixture_id=fixture_id)

        europe_values = [
            float(value.get_text(strip=True))
            for value in cells[-1].find_all("span")
            if re.fullmatch(r"\d+(?:\.\d+)?", value.get_text(strip=True))
        ]
        status = cells[4].get_text(" ", strip=True) if len(cells) > 4 else ""
        score_home = data_cell.select_one(".clt1")
        score_away = data_cell.select_one(".clt3")
        final_score = ""
        if status in {"完", "完场"} and score_home and score_away:
            home_score = score_home.get_text(strip=True)
            away_score = score_away.get_text(strip=True)
            if home_score.isdigit() and away_score.isdigit():
                final_score = f"{home_score} - {away_score}"

        league_cell = row.find("td", class_="ssbox_01")
        league = league_cell.get_text(" ", strip=True) if league_cell else ""
        league_color = str(league_cell.get("bgcolor") or "") if league_cell else ""
        if league_color and not league_color.startswith("#"):
            league_color = f"#{league_color}"

        rows.append(
            {
                "issue": issue,
                "source_url": source_url or (LIVE_ISSUE_URL.format(issue=issue) if issue else ""),
                "fixture_id": fixture_id,
                "analysis_url": analysis_href,
                "match_no": cells[0].get_text(strip=True),
                "league": league,
                "league_color": league_color,
                "match_time": cells[3].get_text(" ", strip=True),
                "home_team": home_team,
                "away_team": away_team,
                "final_score": final_score,
                "europe_home": europe_values[0] if len(europe_values) > 0 else 0.0,
                "europe_draw": europe_values[1] if len(europe_values) > 1 else 0.0,
                "europe_away": europe_values[2] if len(europe_values) > 2 else 0.0,
                "bet_home_pct": 0.0,
                "bet_draw_pct": 0.0,
                "bet_away_pct": 0.0,
                "asian_home_water": 0.0,
                "asian_away_water": 0.0,
                "asian_line_text": line_text,
                "asian_home_line": handicap_line_to_float(line_text),
                "asian_line_display": fmt_handicap(handicap_line_to_float(line_text))
                if line_text
                else "",
            }
        )
    return rows


def sfc_fetch_url(issue: str | None = None) -> str:
    if not issue:
        return TRADE_URL
    return ISSUE_URL.format(issue=issue)

def extract_match_score(html: str) -> str:
    """Extract the final score from a live/detail page, e.g. '2 - 1'. Empty string if not found.

    未开赛页面的比分牌默认就是 0 - 0，直接采信会把没打的比赛当成 0-0 结束。
    页面正文第一个完整日期时间（如 2026-10-07 02:45）是开赛时间，晚于现在
    就说明比赛还没打，返回空串。取不到开赛时间时保持原行为。
    """
    match = re.search(r'class="score"[^>]*>([^<]{0,40})</span>', html)
    if not match:
        return ""
    score = match.group(1).strip()
    if not re.fullmatch(r"\d+\s*-\s*\d+", score):
        return ""
    kickoff = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", html)
    if kickoff:
        try:
            starts_at = datetime.strptime(kickoff.group(1), "%Y-%m-%d %H:%M")
        except ValueError:
            return score
        # 页面时间是东八区，本机时区须与之一致
        if starts_at > datetime.now():
            return ""
    return score
