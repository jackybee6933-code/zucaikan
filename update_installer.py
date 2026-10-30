"""Standalone installer used by the packaged app to replace itself.

Launched by the main app's updater as:
    <updater> --archive <verified.zip> --target <main exe> [--restart] [--wait-seconds N]

It waits for the target exe to be unlocked (the running main process has exited),
then swaps in the new exe from the archive and optionally restarts it.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
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


def _stop_running_app(pid: int) -> None:
    """结束仍在运行的主程序，否则它的 exe 一直处于锁定状态无法替换。"""
    if pid <= 0:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def _install_updater_if_missing(zf: zipfile.ZipFile, root: Path) -> None:
    """旧版中文更新器升级时，补出新版 updater.exe；已有则不覆盖正在运行的更新器。"""
    target = root / "updater.exe"
    if target.exists() or "updater.exe" not in zf.namelist():
        return
    staged = root / "updater.exe.new"
    with zf.open("updater.exe") as src, staged.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    staged.rename(target)


def _install_support_files(zf: zipfile.ZipFile, root: Path) -> None:
    """Install runtime scripts required by the main executable."""
    for name in ("tools/scrape_odds_pages.py",):
        if name not in zf.namelist():
            continue
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(target.name + ".new")
        with zf.open(name) as src, staged.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        staged.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=False)
    parser.add_argument("--target", required=False)
    parser.add_argument("--installer", required=False)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--kill-pid", type=int, default=0)
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--relaunch-env", default="")
    args = parser.parse_args()
    if args.installer:
        if not args.target:
            parser.error("--target is required with --installer")
        return _run_installer(
            Path(args.installer).resolve(),
            Path(args.target).resolve(),
            args.kill_pid,
            args.restart,
            args.relaunch_env,
        )
    if not args.archive or not args.target:
        parser.error("either --installer or both --archive and --target are required")
    return _install_zip(
        Path(args.archive).resolve(),
        Path(args.target).resolve(),
        args.kill_pid,
        args.wait_seconds,
        args.restart,
        args.relaunch_env,
    )


def _run_installer(
    installer: Path,
    target: Path,
    kill_pid: int,
    restart: bool,
    relaunch_env: str = "",
) -> int:
    root = target.parent
    try:
        _stop_running_app(kill_pid)
        deadline = time.time() + 120
        while not _target_unlocked(target) and time.time() < deadline:
            time.sleep(1)
        if not _target_unlocked(target):
            raise TimeoutError("main app still running, cannot replace")
        process = subprocess.Popen(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOCANCEL",
                "/NOICONS",
                "/SP-",
                "/CLOSEAPPLICATIONS",
                "/FORCECLOSEAPPLICATIONS",
                f"/DIR={root}",
                f"/LOG={installer.parent / 'install.log'}",
            ],
            cwd=str(root),
        )
        try:
            exit_code = process.wait(timeout=180)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise TimeoutError("installer timed out") from exc
        if exit_code != 0:
            raise OSError("installer exited unsuccessfully")
        if not target.is_file():
            raise FileNotFoundError(f"installed main app missing: {target}")
        if restart:
            marker = root / ".fa_update_restart"
            marker.write_text(
                json.dumps({"at": time.time(), "exe": target.name}),
                encoding="utf-8",
            )
            _relaunch_app(target, root, relaunch_env)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"update failed: {exc}", file=sys.stderr)
        return 1


def _install_zip(
    archive: Path,
    target: Path,
    kill_pid: int,
    wait_seconds: int,
    restart: bool,
    relaunch_env: str = "",
) -> int:
    try:
        _stop_running_app(kill_pid)
        deadline = time.time() + max(wait_seconds, 0)
        while not _target_unlocked(target) and time.time() < deadline:
            time.sleep(1)
        with zipfile.ZipFile(archive) as zf:
            name = _exe_in_archive(archive, target.name)
            if not name:
                raise ValueError("no exe found in upgrade archive")
            staged = target.with_name(target.name + ".new")
            with zf.open(name) as src, staged.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            _install_updater_if_missing(zf, target.parent)
            _install_support_files(zf, target.parent)
        if not _target_unlocked(target):
            raise TimeoutError("main app still running, cannot replace")
        backup = target.with_name(target.name + ".old")
        backup.unlink(missing_ok=True)
        target.rename(backup)
        staged.rename(target)
        backup.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if restart:
            marker = target.parent / ".fa_update_restart"
            marker.write_text(
                json.dumps({"at": time.time(), "exe": target.name}),
                encoding="utf-8",
            )
            _relaunch_app(target, target.parent, relaunch_env)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"update failed: {exc}", file=sys.stderr)
        return 1


def _relaunch_app(target: Path, root: Path, env_name: str = "") -> None:
    env = os.environ.copy()
    if env_name:
        env[env_name] = "1"
    options = {}
    if sys.platform == "win32":
        env["FA_UPDATE_NEW_CONSOLE"] = "1"
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
        ctypes.windll.kernel32.FreeConsole()
    subprocess.Popen(
        [str(target)],
        cwd=str(root),
        env=env,
        close_fds=True,
        **options,
    )


if __name__ == "__main__":
    raise SystemExit(main())
