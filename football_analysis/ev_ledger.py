"""前瞻性对账：把 ev_home - ev_away 排序快照冻结入库，赛后用真实比分对账。

背景（AGENTS.md）：现有 confidence/margin 门禁未证明带来收益，而按
ev_home - ev_away 期望值差排序在 3 种候选里形态最稳（n=120 时 68.3% ±8.3pp）。
但回头看同一批 307 场挑最优必然带乐观偏差，所以改为**只记录不调参**：
每期 analyze 时把排序结果原样冻结，等比分出来后对账，作为前瞻性净基线。

本模块不改任何生产推荐逻辑，只是旁路记录与对账。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ev_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    issue TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    match_no TEXT NOT NULL DEFAULT '',
    league TEXT NOT NULL DEFAULT '',
    home_team TEXT NOT NULL DEFAULT '',
    away_team TEXT NOT NULL DEFAULT '',
    ev_diff REAL NOT NULL DEFAULT 0,
    ev_home REAL,
    ev_away REAL,
    picked_side TEXT NOT NULL DEFAULT '',
    asian_line REAL NOT NULL DEFAULT 0,
    asian_line_text TEXT NOT NULL DEFAULT '',
    final_score TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(run_id) REFERENCES issue_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_ev_ledger_issue ON ev_ledger(issue, captured_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LEDGER_SCHEMA)
    conn.commit()


def _asian_rec(item: dict) -> dict:
    return (item.get("odds_analysis") or {}).get("asian_handicap") or {}


def _ev_diff(rec: dict) -> float | None:
    """ev_home - ev_away；任一缺失或非有限数时返回 None。"""
    try:
        ev_home = float(rec.get("ev_home"))
        ev_away = float(rec.get("ev_away"))
    except (TypeError, ValueError):
        return None
    return ev_home - ev_away


def build_ledger_rows(analyses: list[dict]) -> list[dict]:
    """按 ev 差绝对值降序排出可对账场次；ev 缺失或无亚盘的场次不入选。

    picked_side 取 ev 更大的一侧（绝对差为 0 时不选边，仅记录）。
    """
    rows: list[dict] = []
    for item in analyses:
        rec = _asian_rec(item)
        if not rec.get("available"):
            continue
        diff = _ev_diff(rec)
        if diff is None:
            continue
        market = (item.get("market") or {}).get("asian") or {}
        rows.append(
            {
                "match_no": str(item.get("match_no") or ""),
                "league": str(item.get("league") or ""),
                "home_team": str(item.get("home_team") or ""),
                "away_team": str(item.get("away_team") or ""),
                "ev_diff": diff,
                "ev_home": float(rec.get("ev_home")),
                "ev_away": float(rec.get("ev_away")),
                "picked_side": "home" if diff > 0 else "away" if diff < 0 else "",
                "asian_line": float(market.get("home_line") or 0),
                "asian_line_text": str(market.get("line_text") or ""),
            }
        )
    rows.sort(key=lambda r: -abs(r["ev_diff"]))
    return rows


def save_ev_ledger(
    db_path: str | Path,
    analyses: list[dict],
    issue: str,
    run_id: int,
    captured_at: str | None = None,
) -> int:
    """把一期排序快照写入 ev_ledger；返回写入条数。已存在同一 run 不重复写。"""
    if not analyses:
        return 0
    captured = captured_at or datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(Path(db_path))
    try:
        ensure_schema(conn)
        exists = conn.execute(
            "SELECT 1 FROM ev_ledger WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone()
        if exists:
            return conn.execute(
                "SELECT COUNT(*) FROM ev_ledger WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        rows = build_ledger_rows(analyses)
        if not rows:
            return 0
        scores = {
            str(a.get("match_no") or ""): str(a.get("final_score") or "")
            for a in analyses
        }
        payload = [
            (
                run_id,
                issue,
                captured,
                r["match_no"],
                r["league"],
                r["home_team"],
                r["away_team"],
                r["ev_diff"],
                r["ev_home"],
                r["ev_away"],
                r["picked_side"],
                r["asian_line"],
                r["asian_line_text"],
                scores.get(r["match_no"], ""),
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO ev_ledger (
                run_id, issue, captured_at, match_no, league, home_team, away_team,
                ev_diff, ev_home, ev_away, picked_side, asian_line, asian_line_text,
                final_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def _score_goals(score: str) -> tuple[int, int] | None:
    if "-" not in str(score or ""):
        return None
    try:
        home, away = (int(part.strip() or "0") for part in str(score).split("-", 1))
    except ValueError:
        return None
    return home, away


def _cover_result(home_goals: int, away_goals: int, line: float, picked_side: str) -> int | None:
    """picked_side 视角的赢盘结果：1 赢、-1 输、0 走水。picked_side 为空时 None。"""
    if not picked_side:
        return None
    # home_line 符号约定：负=主让，正=主受让，统一算 goal_diff + line
    home_handicap = (home_goals - away_goals) + line
    side_value = home_handicap if picked_side == "home" else -home_handicap
    if side_value > 0:
        return 1
    if side_value < 0:
        return -1
    return 0


def reconcile_ledger(
    db_path: str | Path,
    issue: str,
    scores: dict[str, str] | None = None,
) -> dict:
    """对账：用 movement 表的 final_score 回填 ev_ledger 并统计命中率。

    scores 可直接传入（键为 match_no），不传则从 match_movements 取同一期的最终比分。
    走水不计入命中率。返回 {'rows': n, 'settled': n, 'hits': n, 'push': n}。
    """
    conn = sqlite3.connect(Path(db_path))
    try:
        ensure_schema(conn)
        run_id = conn.execute(
            "SELECT MAX(run_id) FROM ev_ledger WHERE issue = ?", (issue,)
        ).fetchone()[0]
        if run_id is None:
            return {"rows": 0, "settled": 0, "hits": 0, "push": 0}

        if scores is None:
            mv_run = conn.execute(
                "SELECT MAX(run_id) FROM issue_runs WHERE issue = ?", (issue,)
            ).fetchone()[0]
            scores = {}
            if mv_run is not None:
                for row in conn.execute(
                    "SELECT match_no, final_score FROM match_movements WHERE run_id = ?",
                    (mv_run,),
                ):
                    if row[1]:
                        scores[str(row[0])] = str(row[1])

        settled = hits = push = 0
        for row in conn.execute(
            "SELECT id, match_no, asian_line, picked_side FROM ev_ledger WHERE run_id = ?",
            (run_id,),
        ):
            ledger_id, match_no, line, picked = row[0], str(row[1]), float(row[2] or 0), row[3]
            score = scores.get(match_no, "")
            goals = _score_goals(score)
            if goals is None:
                continue
            result = _cover_result(goals[0], goals[1], line, picked)
            if result is None:
                continue
            settled += 1
            if result == 1:
                hits += 1
            elif result == 0:
                push += 1
            conn.execute(
                "UPDATE ev_ledger SET final_score = ? WHERE id = ?",
                (score, ledger_id),
            )
        conn.commit()
        return {
            "rows": conn.execute(
                "SELECT COUNT(*) FROM ev_ledger WHERE run_id = ?", (run_id,)
            ).fetchone()[0],
            "settled": settled,
            "hits": hits,
            "push": push,
        }
    finally:
        conn.close()


def ledger_trend(db_path: str | Path) -> list[dict]:
    """跨期对账趋势：按期号升序，每期用 match_movements 最新 final_score 即时对账（走水不计）。"""
    conn = sqlite3.connect(Path(db_path))
    try:
        ensure_schema(conn)
        issues = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT issue FROM ev_ledger ORDER BY issue ASC"
            )
        ]
        out: list[dict] = []
        for issue in issues:
            run_id = conn.execute(
                "SELECT MAX(run_id) FROM ev_ledger WHERE issue = ?", (issue,)
            ).fetchone()[0]
            rows = [
                dict(
                    zip(
                        ("id", "match_no", "home_team", "away_team", "ev_diff", "picked_side", "asian_line", "final_score"),
                        r,
                    )
                )
                for r in conn.execute(
                    """
                    SELECT id, match_no, home_team, away_team, ev_diff,
                           picked_side, asian_line, final_score
                    FROM ev_ledger WHERE run_id = ?
                    ORDER BY ABS(ev_diff) DESC, match_no ASC
                    """,
                    (run_id,),
                )
            ]
            # 与 reconcile_ledger(scores=None) 口径一致：直接读 match_movements
            # 该期最新 run 的 final_score，不依赖事先写回 ev_ledger 的动作。
            # 纯测试库里没有 issue_runs/match_movements 表时回退读 ev_ledger 自带列。
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", ("table",))}
            scores: dict[str, str] = {}
            if "issue_runs" in tables and "match_movements" in tables:
                mv_run = conn.execute(
                    "SELECT MAX(run_id) FROM issue_runs WHERE issue = ?", (issue,)
                ).fetchone()[0]
                if mv_run is not None:
                    for m_no, score in conn.execute(
                        "SELECT match_no, final_score FROM match_movements WHERE run_id = ?",
                        (mv_run,),
                    ):
                        if score:
                            scores[str(m_no)] = str(score)
            if not scores:
                for r in rows:
                    scores.setdefault(r["match_no"], r.get("final_score", ""))

            settled = hits = push = 0
            for r in rows:
                goals = _score_goals(scores.get(r["match_no"], ""))
                if goals is None:
                    continue
                result = _cover_result(goals[0], goals[1], r["asian_line"], r["picked_side"])
                if result is None:
                    continue
                settled += 1
                hits += result == 1
                push += result == 0
            out.append(
                {
                    "issue": issue,
                    "rows": len(rows),
                    "settled": settled,
                    "hits": hits,
                    "push": push,
                }
            )
        return out
    finally:
        conn.close()


def top_picks(db_path: str | Path, issue: str, limit: int = 5) -> list[dict]:
    """取一期排序最高的若干条，供 CLI 展示。"""
    conn = sqlite3.connect(Path(db_path))
    try:
        ensure_schema(conn)
        run_id = conn.execute(
            "SELECT MAX(run_id) FROM ev_ledger WHERE issue = ?", (issue,)
        ).fetchone()[0]
        if run_id is None:
            return []
        rows = conn.execute(
            """
            SELECT match_no, league, home_team, away_team, ev_diff, ev_home, ev_away,
                   picked_side, asian_line, asian_line_text, final_score
            FROM ev_ledger WHERE run_id = ?
            ORDER BY ABS(ev_diff) DESC, match_no ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        keys = (
            "match_no", "league", "home_team", "away_team", "ev_diff", "ev_home",
            "ev_away", "picked_side", "asian_line", "asian_line_text", "final_score",
        )
        return [dict(zip(keys, r)) for r in rows]
    finally:
        conn.close()
