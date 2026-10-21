"""Check GitHub Releases and download a verified application package."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

if __package__:
    from . import __version__
else:
    __version__ = "1.3.1"

DEFAULT_MANIFEST_URL = (
    "https://github.com/jackybee6933-code/zucaikan/releases/latest/download/latest.json"
)
APP_EXE_NAME = "足球胜负彩分析工具.exe"
UPDATER_EXE_NAME = "足球更新器.exe"


class UpdateError(RuntimeError):
    pass


def _manifest_url() -> str:
    return os.environ.get("FA_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)


def _http_json(url: str, *, timeout: float = 10.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analysis-updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"无法读取更新清单：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateError("更新清单格式无效")
    return payload


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).strip().lstrip("vV").split("."))
    except ValueError as exc:
        raise UpdateError(f"版本号无效：{value}") from exc


def check_update(manifest_url: str | None = None, *, current_version: str = __version__) -> dict:
    data = _http_json(manifest_url or _manifest_url())
    version = str(data.get("version", "")).strip()
    url = str(data.get("url", "")).strip()
    sha256 = str(data.get("sha256", "")).strip().lower()
    if not version or not url or len(sha256) != 64:
        raise UpdateError("更新清单缺少 version/url/sha256")
    latest = _version_tuple(version)
    current = _version_tuple(current_version)
    return {
        "current_version": current_version,
        "latest_version": version,
        "update_available": latest > current,
        "url": url,
        "sha256": sha256,
        "notes": str(data.get("notes", "")),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_package(info: dict, *, progress=None) -> Path:
    url = str(info.get("url", ""))
    expected = str(info.get("sha256", "")).lower()
    if not url or len(expected) != 64:
        raise UpdateError("更新信息无效")
    temp_dir = Path(tempfile.mkdtemp(prefix="fa_update_"))
    target = temp_dir / "package.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "football-analysis-updater"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp, target.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    except Exception as exc:
        raise UpdateError(f"下载升级包失败：{exc}") from exc
    actual = _sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("升级包 SHA-256 校验失败")
    return target


def install_downloaded(archive: Path, *, wait_seconds: int = 10) -> dict:
    root = app_root()
    target = root / APP_EXE_NAME
    if not target.is_file():
        raise UpdateError(f"未找到主程序：{target}")
    updater = root / UPDATER_EXE_NAME
    if not updater.is_file():
        raise UpdateError("完整包缺少足球更新器.exe，请手动解压新完整包")
    process = subprocess.Popen(
        [
            str(updater),
            "--archive", str(archive),
            "--target", str(target),
            "--kill-pid", str(os.getpid()),
            "--restart",
        ],
        cwd=str(root),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return {"updater_pid": process.pid, "wait_seconds": wait_seconds}


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()
