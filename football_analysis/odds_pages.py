from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .odds import fmt_handicap, handicap_line_to_float, total_line_to_float
from .odds_movement import _handicap_display, analyze_prematch_movement


# 抓取产物文件名：cache/odds_pages_<期号>.json
PAGES_FILENAME = "odds_pages_{issue}.json"
PAGES_META_FILENAME = "odds_pages_{issue}.meta.json"

# 500 单场页的公司偏好：澳门(5) 主选，威廉(293) 作为备选。
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


# 盘口每场都在动，产物放太久就可能不是最新；超过该小时数在报告里告警。
PAGES_STALE_HOURS = 1.0


def pages_age_hours(cache_root: str | Path | None, issue: str) -> float | None:
    """产物距今多少小时；文件不存在或没给 cache 目录时返回 None。"""
    if not cache_root:
        return None
    path = Path(cache_root) / PAGES_FILENAME.format(issue=issue)
    if not path.exists():
        return None
    return max(0.0, (time.time() - path.stat().st_mtime) / 3600)


def pages_kind_age_hours(
    cache_root: str | Path | None, issue: str, kind: str
) -> float | None:
    """Return the age of one page kind, or None when no per-kind timestamp exists."""
    if not cache_root:
        return None
    path = Path(cache_root) / PAGES_META_FILENAME.format(issue=issue)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        captured_at = float(data.get(kind))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return max(0.0, (time.time() - captured_at) / 3600)


def has_odds_rows(cache_root: str | Path | None, issue: str) -> bool:
    """产物里是否至少有一个可信的 yazhi（亚指）公司行。

    只看内容不看 mtime：残缺产物（如 AI 分析补抓 shuju 合并后只有
    ``|shuju`` 键）虽然 mtime 很新，却给不出任何盘口数据。抓取失败
    （timeout/error）的页不算，上游未开盘（empty）也不算——有可信的
    抓取结果但确实零盘口时，重抓只会再拿一次同样的结果。
    """
    if not cache_root:
        return False
    path = Path(cache_root) / PAGES_FILENAME.format(issue=issue)
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        key.endswith("|yazhi")
        and isinstance(page, dict)
        and _status(page) == "ok"
        and page.get("rows")
        for key, page in raw.items()
    )


def page_failures(raw: dict) -> list[str]:
    """返回抓取失败（timeout/error）的页面 key，供上层告警。"""
    return [
        str(key)
        for key, page in (raw or {}).items()
        if isinstance(page, dict) and _status(page) not in PAGE_STATUSES
    ]


def shuju_text(raw: dict, fixture_id: str) -> str:
    """取该场「数据分析页」的球队资料文本（供大模型阅读）；没抓或抓失败返回空串。"""
    page = (raw or {}).get(f"{fixture_id}|shuju") or {}
    if not isinstance(page, dict) or _status(page) != "ok":
        return ""
    return str(page.get("text") or "").strip()


_H2H_HEADINGS = ("对赛成绩", "对战历史", "交战历史")
_REFERENCE_HEADINGS = ("推介", "心水推荐", *_H2H_HEADINGS, "分析")


def _shuju_sections(text: str, *, preserve_lines: bool = False) -> list[tuple[str, str]]:
    """Split the scraper's ``## heading`` blocks without changing their text."""
    matches = list(re.finditer(r"(?m)^##\s*(.*?)\s*$", str(text or "")))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw_body = text[match.end():end]
        if preserve_lines:
            body = "\n".join(
                re.sub(r"\s+", " ", line).strip()
                for line in raw_body.splitlines()
                if line.strip()
            )
        else:
            body = re.sub(r"\s+", " ", raw_body).strip()
        if body:
            sections.append((match.group(1).strip(), body))
    return sections


def shuju_references(raw: dict, fixture_id: str) -> dict[str, str]:
    """Extract display-only 500-page references; these are never model input."""
    found: dict[str, str] = {}
    for heading, body in _shuju_sections(shuju_text(raw, fixture_id), preserve_lines=True):
        matched = next((name for name in _REFERENCE_HEADINGS if name in heading), None)
        if matched and matched not in found:
            if matched not in _H2H_HEADINGS:
                body = re.sub(r"\s+", " ", body).strip()
            found[matched] = body[:800]
    return found


def shuju_analysis_text(raw: dict, fixture_id: str) -> str:
    """Return 500 data text with display-only recommendation sections removed."""
    text = shuju_text(raw, fixture_id)
    if not text:
        return ""
    sections = _shuju_sections(text)
    if not sections:
        return text[:9000]
    kept = [
        f"## {heading}\n{body}"
        for heading, body in sections
        if not any(name in heading for name in _REFERENCE_HEADINGS)
    ]
    return "\n".join(kept)[:9000]


def _pick_company(rows: list[list]) -> list | None:
    by_cid = {str(row[0]): row for row in rows}
    for cid in CID_PREFERENCE:
        if cid in by_cid:
            return by_cid[cid]
    return rows[0] if rows else None


def _shifted(rows: list[list]) -> list[list]:
    """补齐公司名列：旧产物 rows 只有 7 列（缺公司名），统一成 8 列。"""
    out = []
    for row in rows:
        if len(row) >= 8:
            out.append(list(row))
        elif len(row) == 7:
            out.append([row[0], ""] + list(row[1:]))
        else:
            out.append(list(row))
    return out


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
        rows = _shifted(page.get("rows") or [])
        row = _pick_company(rows)
        if not fixture_id or row is None:
            continue
        slot = result.setdefault(fixture_id, {})
        # 报告来源仍用已确认的中文名（澳门/威廉），行内打码名只用于分歧弹窗。
        name = CID_LABELS.get(str(row[0])) or (str(row[1]) if len(row) > 1 and row[1] else "")
        text = _line_text(row[3])
        if kind == "yazhi":
            line = handicap_line_to_float(text)
            slot.update(
                {
                    "asian_line_text": text,
                    "asian_home_line": line,
                    "asian_line_display": fmt_handicap(line) if text else "",
                    "asian_home_water": _water(row[2]),
                    "asian_away_water": _water(row[4]),
                    "asian_company": name,
                }
            )
        elif kind == "daxiao":
            slot.update(
                {
                    "total_line_text": text,
                    "total_line": total_line_to_float(text),
                    "over_water": _water(row[2]),
                    "under_water": _water(row[4]),
                    "total_company": name,
                }
            )
    return result


# 公司间盘口分歧阈值：同一页各公司即时盘口最大差距达到该值即提示分歧。
SPLIT_WATCH = 0.5

# 单场页对未登录用户打码公司名；已确认的中文名优先，其余抄页面打码名（威***/*门 等）。
# 老产物（7 列、无公司名）靠这张表回退，新产物直接用行内名字。
COMPANY_NAMES = {
    "5": "澳门",
    "293": "威廉",
    "4": "皇冠",
    "6": "伟德",
    "2": "立*",
    "3": "B*****",
    "9": "易**",
    "16": "1****",
    "122": "香***",
    "140": "M*************",
    "280": "*冠",
    "502": "1**********",
    "537": "W**********",
    "651": "利*",
    "863": "1****",
    "1055": "P*********",
    "1484": "o***",
}


def _spread(rows: list[list], converter) -> list[float]:
    """各公司即时盘口，按让球幅度升序；不足两家公司时返回空列表。

    空单元格跳过；「平手」解析成 0.0 是合法盘口，必须参与跨度计算，
    否则「平手 vs 半球」这种 0.5 的真实分歧会被漏判。
    """
    values = []
    for row in _shifted(rows):
        text = _line_text(row[3])
        if text:
            values.append(converter(text))
    return sorted(values, key=abs) if len(values) >= 2 else []


def _company_rows(rows: list[list]) -> list[dict]:
    """各公司的初盘/即时盘口，供悬停弹层展示；保持页面原有公司顺序。"""
    rows = _shifted(rows)
    out = []
    for row in rows:
        init_text = _line_text(row[6])
        cur_text = _line_text(row[3])
        if not init_text and not cur_text:
            continue
        cid = str(row[0])
        name = str(row[1]) if len(row) > 1 and row[1] else COMPANY_NAMES.get(cid, "")
        out.append(
            {
                "cid": cid,
                "name": name,
                "initial": init_text or "—",
                "current": cur_text or "—",
            }
        )
    return out


def _split_block(rows: list[list], label: str, converter) -> dict | None:
    """盘口分歧达到 SPLIT_WATCH 时返回弹层数据块，否则返回 None。

    至少一家公司初盘→即时有变化才挂弹窗：全部公司初盘与即时完全一致时，
    各公司之间即便有差异也只是静态报价，标成「盘口分歧」会误导。
    """
    if not any(_line_text(r[6]) != _line_text(r[3]) for r in _shifted(rows)):
        return None
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
        # 只列真正动了盘的公司，没动的留在页面上反而让人以为弹窗在重复初盘
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
        rows = _shifted(page.get("rows") or [])
        row = _pick_company(rows)
        if not fixture_id or row is None:
            continue
        init_text = _line_text(row[6])
        cur_text = _line_text(row[3])
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
                    "home_water": _water(row[5]),
                    "away_water": _water(row[7]),
                },
                "current": {
                    "line_text": cur_text,
                    "line": cur_line,
                    "line_display": _handicap_display(cur_line),
                    "home_water": _water(row[2]),
                    "away_water": _water(row[4]),
                },
            }
        elif kind == "daxiao":
            slot = result.setdefault(fixture_id, {})
            slot["total"] = {
                "available": True,
                "initial": {
                    "line_text": init_text,
                    "line": total_line_to_float(init_text),
                    "over_water": _water(row[5]),
                    "under_water": _water(row[7]),
                },
                "current": {
                    "line_text": cur_text,
                    "line": total_line_to_float(cur_text),
                    "over_water": _water(row[2]),
                    "under_water": _water(row[4]),
                },
            }

    splits: dict[str, list[str]] = {}
    for key, page in (raw or {}).items():
        fixture_id, _, kind = str(key).partition("|")
        rows = _shifted(page.get("rows") or [])
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
        # 这份盘口来自 500 单场页（澳门/威廉），不是赛前指数
        analysis["source"] = "odds_pages"
        analysis["split"] = splits.get(fixture_id, [])
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
