from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .odds import fmt_handicap, handicap_line_to_float, parse_floats, split_csv


TRADE_URL = "https://trade.500.com/sfc/"
ISSUE_URL = "https://trade.500.com/sfc/?expect={issue}"
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
        league = row.find("td", class_="td-evt").get_text(" ", strip=True) if row.find(
            "td", class_="td-evt"
        ) else ""

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


def sfc_fetch_url(issue: str | None = None) -> str:
    if not issue:
        return TRADE_URL
    return ISSUE_URL.format(issue=issue)

def extract_match_score(html: str) -> str:
    """Extract the current score from a live/detail page, e.g. '2 - 1'. Empty string if not found."""
    match = re.search(r'class="score"[^>]*>([^<]{0,40})</span>', html)
    if not match:
        return ""
    score = match.group(1).strip()
    return score if re.fullmatch(r"\d+\s*-\s*\d+", score) else ""
