"""Portable backup and restore for the local application state."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


BACKUP_VERSION = 1
DB_NAME = "data/football_analysis.sqlite"
SETTINGS_NAME = "data/llm_settings.json"
MANIFEST_NAME = "manifest.json"


def _check_database(path: Path) -> None:
    if not path.is_file():
        return
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    if not result or result[0] != "ok":
        raise ValueError(f"SQLite integrity check failed: {result[0] if result else 'unknown'}")


def _sqlite_snapshot(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    _check_database(target)


def build_backup_bytes(
    db_path: str | Path,
    settings_path: str | Path | None = None,
) -> bytes:
    """Build a validated zip archive in memory."""
    db = Path(db_path)
    settings = Path(settings_path) if settings_path else db.parent / "llm_settings.json"
    with tempfile.TemporaryDirectory(prefix="football-backup-") as tmp:
        snapshot = Path(tmp) / "football_analysis.sqlite"
        _sqlite_snapshot(db, snapshot)
        manifest = {
            "format": "football-analysis-backup",
            "version": BACKUP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "database": snapshot.is_file(),
            "settings": settings.is_file(),
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            if snapshot.is_file():
                archive.write(snapshot, DB_NAME)
            if settings.is_file():
                archive.write(settings, SETTINGS_NAME)
        return output.getvalue()


def write_backup(
    db_path: str | Path,
    output_path: str | Path,
    settings_path: str | Path | None = None,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_backup_bytes(db_path, settings_path))
    return target


def _read_archive(data: bytes) -> tuple[dict, bytes | None, bytes | None]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid backup archive") from exc
    with archive:
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("backup manifest is missing or invalid") from exc
        if (
            manifest.get("format") != "football-analysis-backup"
            or int(manifest.get("version", 0)) != BACKUP_VERSION
        ):
            raise ValueError("unsupported backup format")
        db = archive.read(DB_NAME) if DB_NAME in archive.namelist() else None
        settings = archive.read(SETTINGS_NAME) if SETTINGS_NAME in archive.namelist() else None
        if manifest.get("database") and not db:
            raise ValueError("backup database is missing")
        return manifest, db, settings


def restore_backup(
    archive_path: str | Path,
    db_path: str | Path,
    settings_path: str | Path | None = None,
    safety_dir: str | Path | None = None,
) -> Path | None:
    data = Path(archive_path).read_bytes()
    return restore_backup_bytes(data, db_path, settings_path, safety_dir)


def restore_backup_bytes(
    data: bytes,
    db_path: str | Path,
    settings_path: str | Path | None = None,
    safety_dir: str | Path | None = None,
) -> Path | None:
    _, db_bytes, settings_bytes = _read_archive(data)
    target_db = Path(db_path)
    target_settings = Path(settings_path) if settings_path else target_db.parent / "llm_settings.json"
    safety_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="football-restore-") as tmp:
        tmp_dir = Path(tmp)
        incoming_db = tmp_dir / "football_analysis.sqlite"
        if db_bytes:
            incoming_db.write_bytes(db_bytes)
            _check_database(incoming_db)
        if target_db.is_file() or target_settings.is_file():
            destination = Path(safety_dir) if safety_dir else target_db.parent / "backups"
            destination.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety_path = write_backup(
                target_db,
                destination / f"backup_before_restore_{stamp}.zip",
                target_settings,
            )
        target_db.parent.mkdir(parents=True, exist_ok=True)
        if incoming_db.is_file():
            os.replace(incoming_db, target_db)
        elif target_db.exists():
            target_db.unlink()
        if settings_bytes is not None:
            target_settings.parent.mkdir(parents=True, exist_ok=True)
            target_settings.write_bytes(settings_bytes)
        elif target_settings.exists():
            target_settings.unlink()
    return safety_path


def backup_summary(path: str | Path) -> dict:
    manifest, db_bytes, settings_bytes = _read_archive(Path(path).read_bytes())
    return {
        "created_at": manifest.get("created_at", ""),
        "database": bool(db_bytes),
        "settings": bool(settings_bytes),
        "size": Path(path).stat().st_size,
    }
