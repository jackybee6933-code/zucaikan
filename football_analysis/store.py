from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    match_count INTEGER NOT NULL DEFAULT 0,
    attention_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS match_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    issue TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    match_no TEXT NOT NULL DEFAULT '',
    league TEXT NOT NULL DEFAULT '',
    match_time TEXT NOT NULL DEFAULT '',
    home_team TEXT NOT NULL DEFAULT '',
    away_team TEXT NOT NULL DEFAULT '',
    titan_match_id TEXT NOT NULL DEFAULT '',
    attention INTEGER NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT '无数据',
    direction TEXT NOT NULL DEFAULT '',
    lean TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    initial_line TEXT NOT NULL DEFAULT '',
    current_line TEXT NOT NULL DEFAULT '',
    initial_home_water REAL NOT NULL DEFAULT 0,
    initial_away_water REAL NOT NULL DEFAULT 0,
    current_home_water REAL NOT NULL DEFAULT 0,
    current_away_water REAL NOT NULL DEFAULT 0,
    line_change REAL NOT NULL DEFAULT 0,
    asian_rec TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '',
    final_score TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(run_id) REFERENCES issue_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_issue_runs_issue ON issue_runs(issue, captured_at);
CREATE INDEX IF NOT EXISTS idx_movements_issue ON match_movements(issue, captured_at);
CREATE INDEX IF NOT EXISTS idx_movements_team ON match_movements(home_team, away_team);
CREATE INDEX IF NOT EXISTS idx_movements_level ON match_movements(level, attention);
"""

_LEVEL_ORDER = {"重点关注": 3, "关注": 2, "稳定": 1, "无数据": 0}


def default_db_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root else Path.cwd()
    return Path(base) / "data" / "football_analysis.sqlite"


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_kind(kind: str) -> str:
    return "降盘" if kind == "退盘" else kind


def _movement_kind(reason: str) -> str:
    if "让球盘退盘" in reason or "让球盘降盘" in reason:
        return "降盘"
    if "让球盘升盘" in reason:
        return "升盘"
    if "亚盘水位反转" in reason:
        return "水位反转"
    if "亚盘水位摆动" in reason:
        return "水位摆动"
    return "关注" if reason else ""


def _asian_rec(item: dict) -> str:
    rec = ((item.get("odds_analysis") or {}).get("asian_handicap") or {})
    if not rec.get("available"):
        return "无市场"
    action = rec.get("action") or "观望"
    value = rec.get("recommended_side") or rec.get("final_direction") or "主队赢盘"
    if action == "观望":
        return f"{value}?"
    return f"{value}（{action}）"


def _line_text(side: dict) -> str:
    line = str(side.get("line_display") or side.get("line_text") or "").strip()
    home_water = _safe_float(side.get("home_water"))
    away_water = _safe_float(side.get("away_water"))
    if home_water or away_water:
        return f"{line} {home_water:.2f}/{away_water:.2f}".strip()
    return line


def save_issue_movements(
    db_path: str | Path,
    analyses: list[dict],
    issue: str,
    source_url: str = "",
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    captured = (captured_at or datetime.now()).isoformat(timespec="seconds")
    attention_count = sum(
        1 for item in analyses if (item.get("odds_movement") or {}).get("attention")
    )
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO issue_runs (issue, captured_at, source_url, match_count, attention_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (issue, captured, source_url, len(analyses), attention_count),
        )
        run_id = int(cur.lastrowid)
        rows = []
        for item in analyses:
            movement = item.get("odds_movement") or {}
            asian = movement.get("asian") or {}
            initial = asian.get("initial") or {}
            current = asian.get("current") or {}
            reason = "；".join(str(part) for part in movement.get("reasons") or [])
            kind = _normalize_kind(str(movement.get("kind") or "") or _movement_kind(reason))
            snapshot = {k: v for k, v in item.items() if k not in ("analysis_url",)}
            rows.append(
                (
                    run_id,
                    issue,
                    captured,
                    str(item.get("match_no") or ""),
                    str(item.get("league") or ""),
                    str(item.get("match_time") or ""),
                    str(item.get("home_team") or ""),
                    str(item.get("away_team") or ""),
                    str(item.get("titan_match_id") or ""),
                    1 if movement.get("attention") else 0,
                    str(movement.get("level") or "无数据"),
                    str(asian.get("direction") or ""),
                    str(asian.get("lean") or ""),
                    kind,
                    reason,
                    _line_text(initial),
                    _line_text(current),
                    _safe_float(initial.get("home_water")),
                    _safe_float(initial.get("away_water")),
                    _safe_float(current.get("home_water")),
                    _safe_float(current.get("away_water")),
                    _safe_float(asian.get("line_change")),
                    _asian_rec(item),
                    json.dumps(snapshot, ensure_ascii=False),
                    str(item.get("final_score") or ""),
                )
            )
        conn.executemany(
            """
            INSERT INTO match_movements (
                run_id, issue, captured_at, match_no, league, match_time,
                home_team, away_team, titan_match_id, attention, level,
                direction, lean, kind, reason, initial_line, current_line,
                initial_home_water, initial_away_water, current_home_water,
                current_away_water, line_change, asian_rec, payload_json, final_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return {
            "db_path": str(Path(db_path)),
            "run_id": run_id,
            "issue": issue,
            "captured_at": captured,
            "match_count": len(analyses),
            "attention_count": attention_count,
        }
    finally:
        conn.close()


def list_issues(db_path: str | Path) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT issue,
                   MAX(captured_at) AS last_captured_at,
                   COUNT(*) AS run_count,
                   MAX(match_count) AS match_count,
                   MAX(attention_count) AS attention_count
            FROM issue_runs
            GROUP BY issue
            ORDER BY last_captured_at DESC, issue DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query_movements(
    db_path: str | Path,
    *,
    issue: str = "",
    team: str = "",
    level: str = "",
    kind: str = "",
    attention_only: bool = False,
    latest_only: bool = True,
    limit: int = 200,
) -> list[dict]:
    conn = connect(db_path)
    try:
        clauses = ["1=1"]
        params: list[object] = []
        if latest_only:
            clauses.append(
                """run_id = (
                    SELECT MAX(run_id) FROM issue_runs
                    WHERE issue_runs.issue = match_movements.issue
                )"""
            )
        if issue:
            clauses.append("issue = ?")
            params.append(issue)
        if team:
            like = f"%{team}%"
            clauses.append("(home_team LIKE ? OR away_team LIKE ? OR league LIKE ?)")
            params.extend([like, like, like])
        if level:
            clauses.append("level = ?")
            params.append(level)
        if attention_only:
            clauses.append("attention = 1")
        if kind:
            kind = _normalize_kind(kind)
            if kind == "降盘":
                clauses.append("kind IN (?, ?)")
                params.extend(["降盘", "退盘"])
            else:
                clauses.append("kind = ?")
                params.append(kind)
        sql = f"""
            SELECT * FROM match_movements
            WHERE {' AND '.join(clauses)}
            ORDER BY issue DESC, captured_at DESC,
                     match_time ASC, CAST(match_no AS INTEGER), home_team ASC, away_team ASC
            LIMIT ?
        """
        params.append(limit)
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["kind"] = _normalize_kind(str(row.get("kind") or ""))
        return rows
    finally:
        conn.close()
def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns for databases created before a column existed."""
    cursor = conn.execute("SELECT name FROM pragma_table_info('match_movements')")
    existing = {row["name"] for row in cursor.fetchall()}
    if "final_score" not in existing:
        conn.execute("ALTER TABLE match_movements ADD COLUMN final_score TEXT NOT NULL DEFAULT ''")
    conn.commit()

def rebuild_analyses_from_db(db_path: str | Path, issue: str) -> list[dict]:
    """Read match_movements rows for issue and rebuild a flat analysis list."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT m.*, r.source_url, r.captured_at
            FROM match_movements m
            JOIN issue_runs r ON r.run_id = m.run_id
            WHERE m.issue = ?
              AND m.run_id = (
                  SELECT MAX(run_id) FROM issue_runs WHERE issue = ?
              )
            ORDER BY CAST(m.match_no AS INTEGER)
            """
            , (issue, issue)
        ).fetchall()
        analyses: list[dict] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            # Old payload (movement-only): synthesize a minimal analysis so query still renders
            if "market" not in payload:
                movement = dict(payload)
                movement["available"] = row["attention"] == 1
                movement["attention"] = row["attention"] == 1
                movement["kind"] = row["kind"]
                movement["reasons"] = [row["reason"]] if row["reason"] else []
                analyses.append({
                    "match_no": row["match_no"],
                    "league": row["league"],
                    "match_time": row["match_time"],
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "final_score": row["final_score"],
                    "titan_match_id": row["titan_match_id"],
                    "odds_movement": movement,
                    "captured_at": row["captured_at"],
                    "source_url": row["source_url"],
                    "_legacy": True,
                })
                continue
            # New full snapshot
            movement = payload.get("odds_movement") or {}
            movement["available"] = row["attention"] == 1
            movement["attention"] = row["attention"] == 1
            movement["kind"] = row["kind"]
            movement["reasons"] = [row["reason"]] if row["reason"] else []
            analysis = dict(payload)
            analysis["odds_movement"] = movement
            analysis["final_score"] = row["final_score"]
            analysis["captured_at"] = row["captured_at"]
            analysis["source_url"] = row["source_url"]
            analyses.append(analysis)
        return analyses
    finally:
        conn.close()

def load_issue_snapshot(db_path: str | Path, issue: str) -> list[dict]:
    """Read the full analysis snapshot for one issue from the DB."""
    return rebuild_analyses_from_db(db_path, issue)
