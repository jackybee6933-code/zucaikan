from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .http import Fetcher
from .odds import (
    fmt_handicap,
    nowscore_handicap_to_home_line,
    total_line_to_float,
)


NOWSCORE_MOVEMENT_URL = "https://m.nowscore.com/Analy/Analysis/{match_id}.htm"
SOURCE_NAME = "nowscore_prematch"

ASIAN_LINE_WATCH = 0.25
ASIAN_LINE_BIG = 0.5
WATER_WATCH = 0.08

_ROW_KEYS = {4: "europe", 1: "asian", 2: "total"}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or default).strip())
    except (TypeError, ValueError):
        return default


def _line_text(value: object) -> str:
    return str(value or "").strip()


def _handicap_display(home_line: float) -> str:
    if abs(home_line) < 0.001:
        return "平手"
    direction = "主让" if home_line < 0 else "主受"
    return f"{direction}{fmt_handicap(-abs(home_line))}"


def _format_water(home_water: float, away_water: float) -> str:
    if home_water <= 0 and away_water <= 0:
        return ""
    return f"{home_water:.2f}/{away_water:.2f}"


def _parse_row_values(row) -> tuple[list[float], list[str], list[float]] | None:
    cells = row.find_all("td")
    texts = [cell.get_text(" ", strip=True) for cell in cells]
    if len(texts) < 6:
        return None
    return (
        [_safe_float(texts[0]), _safe_float(texts[2])],
        [_line_text(texts[1]), _line_text(texts[4])],
        [_safe_float(texts[3]), _safe_float(texts[5])],
    )


def _prematch_rows(soup: BeautifulSoup):
    for bar in soup.find_all("div", class_="fenxiBar"):
        if bar.get_text(" ", strip=True) != "赛前指数":
            continue
        box = bar.find_next_sibling("div", class_="contentBox")
        table = box.find("table") if box is not None else None
        if table is None:
            continue
        for row in table.find_all("tr"):
            onclick = str(row.get("onclick") or "")
            match = re.search(r"GoUrl\((\d+),0\)", onclick)
            if not match:
                continue
            key = _ROW_KEYS.get(int(match.group(1)))
            if key is not None:
                yield key, row


def parse_prematch_movement_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {"asian": {}, "total": {}, "europe": {}}
    found = False
    for key, row in _prematch_rows(soup):
        parsed = _parse_row_values(row)
        if parsed is None:
            continue
        waters, lines, current_waters = parsed
        initial_home_water, initial_away_water = waters
        initial_line_text, current_line_text = lines
        current_home_water, current_away_water = current_waters
        found = True
        if key == "europe":
            result[key] = {
                "available": True,
                "initial": {
                    "home": initial_home_water,
                    "draw": _safe_float(initial_line_text),
                    "away": initial_away_water,
                },
                "current": {
                    "home": current_home_water,
                    "draw": _safe_float(current_line_text),
                    "away": current_away_water,
                },
            }
        elif key == "asian":
            initial_home_line = nowscore_handicap_to_home_line(initial_line_text)
            current_home_line = nowscore_handicap_to_home_line(current_line_text)
            result[key] = {
                "available": True,
                "initial": {
                    "line_text": initial_line_text,
                    "line": initial_home_line,
                    "line_display": _handicap_display(initial_home_line),
                    "home_water": initial_home_water,
                    "away_water": initial_away_water,
                },
                "current": {
                    "line_text": current_line_text,
                    "line": current_home_line,
                    "line_display": _handicap_display(current_home_line),
                    "home_water": current_home_water,
                    "away_water": current_away_water,
                },
            }
        else:
            result[key] = {
                "available": True,
                "initial": {
                    "line_text": initial_line_text,
                    "line": total_line_to_float(initial_line_text),
                    "over_water": initial_home_water,
                    "under_water": initial_away_water,
                },
                "current": {
                    "line_text": current_line_text,
                    "line": total_line_to_float(current_line_text),
                    "over_water": current_home_water,
                    "under_water": current_away_water,
                },
            }
    result["source"] = SOURCE_NAME
    result["found"] = found
    return result


def _water_preferred(home_water: float, away_water: float) -> str | None:
    if home_water <= 0 or away_water <= 0:
        return None
    return "home" if home_water < away_water else "away"


def _handicap_sides(home_line: float) -> tuple[str, str]:
    if home_line > 0.001:
        return "客队", "主队"
    if home_line < -0.001:
        return "主队", "客队"
    return "", ""


def _asian_movement(asian: dict) -> dict:
    if not asian.get("available"):
        return {"available": False, "level": "稳定", "note": ""}
    initial = asian["initial"]
    current = asian["current"]
    initial_line = float(initial["line"])
    current_line = float(current["line"])
    line_change = abs(current_line) - abs(initial_line)
    kind = ""
    home_delta = float(current["home_water"]) - float(initial["home_water"])
    away_delta = float(current["away_water"]) - float(initial["away_water"])
    initial_water = _format_water(float(initial["home_water"]), float(initial["away_water"]))
    current_water = _format_water(float(current["home_water"]), float(current["away_water"]))
    old_text = f"{initial['line_display']} {initial_water}".strip()
    new_text = f"{current['line_display']} {current_water}".strip()

    level = "稳定"
    note = ""
    direction = ""
    lean = ""
    if abs(line_change) >= ASIAN_LINE_BIG:
        level = "重点关注"
        direction = "升盘" if line_change > 0 else "降盘"
    elif abs(line_change) >= ASIAN_LINE_WATCH:
        level = "关注"
        direction = "升盘" if line_change > 0 else "降盘"
    if direction:
        kind = direction
        ref_line = current_line if abs(current_line) > 0.001 else initial_line
        letting_side, receiving_side = _handicap_sides(ref_line)
        if line_change > 0:
            lean = f"{letting_side}（上盘/让球方）" if letting_side else "上盘/让球方"
        else:
            lean = f"{receiving_side}（下盘/受让方）" if receiving_side else "下盘/受让方"
        note = f"让球盘{direction}：{old_text} -> {new_text}"
        if lean:
            note += f"，倾向{lean}赢盘"
    else:
        initial_side = _water_preferred(
            float(initial["home_water"]), float(initial["away_water"])
        )
        current_side = _water_preferred(
            float(current["home_water"]), float(current["away_water"])
        )
        water_delta = max(abs(home_delta), abs(away_delta))
        if initial_side and current_side and initial_side != current_side:
            level = "关注"
            kind = "水位反转"
            letting_side, receiving_side = _handicap_sides(current_line)
            side_label = "主队" if current_side == "home" else "客队"
            if side_label == receiving_side:
                role_text = "（下盘/受让方）"
            elif side_label == letting_side:
                role_text = "（上盘/让球方）"
            else:
                role_text = ""
            note = (
                f"亚盘水位反转：{old_text} -> {new_text}，"
                f"即时低水指向{side_label}{role_text}"
            )
        elif water_delta >= WATER_WATCH:
            level = "关注"
            kind = "水位摆动"
            direction = []
            if abs(home_delta) >= WATER_WATCH:
                direction.append(f"主水{'+' if home_delta > 0 else '-'}{abs(home_delta):.2f}")
            if abs(away_delta) >= WATER_WATCH:
                direction.append(f"客水{'+' if away_delta > 0 else '-'}{abs(away_delta):.2f}")
            note = f"亚盘水位摆动：{old_text} -> {new_text}（{'，'.join(direction)}）"
    return {
        "available": True,
        "level": level,
        "note": note,
        "initial": initial,
        "current": current,
        "line_change": line_change,
        "home_water_change": home_delta,
        "away_water_change": away_delta,
        "direction": direction,
        "lean": lean,
        "kind": kind,
    }


def analyze_prematch_movement(raw: dict) -> dict:
    asian = _asian_movement(raw.get("asian") or {})
    reasons = [asian["note"]] if asian.get("available") and asian.get("note") else []
    max_level = asian["level"] if asian.get("available") else "稳定"
    return {
        "available": bool((raw.get("asian") or {}).get("available")),
        "source": SOURCE_NAME,
        "asian": asian,
        "total": raw.get("total") or {},
        "attention": bool(reasons),
        "level": max_level,
        "kind": asian.get("kind") or "",
        "reasons": reasons,
    }


def fetch_nowscore_movement(fetcher: Fetcher, match_id: str) -> dict:
    html = fetcher.fetch(
        NOWSCORE_MOVEMENT_URL.format(match_id=match_id),
        cache_key=f"movement_mobile:{match_id}",
    )
    return analyze_prematch_movement(parse_prematch_movement_html(html))
