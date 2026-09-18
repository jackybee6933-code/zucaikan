from __future__ import annotations

from bs4 import BeautifulSoup


JCZQ_URL = "https://trade.500.com/jczq/?playid=312&g=2"


def parse_jczq_html(html: str) -> dict[str, dict]:
    """Parse jczq betting rows into a fixture_id -> enrichment map."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, dict] = {}
    for row in soup.find_all("tr", class_="bet-tb-tr"):
        fixture_id = str(row.get("data-fixtureid") or "").strip()
        if not fixture_id:
            continue
        team_td = row.find("td", class_="td-team")
        home_node = team_td.find("a", class_="team-l") if team_td else None
        away_node = team_td.find("a", class_="team-r") if team_td else None
        home_name = home_node.get_text(" ", strip=True) if home_node else str(
            row.get("data-homesxname", "") or ""
        )
        away_name = away_node.get_text(" ", strip=True) if away_node else str(
            row.get("data-awaysxname", "") or ""
        )
        result[fixture_id] = {
            "fixture_id": fixture_id,
            "info_match_id": str(row.get("data-infomatchid") or "").strip(),
            "home_id": str(row.get("data-homeid") or "").strip(),
            "away_id": str(row.get("data-awayid") or "").strip(),
            "home_name": home_name,
            "away_name": away_name,
            "league": str(row.get("data-simpleleague") or "").strip(),
            "match_no": str(row.get("data-matchnum") or "").strip(),
            "match_date": str(row.get("data-matchdate") or "").strip(),
            "match_time": str(row.get("data-matchtime") or "").strip(),
            "rangqiu": str(row.get("data-rangqiu") or "").strip(),
        }
    return result
