from __future__ import annotations

import re
from typing import Iterable


def split_csv(value: str | None, expected: int = 3) -> list[str]:
    parts = (str(value or "").replace("，", ",")).split(",")
    parts = [part.strip() for part in parts]
    return (parts + [""] * expected)[:expected]


def safe_float(value: str | float | int | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_floats(value: str | None) -> list[float]:
    parts = split_csv(value)
    return [safe_float(part) for part in parts]


def normalize_probabilities(*values: float) -> list[float]:
    positive = [max(float(value), 0.001) for value in values]
    total = sum(positive)
    return [value / total for value in positive]


def market_implied_from_odds(home: float, draw: float, away: float) -> dict[str, float]:
    if home <= 0 and draw <= 0 and away <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    implied = [1.0 / odd if odd > 1.01 else 0.001 for odd in (home, draw, away)]
    total = sum(implied) or 1.0
    return {
        "home": implied[0] / total,
        "draw": implied[1] / total,
        "away": implied[2] / total,
    }


_HANDICAP_PART_MAP = {
    "平手": 0.0,
    "平": 0.0,
    "半球": 0.5,
    "半": 0.5,
    "一球": 1.0,
    "一": 1.0,
    "球半": 1.5,
    "两球": 2.0,
    "两": 2.0,
    "两球半": 2.5,
    "三球": 3.0,
    "三": 3.0,
    "三球半": 3.5,
    "四球": 4.0,
    "四": 4.0,
    "四球半": 4.5,
    "五球": 5.0,
    "五": 5.0,
    "五球半": 5.5,
    "六球": 6.0,
    "六": 6.0,
}


def handicap_part_value(part: str) -> float | None:
    text = part.strip().replace(" ", "")
    if not text:
        return None
    if text in _HANDICAP_PART_MAP:
        return _HANDICAP_PART_MAP[text]
    # Handles "受半球", "受让一球", "半球/一球" style fragments.
    text = text.replace("受让", "").replace("让", "")
    if text in _HANDICAP_PART_MAP:
        return _HANDICAP_PART_MAP[text]
    numeric = re.search(r"\d+(?:\.\d+)?", text)
    if numeric:
        return float(numeric.group(0))
    return None


def split_handicap_parts(text: str) -> tuple[list[str], bool]:
    value = str(text or "").replace(" ", "").replace("，", "/")
    received = "受" in value
    value = value.replace("受让", "受").replace("受", "").replace("让", "")
    parts = [part for part in re.split(r"[/／]", value) if part]
    return parts, received


def handicap_line_to_float(text: str) -> float:
    """Return line from home perspective; positive means home receives a goal start."""
    if not str(text or "").strip():
        return 0.0
    parts, received = split_handicap_parts(text)
    values = [handicap_part_value(part) for part in parts]
    values = [value for value in values if value is not None]
    if not values:
        return 0.0
    magnitude = sum(values) / len(values)
    return magnitude if received else -magnitude


def _signed_numeric_part(part: str) -> float | None:
    text = str(part or "").strip()
    if not text:
        return None
    sign = -1.0 if text.startswith("-") else 1.0
    text = text.lstrip("+-")
    numeric = re.search(r"\d+(?:\.\d+)?", text)
    return sign * float(numeric.group(0)) if numeric else None


def nowscore_handicap_numeric(text: str) -> float:
    """Return Nowscore signed line; positive means the home side gives goals."""
    value = str(text or "").replace(" ", "").replace("＋", "+").replace("－", "-")
    if not value or value in {"0", "平手"}:
        return 0.0
    parts = [part for part in re.split(r"[/／]", value) if part]
    if not parts:
        return 0.0
    if value.startswith(("-", "+")):
        sign = -1.0 if value.startswith("-") else 1.0
        magnitudes = [handicap_part_value(part) for part in parts]
        values = [item for item in magnitudes if item is not None]
        return sign * (sum(values) / len(values)) if values else 0.0
    signed_values = [_signed_numeric_part(part) for part in parts]
    signed_values = [item for item in signed_values if item is not None]
    return sum(signed_values) / len(signed_values) if signed_values else 0.0


def nowscore_handicap_to_home_line(text: str) -> float:
    """Convert Nowscore handicap text to this project's home-perspective line."""
    return -nowscore_handicap_numeric(text)


def total_line_to_float(text: str) -> float:
    parts, _received = split_handicap_parts(str(text or ""))
    values = [handicap_part_value(part) for part in parts]
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0.0


def fmt_probability(value: float) -> str:
    return f"{value * 100:.1f}%"


def fmt_handicap(home_line: float) -> str:
    if abs(home_line) < 0.001:
        return "平手"
    magnitude = abs(home_line)
    whole_goal_labels = {
        0.5: "半球",
        1.0: "一球",
        1.5: "球半",
        2.0: "两球",
        2.5: "两球半",
        3.0: "三球",
        3.5: "三球半",
        4.0: "四球",
        4.5: "四球半",
        5.0: "五球",
        5.5: "五球半",
        6.0: "六球",
    }
    key = round(magnitude, 2)
    if key in whole_goal_labels:
        label = whole_goal_labels[key]
    elif key == 0.75:
        label = "半球/一球"
    elif key == 0.25:
        label = "平手/半球"
    elif key == 1.25:
        label = "一球/球半"
    elif key == 1.75:
        label = "球半/两球"
    elif key == 2.25:
        label = "两球/两球半"
    elif key == 2.75:
        label = "两球半/三球"
    elif key == 3.25:
        label = "三球/三球半"
    else:
        label = f"{magnitude:g}"
    return f"受{label}" if home_line > 0 else label


def weighted_rating(values: Iterable[float]) -> float:
    items = [float(value) for value in values if value is not None]
    if not items:
        return 0.0
    total = 0.0
    weight_sum = 0.0
    for idx, value in enumerate(items):
        weight = 1.0 + idx * 0.15
        total += value * weight
        weight_sum += weight
    return total / weight_sum
