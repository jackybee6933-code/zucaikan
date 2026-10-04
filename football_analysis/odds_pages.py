from __future__ import annotations

import json
import re
from pathlib import Path

from .odds import fmt_handicap, handicap_line_to_float, total_line_to_float
from .odds_movement import _handicap_display, analyze_prematch_movement


# 抓取产物文件名：cache/odds_pages_<期号>.json
PAGES_FILENAME = "odds_pages_{issue}.json"

# 500 单场页的公司偏好：澳门(5) 与 odds.xml 的 ASIAN_COMPANY_PREFERENCE 首选项一致，
# 但只在部分页面出现；威廉(293) 在实测 24 个页面里 100% 覆盖，作为兜底。
CID_PREFERENCE = ("5", "293")
CID_LABELS = {"5": "澳门", "293": "威廉"}


def _water(text: object) -> float:
    """'0.670↓' -> 0.67"""
    match = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(match.group(0)) if match else 0.0


def _line_text(text: object) -> str:
    """'受半球 降' / '2.5↓' -> '受半球' / '2.5'（箭头与升降标记是涨跌方向，不属于盘口本身）。"""
    return re.sub(r"[\s↑↓升降]+$", "", str(text or "").strip())


# 抓取产物里的页面状态。只有这两种算「结果可信」；timeout/error 意味着抓取失败，
# 会被上游忽略并告警——绝不能当成「上游没开盘」而静默当成事实。
PAGE_STATUSES = ("ok", "empty")


def _status(page: dict) -> str:
    """取页面状态；旧产物没有 status 字段，按有无公司行推断，保持向后兼容。"""
    status = str(page.get("status") or "").strip()
    if status:
        return status
    return "ok" if page.get("rows") else "empty"


def page_failures(raw: dict) -> list[str]:
    """返回抓取失败（timeout/error）的页面 key，供上层告警。"""
    return [
        str(key)
        for key, page in (raw or {}).items()
        if isinstance(page, dict) and _status(page) not in PAGE_STATUSES
    ]


def _pick_company(rows: list[list]) -> list | None:
    by_cid = {str(row[0]): row for row in rows}
    for cid in CID_PREFERENCE:
        if cid in by_cid:
            return by_cid[cid]
    return rows[0] if rows else None


def parse_odds_pages(raw: dict) -> dict[str, dict]:
    """把 odds.500.com 单场页抓取结果转成与 parse_odds_xml 同构的字典，按 fixture_id 索引。

    每条记录形如 {"<fixture_id>|<kind>": {"rows": [[cid, 即时主水, 即时盘, 即时客水, 初主水, 初盘, 初客水]]}}，
    kind 取 yazhi（亚指）/ daxiao（大小指数）。抓不到公司的页面直接跳过，交给上层兜底。
    """
    result: dict[str, dict] = {}
    for key, page in (raw or {}).items():
        if not isinstance(page, dict) or _status(page) not in PAGE_STATUSES:
            continue
        fixture_id, _, kind = str(key).partition("|")
        rows = page.get("rows") or []
        row = _pick_company(rows)
        if not fixture_id or row is None:
            continue
        slot = result.setdefault(fixture_id, {})
        label = CID_LABELS.get(str(row[0]), "")
        text = _line_text(row[2])
        if kind == "yazhi":
            line = handicap_line_to_float(text)
            slot.update(
                {
                    "asian_line_text": text,
                    "asian_home_line": line,
                    "asian_line_display": fmt_handicap(line) if text else "",
                    "asian_home_water": _water(row[1]),
                    "asian_away_water": _water(row[3]),
                    "asian_company": label,
                }
            )
        elif kind == "daxiao":
            slot.update(
                {
                    "total_line_text": text,
                    "total_line": total_line_to_float(text),
                    "over_water": _water(row[1]),
                    "under_water": _water(row[3]),
                    "total_company": label,
                }
            )
    return result


# 公司间盘口分歧阈值：同一页各公司即时盘口最大差距达到该值即提示分歧。
SPLIT_WATCH = 0.5

# 单场页对未登录用户打码公司名，这里只登记已确认的；未登记的直接显示 cid。
COMPANY_NAMES = {"5": "澳门", "293": "威廉", "4": "皇冠", "6": "伟德"}


def _spread(rows: list[list], converter) -> list[float]:
    """各公司即时盘口，按让球幅度升序；不足两家公司时返回空列表。"""
    values = []
    for row in rows:
        line = converter(_line_text(row[2]))
        if line:
            values.append(line)
    return sorted(values, key=abs) if len(values) >= 2 else []


def _company_rows(rows: list[list]) -> list[dict]:
    """各公司的初盘/即时盘口，供悬停弹层展示；保持页面原有公司顺序。"""
    out = []
    for row in rows:
        init_text = _line_text(row[5])
        cur_text = _line_text(row[2])
        if not init_text and not cur_text:
            continue
        cid = str(row[0])
        out.append(
            {
                "cid": cid,
                "name": COMPANY_NAMES.get(cid, ""),
                "initial": init_text or "—",
                "current": cur_text or "—",
            }
        )
    return out


def _split_block(rows: list[list], label: str, converter) -> dict | None:
    """盘口分歧达到 SPLIT_WATCH 时返回弹层数据块，否则返回 None。"""
    values = _spread(rows, converter)
    if not values:
        return None
    low, high = values[0], values[-1]
    if abs(high - low) < SPLIT_WATCH:
        return None
    picked = _pick_company(rows)
    return {
        "market": label,
        "picked": str(picked[0]) if picked else "",
        "rows": _company_rows(rows),
    }


def build_pages_movements(raw: dict, teams_by_fixture: dict | None = None) -> dict[str, dict]:
    """用单场页的初盘/即时盘构造异动检测输入，产出与 Nowscore 同构的分析结果。

    Nowscore 赛前指数只覆盖少数场次，单场页覆盖更广；两者结构一致，可直接喂给
    analyze_prematch_movement。抓不到公司的页面不出现在结果里。
    """
    from .odds_movement import _handicap_display, analyze_prematch_movement

    result: dict[str, dict] = {}
    for key, page in (raw or {}).items():
        fixture_id, _, kind = str(key).partition("|")
        rows = page.get("rows") or []
        row = _pick_company(rows)
        if not fixture_id or row is None:
            continue
        init_text = _line_text(row[5])
        cur_text = _line_text(row[2])
        if kind == "yazhi":
            init_line = handicap_line_to_float(init_text)
            cur_line = handicap_line_to_float(cur_text)
            slot = result.setdefault(fixture_id, {})
            slot["asian"] = {
                "available": True,
                "initial": {
                    "line_text": init_text,
                    "line": init_line,
                    "line_display": _handicap_display(init_line),
                    "home_water": _water(row[4]),
                    "away_water": _water(row[6]),
                },
                "current": {
                    "line_text": cur_text,
                    "line": cur_line,
                    "line_display": _handicap_display(cur_line),
                    "home_water": _water(row[1]),
                    "away_water": _water(row[3]),
                },
            }
        elif kind == "daxiao":
            slot = result.setdefault(fixture_id, {})
            slot["total"] = {
                "available": True,
                "initial": {
                    "line_text": init_text,
                    "line": total_line_to_float(init_text),
                    "over_water": _water(row[4]),
                    "under_water": _water(row[6]),
                },
                "current": {
                    "line_text": cur_text,
                    "line": total_line_to_float(cur_text),
                    "over_water": _water(row[1]),
                    "under_water": _water(row[3]),
                },
            }

    splits: dict[str, list[str]] = {}
    for key, page in (raw or {}).items():
        fixture_id, _, kind = str(key).partition("|")
        rows = page.get("rows") or []
        if not fixture_id or len(rows) < 2:
            continue
        if kind == "yazhi":
            block = _split_block(rows, "亚盘", handicap_line_to_float)
        elif kind == "daxiao":
            block = _split_block(rows, "大小球", total_line_to_float)
        else:
            continue
        if block:
            splits.setdefault(fixture_id, []).append(block)

    analysed: dict[str, dict] = {}
    for fixture_id, slot in result.items():
        payload = dict(slot)
        payload["teams"] = (teams_by_fixture or {}).get(fixture_id)
        analysis = analyze_prematch_movement(payload)
        if fixture_id in splits:
            analysis["split"] = splits[fixture_id]
        analysed[fixture_id] = analysis
    return analysed


def load_odds_pages_raw(cache_root: str | Path | None, issue: str) -> dict:
    """读取 cache/odds_pages_<期号>.json 原始产物；没有产物或解析失败时返回空字典。"""
    if not cache_root:
        return {}
    path = Path(cache_root) / PAGES_FILENAME.format(issue=issue)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_odds_pages(cache_root: str | Path | None, issue: str) -> dict[str, dict]:
    """读取 cache/odds_pages_<期号>.json 并转成盘口字典；没有产物时返回空字典。"""
    return parse_odds_pages(load_odds_pages_raw(cache_root, issue))
