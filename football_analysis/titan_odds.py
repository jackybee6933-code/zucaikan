from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .odds import handicap_line_to_float, total_line_to_float


TITAN_ASIAN_URL = "https://vip.titan007.com/AsianOdds_n.aspx?id={match_id}"
TITAN_TOTAL_URL = "https://vip.titan007.com/OverDown_n.aspx?id={match_id}"

TITAN_CID_PREFERENCE = ("1", "9")
TITAN_CID_LABELS = {"1": "澳门", "9": "威廉"}


def _strip_timestamp(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}\b", "", text)
    text = re.sub(r"\b\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}\b", "", text)
    return text.strip()


def _water(value: object) -> str:
    text = _strip_timestamp(value)
    matches = re.findall(r"\d+(?:\.\d+)?", text)
    return matches[-1] if matches else ""


def _line(value: object) -> str:
    text = _strip_timestamp(value)
    # A line cell can contain a trailing direction marker or a small icon label.
    text = re.sub(r"\s+(?:升|降|↑|↓)\s*$", "", text)
    return text.strip()


def _cell_value(cell, *, line: bool) -> str:
    """Read the visible odds value while ignoring the update timestamp."""
    selector = (
        '[oddstype*="Odds"], [class*="odds"], [class*="Odds"]'
        if not line
        else '[oddstype*="Odds"], [class*="handicap"], [class*="Handicap"]'
    )
    candidates = [node.get_text(" ", strip=True) for node in cell.select(selector)]
    candidates = [item for item in candidates if item]
    raw = candidates[-1] if candidates else cell.get_text(" ", strip=True)
    return _line(raw) if line else _water(raw)


def _company_id(row) -> str:
    node = row.find("input", attrs={"name": "oddsShow"})
    if node is None:
        node = row.find(attrs={"data-id": True})
    return str(node.get("data-id") or "").strip() if node else ""


def parse_titan_rows(html: str, *, kind: str) -> list[list[str]]:
    """Parse Titan's comparison table into the project's common 8-column rows.

    The returned shape is ``[cid, name, current_home, current_line,
    current_away, initial_home, initial_line, initial_away]``. Titan uses
    different company IDs from 500 (1=Macau, 9=William).
    """
    if kind not in {"yazhi", "daxiao"}:
        raise ValueError(f"unsupported Titan odds kind: {kind}")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="odds") or soup.find("table", class_="odds")
    if table is None:
        return []
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cid = _company_id(row)
        cells = row.find_all("td", recursive=False)
        if not cid or len(cells) < 8:
            continue
        if row.get("class") and any(
            marker in row.get("class", []) for marker in ("maxTr", "minTr")
        ):
            continue
        name = " ".join(cells[1].get_text(" ", strip=True).split())
        values = cells[2:8]
        initial_home = _cell_value(values[0], line=False)
        initial_line = _cell_value(values[1], line=True)
        initial_away = _cell_value(values[2], line=False)
        current_home = _cell_value(values[3], line=False)
        current_line = _cell_value(values[4], line=True)
        current_away = _cell_value(values[5], line=False)
        if not (current_line or initial_line):
            continue
        rows.append(
            [
                cid,
                name,
                current_home,
                current_line,
                current_away,
                initial_home,
                initial_line,
                initial_away,
            ]
        )
    return rows


def pick_titan_company(rows: list[list]) -> list | None:
    by_cid = {str(row[0]): row for row in rows}
    for cid in TITAN_CID_PREFERENCE:
        if cid in by_cid:
            return by_cid[cid]
    return rows[0] if rows else None


def parse_titan_market(html: str, *, kind: str) -> dict:
    rows = parse_titan_rows(html, kind=kind)
    row = pick_titan_company(rows)
    if row is None:
        return {}
    line_text = str(row[3] or "").strip()
    name = TITAN_CID_LABELS.get(str(row[0]), str(row[1] or ""))
    if kind == "yazhi":
        return {
            "asian_line_text": line_text,
            "asian_home_line": handicap_line_to_float(line_text),
            "asian_home_water": float(row[2] or 0),
            "asian_away_water": float(row[4] or 0),
            "asian_company": name,
        }
    return {
        "total_line_text": line_text,
        "total_line": total_line_to_float(line_text),
        "over_water": float(row[2] or 0),
        "under_water": float(row[4] or 0),
        "total_company": name,
    }
