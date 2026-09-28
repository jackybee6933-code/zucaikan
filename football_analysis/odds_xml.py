from __future__ import annotations

import xml.etree.ElementTree as ET

from .odds import (
    fmt_handicap,
    handicap_line_to_float,
    market_implied_from_odds,
    parse_floats,
    split_csv,
    total_line_to_float,
)


ODDS_XML_URL = "https://www.500.com/static/public/jczq/xml/odds/odds.xml"


# 500.com 的盘口按博彩公司分属性存放，如 <asian am="..." hg="..." bet365="..."/>。
# 亚盘/大小盘以澳门为参考盘口，澳门缺数据时按序回退到第一家有数据的公司；
# 欧赔类则以所有公司的平均值优先。
ASIAN_COMPANY_PREFERENCE = ("am", "hg", "bet365", "lb", "wl", "avg")
EUROPE_COMPANY_PREFERENCE = ("avg", "wl", "am", "lb", "bet365", "hg")

COMPANY_LABELS = {
    "avg": "平均",
    "wl": "威廉",
    "am": "澳门",
    "lb": "立博",
    "bet365": "bet365",
    "hg": "皇冠",
}


def company_label(key: str) -> str:
    return COMPANY_LABELS.get(key, key)


def _pick_company_odds(
    node: ET.Element | None,
    preference: tuple[str, ...],
) -> tuple[str, str]:
    """Return (odds_text, company_key) from the first company with data.

    Preferred companies are tried in order, then any other company attribute
    on the node, so a missing 澳门盘 no longer drops the whole market.
    """
    if node is None:
        return "", ""
    tried = set(preference)
    ordered = list(preference)
    ordered.extend(key for key in node.attrib if key not in tried and key != "id")
    for key in ordered:
        value = str(node.get(key) or "").strip()
        if value:
            return value, key
    return "", ""


def _gl_market(gl_text: str) -> dict[str, float]:
    values = parse_floats(gl_text)
    if len(values) == 3 and sum(values) > 0:
        total = sum(values)
        return {
            "home": values[0] / total,
            "draw": values[1] / total,
            "away": values[2] / total,
        }
    return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}


def parse_odds_xml(raw: bytes | str) -> dict[str, dict]:
    if isinstance(raw, bytes):
        root = ET.fromstring(raw)
    else:
        root = ET.fromstring(raw.encode("utf-8"))
    result: dict[str, dict] = {}
    for match in root.findall(".//match"):
        match_id = str(match.get("id") or "").strip()
        asian_text, asian_company = _pick_company_odds(
            match.find("asian"), ASIAN_COMPANY_PREFERENCE
        )
        dxq_text, total_company = _pick_company_odds(
            match.find("dxq"), ASIAN_COMPANY_PREFERENCE
        )
        europe_text, europe_company = _pick_company_odds(
            match.find("europe"), EUROPE_COMPANY_PREFERENCE
        )
        gl_text, _gl_company = _pick_company_odds(
            match.find("gl"), EUROPE_COMPANY_PREFERENCE
        )
        rq_text, _rq_company = _pick_company_odds(
            match.find("rq"), EUROPE_COMPANY_PREFERENCE
        )

        asian = split_csv(asian_text)
        dxq = split_csv(dxq_text)
        europe = parse_floats(europe_text)
        gl_market = _gl_market(gl_text)
        asian_line_text = asian[1] if len(asian) > 1 else ""
        dxq_line_text = dxq[1] if len(dxq) > 1 else ""
        home_line = handicap_line_to_float(asian_line_text)
        total_line = total_line_to_float(dxq_line_text)

        result[match_id] = {
            "match_id": match_id,
            "process_date": str(match.get("processdate") or "").strip(),
            "process_name": str(match.get("processname") or "").strip(),
            "europe_home": europe[0] if len(europe) > 0 else 0.0,
            "europe_draw": europe[1] if len(europe) > 1 else 0.0,
            "europe_away": europe[2] if len(europe) > 2 else 0.0,
            "europe_company": europe_company,
            "market_home": gl_market["home"],
            "market_draw": gl_market["draw"],
            "market_away": gl_market["away"],
            "asian_home_water": float(asian[0]) if asian and asian[0] else 0.0,
            "asian_away_water": float(asian[2]) if len(asian) > 2 and asian[2] else 0.0,
            "asian_line_text": asian_line_text,
            "asian_home_line": home_line,
            "asian_line_display": fmt_handicap(home_line) if asian_line_text else "",
            "asian_company": asian_company,
            "over_water": float(dxq[0]) if dxq and dxq[0] else 0.0,
            "under_water": float(dxq[2]) if len(dxq) > 2 and dxq[2] else 0.0,
            "total_line_text": dxq_line_text,
            "total_line": total_line,
            "total_company": total_company,
            "handicap_3way_home": parse_floats(rq_text)[0] if parse_floats(rq_text) else 0.0,
            "handicap_3way_draw": parse_floats(rq_text)[1] if len(parse_floats(rq_text)) > 1 else 0.0,
            "handicap_3way_away": parse_floats(rq_text)[2] if len(parse_floats(rq_text)) > 2 else 0.0,
        }
    return result
