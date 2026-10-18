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
    league_color TEXT NOT NULL DEFAULT '',
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
    team_read TEXT NOT NULL DEFAULT '',
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


def _extract_split(payload_json: str) -> list[dict]:
    """从 payload_json 还原公司间盘口分歧的弹层数据块。"""
    try:
        movement = (json.loads(payload_json or "{}") or {}).get("odds_movement") or {}
    except (TypeError, ValueError):
        return []
    split = movement.get("split")
    if not isinstance(split, list):
        return []
    return [item for item in split if isinstance(item, dict)]


def _movement_kind(reason: str) -> str:
    if "让球盘退盘" in reason or "让球盘降盘" in reason:
        return "降盘"
    if "让球盘升盘" in reason:
        return "升盘"
    if "亚盘水位反转" in reason:
        return "水位反转"
    if "亚盘水位摆动" in reason:
        return "水位摆动"
    if "大小盘降" in reason or "大盘降" in reason:
        return "大小盘降"
    if "大小盘升" in reason or "大盘升" in reason:
        return "大小盘升"
    if "大小盘水位摆动" in reason:
        return "大水位摆动"
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


def _load_prev_splits(conn: sqlite3.Connection, issue: str, run_id: int) -> dict[str, list[dict]]:
    """取上一 run 的公司盘口分歧快照，供新 run 继承。

    split 只在「刷新盘口分歧」时由单场页产物生成；普通抓取走网络不读产物，
    新 run 会缺失 split，而查询页只展示最新 run——不继承的话盘口分歧会消失。
    """
    prev = conn.execute(
        """
        SELECT m.match_no, m.payload_json
        FROM match_movements m
        WHERE m.issue = ? AND m.run_id < ?
          AND m.payload_json LIKE '%split%'
        ORDER BY m.run_id DESC
        """,
        (issue, run_id),
    ).fetchall()
    splits: dict[str, list[dict]] = {}
    for match_no, payload_json in prev:
        if match_no in splits:
            continue
        movement = (json.loads(payload_json or "{}") or {}).get("odds_movement") or {}
        block = movement.get("split")
        if isinstance(block, list) and block:
            splits[match_no] = block
    return splits


def _load_prev_team_reads(conn, issue, run_id):
    """取上一 run 的 team_read 快照，供新 run 继承。

    team_read 由「AI 分析」按钮单独生成并写入所有 run；普通抓取/回填不重新
    生成，新 run 会缺 team_read。继承避免查询页只看最新 run 时倾向栏被清空。
    """
    prev = conn.execute(
        """
        SELECT match_no, team_read
        FROM match_movements
        WHERE issue = ? AND run_id < ? AND team_read != ''
        ORDER BY run_id DESC
        """,
        (issue, run_id),
    ).fetchall()
    reads = {}
    for match_no, payload in prev:
        if match_no in reads:
            continue
        try:
            reads[match_no] = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
    return reads


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
        # split（公司间盘口分歧）来自单场页产物，只在「刷新盘口分歧」时生成；
        # 普通抓取走网络不读产物，新 run 会没有 split。继承上一 run 的 split，
        # 避免查询页只看最新 run 时盘口分歧「消失」。
        prev_splits = _load_prev_splits(conn, issue, run_id)
        prev_team_reads = _load_prev_team_reads(conn, issue, run_id)
        rows = []
        for item in analyses:
            movement = item.get("odds_movement") or {}
            if prev_splits and not movement.get("split"):
                inherited = prev_splits.get(str(item.get("match_no") or ""))
                if inherited:
                    movement["split"] = inherited
            if prev_team_reads and not item.get("team_read"):
                inherited_read = prev_team_reads.get(str(item.get("match_no") or ""))
                if inherited_read:
                    item["team_read"] = inherited_read
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
                    str(item.get("league_color") or ""),
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
                    (
                        json.dumps(item.get("team_read"), ensure_ascii=False)
                        if item.get("team_read")
                        else ""
                    ),
                )
            )
        conn.executemany(
            """
            INSERT INTO match_movements (
                run_id, issue, captured_at, match_no, league, league_color, match_time,
                home_team, away_team, titan_match_id, attention, level,
                direction, lean, kind, reason, initial_line, current_line,
                initial_home_water, initial_away_water, current_home_water,
                current_away_water, line_change, asian_rec, payload_json, final_score,
                team_read
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def list_leagues(db_path: str | Path, issue: str = "") -> list[str]:
    """League names for the filter dropdown; limited to one issue when given."""
    conn = connect(db_path)
    try:
        if issue:
            rows = conn.execute(
                "SELECT DISTINCT league FROM match_movements WHERE issue = ? AND league <> '' ORDER BY league",
                (issue,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT league FROM match_movements WHERE league <> '' ORDER BY league"
            ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def latest_captured_at(db_path: str | Path, issue: str) -> str | None:
    """Return the most recent captured_at for an issue, or None if it has no runs."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(captured_at) FROM issue_runs WHERE issue = ?",
            (issue,),
        ).fetchone()
        return row[0] if row and row[0] else None
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
            row["split"] = _extract_split(str(row.get("payload_json") or ""))
        return rows
    finally:
        conn.close()
def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns for databases created before a column existed."""
    cursor = conn.execute("SELECT name FROM pragma_table_info('match_movements')")
    existing = {row["name"] for row in cursor.fetchall()}
    if "final_score" not in existing:
        conn.execute("ALTER TABLE match_movements ADD COLUMN final_score TEXT NOT NULL DEFAULT ''")
    if "league_color" not in existing:
        conn.execute("ALTER TABLE match_movements ADD COLUMN league_color TEXT NOT NULL DEFAULT ''")
    if "team_read" not in existing:
        conn.execute("ALTER TABLE match_movements ADD COLUMN team_read TEXT NOT NULL DEFAULT ''")
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
                    "league_color": row["league_color"],
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
            analysis["league_color"] = row["league_color"]
            analyses.append(analysis)
        return analyses
    finally:
        conn.close()

def load_issue_snapshot(db_path: str | Path, issue: str) -> list[dict]:
    """Read the full analysis snapshot for one issue from the DB."""
    return rebuild_analyses_from_db(db_path, issue)
def update_match_scores(
    db_path: str | Path,
    issue: str,
    scores: dict[str, str],
) -> int:
    """Write final scores keyed by match_no into every run of an issue that lacks them.

    比分是事实不随 run 漂移：同一期若已抓取多个 run（「抓取并入库」可重复点击），
    只写最新 run 会让旧 run 的复盘统计口径失效；统一回填所有 run，
    让「复盘」的命中率统计在任一下一次「抓取并入库」后仍可复现。

    用新取到的非空比分覆盖旧值，而非只回填空值：门禁修复（2026-10-07）前，
    赛前缓存的详情页会把未开赛比赛写成占位 0-0，这些假 0-0 非空，只回填空值
    时赛后复盘拿到真比分也覆盖不进去（26135 期曾因此卡住 156 行假 0-0）。"""
    conn = connect(db_path)
    try:
        updated = 0
        for match_no, score in scores.items():
            cur = conn.execute(
                """
                UPDATE match_movements SET final_score = ?
                WHERE issue = ? AND match_no = ?
                """,
                (score, issue, match_no),
            )
            updated += cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()


def update_team_reads(
    db_path: str | Path,
    issue: str,
    team_reads: dict[str, dict],
) -> int:
    """Write team-read (LLM tendency) per match_no into every run of an issue.

    Display-only info, overwritten by the newest AI analyze pass; same scope
    as final scores: written to all runs so the query page (latest run) and
    review stats always see the same tendency.
    """
    conn = connect(db_path)
    try:
        updated = 0
        for match_no, payload in team_reads.items():
            cur = conn.execute(
                """
                UPDATE match_movements SET team_read = ?
                WHERE issue = ? AND match_no = ?
                """,
                (json.dumps(payload, ensure_ascii=False), issue, match_no),
            )
            updated += cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()

