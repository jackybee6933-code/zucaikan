"""投注：本地注单、赛前编辑/删除、赛后派生结算。"""
from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .settlement import settle_asian, settle_total
from .store import connect, query_movements, rebuild_analyses_from_db


SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue TEXT NOT NULL,
    match_no TEXT NOT NULL,
    source_run_id INTEGER NOT NULL DEFAULT 0,
    league TEXT NOT NULL DEFAULT '',
    match_time TEXT NOT NULL DEFAULT '',
    home_team TEXT NOT NULL DEFAULT '',
    away_team TEXT NOT NULL DEFAULT '',
    kickoff_at TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    line REAL NOT NULL DEFAULT 0,
    line_text TEXT NOT NULL DEFAULT '',
    water REAL NOT NULL DEFAULT 0,
    odds REAL NOT NULL DEFAULT 0,
    stake REAL NOT NULL,
    rec_action TEXT NOT NULL DEFAULT '',
    rec_side TEXT NOT NULL DEFAULT '',
    rec_confidence REAL NOT NULL DEFAULT 0,
    rec_margin REAL NOT NULL DEFAULT 0,
    follows_recommendation INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_bets_unique
    ON manual_bets(issue, match_no, market);
CREATE INDEX IF NOT EXISTS idx_manual_bets_issue ON manual_bets(issue, match_no);
"""

MARKET_LABELS = {"asian": "亚盘", "total": "大小球"}
SELECTION_LABELS = {
    "asian": {"home": "主队赢盘", "away": "客队赢盘"},
    "total": {"over": "大球", "under": "小球"},
}
RESULT_LABELS = {1.0: "赢", 0.5: "赢半", 0.0: "走", -0.5: "输半", -1.0: "输"}


class BetError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_schema(conn)
    return conn


def parse_kickoff(issue: str, match_time: str, captured_at: str = "") -> datetime | None:
    """把 10-18 23:30 这类比赛时间补全年份，返回本地无时区时间。"""
    text = str(match_time or "").strip()
    matched = re.search(
        r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})",
        text,
    )
    if not matched:
        return None
    year_text, month, day, hour, minute = matched.groups()
    if year_text:
        year = int(year_text)
    else:
        digits = re.sub(r"\D", "", str(issue or ""))
        if len(digits) >= 2:
            year = 2000 + int(digits[:2])
        elif captured_at:
            try:
                year = datetime.fromisoformat(captured_at).year
            except ValueError:
                year = datetime.now().year
        else:
            year = datetime.now().year
    try:
        return datetime(year, int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _stake(value: object) -> float:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        raise BetError("积分必须是数字") from None
    if not math.isfinite(amount) or amount <= 0:
        raise BetError("积分必须大于 0")
    if amount > 1_000_000:
        raise BetError("积分不能超过 1,000,000")
    return amount


def _parse_score(score: str) -> tuple[int, int] | None:
    if "-" not in str(score or ""):
        return None
    try:
        return tuple(int(part.strip() or "0") for part in str(score).split("-", 1))  # type: ignore[return-value]
    except ValueError:
        return None


def _recommendation(rec: dict, side_map: dict[str, str]) -> dict:
    action = str(rec.get("action") or "")
    side_text = str(rec.get("recommended_side") or "")
    side = side_map.get(side_text, "")
    return {
        "action": action,
        "side": side,
        "side_text": side_text,
        "default": side if action in ("主推", "轻仓") else "",
        "confidence": _num(rec.get("confidence")),
        "margin": _num(rec.get("margin")),
    }


def market_options(match: dict) -> dict[str, dict]:
    """从最新 analysis 提取可下注的亚盘/大小球市场。"""
    market = match.get("market") or {}
    odds = match.get("odds_analysis") or {}
    out: dict[str, dict] = {}

    if market.get("has_asian"):
        asian = market.get("asian") or {}
        rec = odds.get("asian_handicap") or {}
        home_water = _num(asian.get("home_water"))
        away_water = _num(asian.get("away_water"))
        if rec.get("available") and (home_water > 0 or away_water > 0):
            line = _num(asian.get("home_line"))
            line_text = str(asian.get("line_text") or "")
            out["asian"] = {
                "market": "asian",
                "label": MARKET_LABELS["asian"],
                "line": line,
                "line_text": line_text,
                "source": str(asian.get("source") or ""),
                "selections": [
                    {
                        "value": "home",
                        "label": SELECTION_LABELS["asian"]["home"],
                        "line": line,
                        "line_text": line_text,
                        "water": home_water,
                        "odds": round(1 + home_water, 4),
                    },
                    {
                        "value": "away",
                        "label": SELECTION_LABELS["asian"]["away"],
                        "line": line,
                        "line_text": line_text,
                        "water": away_water,
                        "odds": round(1 + away_water, 4),
                    },
                ],
                "recommendation": _recommendation(
                    rec, {"主队赢盘": "home", "客队赢盘": "away"}
                ),
            }

    if market.get("has_total"):
        total = market.get("total") or {}
        rec = odds.get("total_goals") or {}
        over_water = _num(total.get("over_water"))
        under_water = _num(total.get("under_water"))
        line = _num(total.get("line"))
        if rec.get("available") and line > 0 and (over_water > 0 or under_water > 0):
            line_text = str(total.get("line_text") or f"{line:g}")
            side_map = {"大球": "over", "小球": "under", "大": "over", "小": "under"}
            out["total"] = {
                "market": "total",
                "label": MARKET_LABELS["total"],
                "line": line,
                "line_text": line_text,
                "source": str(total.get("source") or ""),
                "selections": [
                    {
                        "value": "over",
                        "label": SELECTION_LABELS["total"]["over"],
                        "line": line,
                        "line_text": line_text,
                        "water": over_water,
                        "odds": round(1 + over_water, 4),
                    },
                    {
                        "value": "under",
                        "label": SELECTION_LABELS["total"]["under"],
                        "line": line,
                        "line_text": line_text,
                        "water": under_water,
                        "odds": round(1 + under_water, 4),
                    },
                ],
                "recommendation": _recommendation(rec, side_map),
            }
    return out


def _find_match(db_path: str | Path, issue: str, match_no: str) -> dict | None:
    for item in rebuild_analyses_from_db(Path(db_path), issue):
        if str(item.get("match_no") or "") == str(match_no):
            return item
    return None


def _latest_matches(db_path: str | Path, issue: str) -> dict[str, dict]:
    rows = query_movements(
        Path(db_path), issue=issue, latest_only=True, limit=100
    )
    return {str(row.get("match_no") or ""): row for row in rows}


def _latest_run_id(conn: sqlite3.Connection, issue: str) -> int:
    row = conn.execute(
        "SELECT MAX(run_id) FROM issue_runs WHERE issue = ?", (issue,)
    ).fetchone()
    return int(row[0] or 0)


def _get_bet(conn: sqlite3.Connection, bet_id: int) -> dict:
    row = conn.execute("SELECT * FROM manual_bets WHERE id = ?", (bet_id,)).fetchone()
    if row is None:
        raise BetError("注单不存在", 404)
    return dict(row)


def _is_locked(match: dict | None, bet: dict | None, now: datetime) -> tuple[bool, str]:
    final_score = str((match or {}).get("final_score") or "")
    stored = None
    if bet and bet.get("kickoff_at"):
        try:
            stored = datetime.fromisoformat(str(bet["kickoff_at"]))
        except ValueError:
            stored = None
    kickoff = None
    if match:
        kickoff = parse_kickoff(
            str(match.get("issue") or (bet or {}).get("issue") or ""),
            str(match.get("match_time") or ""),
            str(match.get("captured_at") or ""),
        )
    kickoff = kickoff or stored
    if final_score:
        return True, "已结算"
    if kickoff is None:
        return True, "开赛时间未知"
    if now >= kickoff:
        return True, "已开赛"
    return False, ""


def _settle(bet: dict, final_score: str) -> tuple[str, float]:
    goals = _parse_score(final_score)
    if goals is None:
        return "", 0.0
    if bet["market"] == "asian":
        result = settle_asian(
            goals[0], goals[1], _num(bet.get("line")), str(bet.get("selection") or "")
        )
    else:
        result = settle_total(
            goals[0] + goals[1],
            _num(bet.get("line")),
            str(bet.get("selection") or "") == "over",
        )
    if result is None:
        return "", 0.0
    stake = _num(bet.get("stake"))
    water = _num(bet.get("water"))
    if result > 0:
        profit = result * stake * water
    elif result < 0:
        profit = result * stake
    else:
        profit = 0.0
    return RESULT_LABELS.get(result, ""), round(profit, 2)


def _bet_view(row: dict, match: dict | None, now: datetime) -> dict:
    locked, locked_reason = _is_locked(match, row, now)
    final_score = str((match or {}).get("final_score") or "")
    result_label, profit = _settle(row, final_score)
    if final_score and result_label:
        status = "settled"
        status_label = "已结算"
    elif locked:
        status = "waiting"
        status_label = "待结算"
    else:
        status = "pending"
        status_label = "待开赛"
    market = str(row.get("market") or "")
    selection = str(row.get("selection") or "")
    rec_side = str(row.get("rec_side") or "")
    return {
        **row,
        "market_label": MARKET_LABELS.get(market, market),
        "selection_label": SELECTION_LABELS.get(market, {}).get(selection, selection),
        "rec_side_label": SELECTION_LABELS.get(market, {}).get(rec_side, rec_side),
        "status": status,
        "status_label": status_label,
        "result_label": result_label or status_label,
        "profit": profit,
        "final_score": final_score,
        "locked_reason": locked_reason,
        "can_edit": not locked,
        "can_delete": not locked,
    }


def list_bets(
    db_path: str | Path,
    *,
    issue: str = "",
    now: datetime | None = None,
    limit: int = 200,
) -> list[dict]:
    now = now or datetime.now()
    conn = _connect(db_path)
    try:
        if issue:
            rows = conn.execute(
                "SELECT * FROM manual_bets WHERE issue = ? ORDER BY CAST(match_no AS INTEGER), market, id",
                (issue,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manual_bets ORDER BY issue DESC, CAST(match_no AS INTEGER), market, id LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    bets = [dict(row) for row in rows]
    matches_by_issue: dict[str, dict[str, dict]] = {}
    for item in bets:
        issue_value = str(item.get("issue") or "")
        if issue_value not in matches_by_issue:
            matches_by_issue[issue_value] = _latest_matches(db_path, issue_value)
    return [
        _bet_view(item, matches_by_issue.get(str(item.get("issue") or ""), {}).get(
            str(item.get("match_no") or "")
        ), now)
        for item in bets
    ]


def _subset_stats(bets: list[dict]) -> dict:
    settled = [bet for bet in bets if bet.get("status") == "settled"]
    settled_stake = sum(float(bet.get("stake") or 0) for bet in settled)
    profit = sum(float(bet.get("profit") or 0) for bet in settled)
    hits = sum(bet.get("result_label") in ("赢", "赢半") for bet in settled)
    pushes = sum(bet.get("result_label") == "走" for bet in settled)
    decided = sum(
        bet.get("result_label") in ("赢", "赢半", "输", "输半") for bet in settled
    )
    return {
        "bets": len(bets),
        "settled": len(settled),
        "pending": len(bets) - len(settled),
        "stake": round(sum(float(bet.get("stake") or 0) for bet in bets), 2),
        "settled_stake": round(settled_stake, 2),
        "profit": round(profit, 2),
        "roi": round(profit / settled_stake * 100, 1) if settled_stake else 0.0,
        "hits": hits,
        "decided": decided,
        "pushes": pushes,
        "hit_rate": round(hits / decided * 100, 1) if decided else 0.0,
        "push_rate": round(pushes / len(settled) * 100, 1) if settled else 0.0,
    }


def summarize_bets(bets: list[dict]) -> dict:
    """按市场、是否跟随模型汇总当前注单列表。"""
    stats = _subset_stats(bets)
    stats["markets"] = {
        market: _subset_stats([bet for bet in bets if bet.get("market") == market])
        for market in ("asian", "total")
    }
    stats["follows"] = {
        "follow": _subset_stats(
            [bet for bet in bets if int(bet.get("follows_recommendation") or 0)]
        ),
        "deviate": _subset_stats(
            [
                bet for bet in bets
                if not int(bet.get("follows_recommendation") or 0)
                and bet.get("rec_action") in ("主推", "轻仓")
            ]
        ),
        "none": _subset_stats(
            [
                bet for bet in bets
                if bet.get("rec_action") not in ("主推", "轻仓")
            ]
        ),
    }
    return stats


def bet_trend(
    db_path: str | Path,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """按期号升序返回投注统计。"""
    grouped: dict[str, list[dict]] = {}
    for bet in list_bets(db_path, now=now, limit=10000):
        grouped.setdefault(str(bet.get("issue") or ""), []).append(bet)
    return [
        {"issue": issue, **summarize_bets(grouped[issue])}
        for issue in sorted(grouped)
    ]


def export_bets_csv(
    db_path: str | Path,
    *,
    issue: str = "",
    now: datetime | None = None,
) -> str:
    """导出注单 CSV，返回可带 BOM 写入文件的文本。"""
    bets = list_bets(db_path, issue=issue, now=now, limit=10000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "期号", "场次", "联赛", "开赛", "主队", "客队", "市场", "方向",
        "盘口", "水位", "积分", "模型动作", "模型方向", "是否跟随",
        "状态", "结果", "收益", "最终比分", "创建时间", "更新时间",
    ])
    for bet in bets:
        if int(bet.get("follows_recommendation") or 0):
            follow = "跟随"
        elif bet.get("rec_action") in ("主推", "轻仓"):
            follow = "偏离"
        else:
            follow = "无推荐"
        writer.writerow([
            bet.get("issue", ""),
            bet.get("match_no", ""),
            bet.get("league", ""),
            bet.get("match_time", ""),
            bet.get("home_team", ""),
            bet.get("away_team", ""),
            bet.get("market_label", ""),
            bet.get("selection_label", ""),
            bet.get("line_text", ""),
            bet.get("water", 0),
            f"{float(bet.get('stake') or 0):.2f}",
            bet.get("rec_action", ""),
            bet.get("rec_side_label", ""),
            follow,
            bet.get("status_label", ""),
            bet.get("result_label", ""),
            f"{float(bet.get('profit') or 0):.2f}",
            bet.get("final_score", ""),
            bet.get("created_at", ""),
            bet.get("updated_at", ""),
        ])
    return output.getvalue()


def bet_options(
    db_path: str | Path,
    issue: str,
    match_no: str,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    match = _find_match(db_path, issue, match_no)
    if match is None:
        raise BetError("找不到该期比赛", 404)
    kickoff = parse_kickoff(
        issue,
        str(match.get("match_time") or ""),
        str(match.get("captured_at") or ""),
    )
    locked = False
    locked_reason = ""
    if match.get("final_score"):
        locked, locked_reason = True, "已结算"
    elif kickoff is None:
        locked, locked_reason = True, "开赛时间未知"
    elif now >= kickoff:
        locked, locked_reason = True, "已开赛"
    existing = [
        item
        for item in list_bets(db_path, issue=issue, now=now)
        if str(item.get("match_no") or "") == str(match_no)
    ]
    return {
        "issue": issue,
        "match_no": str(match_no),
        "match": {
            "league": str(match.get("league") or ""),
            "match_time": str(match.get("match_time") or ""),
            "home_team": str(match.get("home_team") or ""),
            "away_team": str(match.get("away_team") or ""),
            "final_score": str(match.get("final_score") or ""),
        },
        "kickoff_at": kickoff.isoformat(timespec="minutes") if kickoff else "",
        "locked": locked,
        "locked_reason": locked_reason,
        "markets": market_options(match),
        "existing": existing,
    }


def create_bet(
    db_path: str | Path,
    issue: str,
    match_no: str,
    market: str,
    selection: str,
    stake: object,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    options = bet_options(db_path, issue, match_no, now=now)
    if options["locked"]:
        raise BetError(options["locked_reason"], 409)
    market = str(market or "")
    snapshot = options["markets"].get(market)
    if snapshot is None:
        raise BetError("该市场当前没有可用盘口")
    selection = str(selection or "")
    choice = next(
        (item for item in snapshot["selections"] if item["value"] == selection), None
    )
    if choice is None:
        raise BetError("投注方向无效")
    if any(item.get("market") == market for item in options["existing"]):
        raise BetError("该市场已有注单，请修改或删除", 409)
    amount = _stake(stake)
    rec = snapshot["recommendation"]
    follows = int(rec["action"] in ("主推", "轻仓") and rec["side"] == selection)
    match = options["match"]
    created = now.isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        source_run_id = _latest_run_id(conn, issue)
        cur = conn.execute(
            """
            INSERT INTO manual_bets (
                issue, match_no, source_run_id, league, match_time, home_team,
                away_team, kickoff_at, market, selection, line, line_text, water,
                odds, stake, rec_action, rec_side, rec_confidence, rec_margin,
                follows_recommendation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue,
                str(match_no),
                source_run_id,
                str(match.get("league") or ""),
                str(match.get("match_time") or ""),
                str(match.get("home_team") or ""),
                str(match.get("away_team") or ""),
                options["kickoff_at"],
                market,
                selection,
                _num(choice.get("line")),
                str(choice.get("line_text") or ""),
                _num(choice.get("water")),
                _num(choice.get("odds")),
                amount,
                str(rec.get("action") or ""),
                str(rec.get("side") or ""),
                _num(rec.get("confidence")),
                _num(rec.get("margin")),
                follows,
                created,
                created,
            ),
        )
        conn.commit()
        row = _get_bet(conn, int(cur.lastrowid))
    except sqlite3.IntegrityError:
        raise BetError("该市场已有注单，请修改或删除", 409) from None
    finally:
        conn.close()
    return _bet_view(row, options.get("match"), now)


def update_bet(
    db_path: str | Path,
    bet_id: int,
    *,
    selection: str | None = None,
    stake: object | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    conn = _connect(db_path)
    try:
        existing = _get_bet(conn, int(bet_id))
    finally:
        conn.close()
    options = bet_options(db_path, str(existing["issue"]), str(existing["match_no"]), now=now)
    if options["locked"]:
        raise BetError(options["locked_reason"], 409)
    new_selection = str(selection or existing["selection"])
    amount = _stake(stake if stake is not None else existing["stake"])
    fields = ["stake = ?", "updated_at = ?"]
    params: list[object] = [amount, now.isoformat(timespec="seconds")]
    if new_selection != existing["selection"]:
        snapshot = options["markets"].get(str(existing["market"]))
        if snapshot is None:
            raise BetError("该市场当前没有可用盘口")
        choice = next(
            (item for item in snapshot["selections"] if item["value"] == new_selection),
            None,
        )
        if choice is None:
            raise BetError("投注方向无效")
        rec = snapshot["recommendation"]
        fields.extend(
            [
                "selection = ?",
                "line = ?",
                "line_text = ?",
                "water = ?",
                "odds = ?",
                "rec_action = ?",
                "rec_side = ?",
                "rec_confidence = ?",
                "rec_margin = ?",
                "follows_recommendation = ?",
            ]
        )
        params.extend(
            [
                new_selection,
                _num(choice.get("line")),
                str(choice.get("line_text") or ""),
                _num(choice.get("water")),
                _num(choice.get("odds")),
                str(rec.get("action") or ""),
                str(rec.get("side") or ""),
                _num(rec.get("confidence")),
                _num(rec.get("margin")),
                int(rec["action"] in ("主推", "轻仓") and rec["side"] == new_selection),
            ]
        )
    conn = _connect(db_path)
    try:
        conn.execute(
            f"UPDATE manual_bets SET {', '.join(fields)} WHERE id = ?",
            (*params, int(bet_id)),
        )
        conn.commit()
        row = _get_bet(conn, int(bet_id))
    finally:
        conn.close()
    return _bet_view(row, options.get("match"), now)


def delete_bet(
    db_path: str | Path,
    bet_id: int,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    conn = _connect(db_path)
    try:
        existing = _get_bet(conn, int(bet_id))
    finally:
        conn.close()
    options = bet_options(db_path, str(existing["issue"]), str(existing["match_no"]), now=now)
    if options["locked"]:
        raise BetError(options["locked_reason"], 409)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM manual_bets WHERE id = ?", (int(bet_id),))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": int(bet_id)}
