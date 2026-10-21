"""Standalone installer used by the packaged app to replace itself.

Launched by the main app's updater as:
    <updater> --archive <verified.zip> --target <main exe> [--restart] [--wait-seconds N]

It waits for the target exe to be unlocked (the running main process has exited),
then swaps in the new exe from the archive and optionally restarts it.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def _exe_in_archive(archive: Path, preferred: str) -> str | None:
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    if preferred in names:
        return preferred
    for name in names:
        if name.lower().endswith(".exe"):
            return name
    return None


def _target_unlocked(target: Path) -> bool:
    """A running PE is locked for writing on Windows; r+b succeeds only when idle."""
    try:
        with target.open("r+b"):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    archive = Path(args.archive).resolve()
    target = Path(args.target).resolve()
    try:
        deadline = time.time() + max(args.wait_seconds, 0)
        while not _target_unlocked(target) and time.time() < deadline:
            time.sleep(1)
        with zipfile.ZipFile(archive) as zf:
            name = _exe_in_archive(archive, target.name)
            if not name:
                raise ValueError("no exe found in upgrade archive")
            staged = target.with_name(target.name + ".new")
            with zf.open(name) as src, staged.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        if not _target_unlocked(target):
            raise TimeoutError("main app still running, cannot replace")
        backup = target.with_name(target.name + ".old")
        backup.unlink(missing_ok=True)
        target.rename(backup)
        staged.rename(target)
        backup.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if args.restart:
            marker = target.parent / ".fa_update_restart"
            marker.write_text(
                json.dumps({"at": time.time(), "exe": target.name}),
                encoding="utf-8",
            )
            subprocess.Popen([str(target)], cwd=str(target.parent))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
