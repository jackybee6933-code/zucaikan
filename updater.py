"""Check GitHub Releases and download a verified application package."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from . import __version__
else:
    __version__ = "1.3.6"

DEFAULT_MANIFEST_URL = (
    "https://github.com/jackybee6933-code/zucaikan/releases/latest/download/latest.json"
)
UPDATE_CACHE_FILENAME = ".update_manifest.json"
RELEASES_BASE_URL = "https://github.com/jackybee6933-code/zucaikan/releases/tag/v"
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAYS = (2, 5)
APP_EXE_NAME = "football_analysis.exe"
UPDATER_EXE_NAME = "updater.exe"
LEGACY_UPDATER_EXE_NAME = "足球更新器.exe"


class UpdateError(RuntimeError):
    pass


def _manifest_url() -> str:
    return os.environ.get("FA_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)


def _http_json(url: str, *, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analysis-updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise UpdateError("访问 GitHub 更新清单超时，请检查网络或代理（如 Clash）后重试") from exc
        raise UpdateError(f"无法读取更新清单：{exc}") from exc
    except Exception as exc:
        raise UpdateError(f"无法读取更新清单：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateError("更新清单格式无效")
    return payload


def _cache_path() -> Path:
    return app_root() / "cache" / UPDATE_CACHE_FILENAME


def _release_url(version: str, data: dict) -> str:
    value = str(data.get("release_url") or data.get("html_url") or "").strip()
    return value or f"{RELEASES_BASE_URL}{version.lstrip('vV')}"


def _validated_info(data: dict, *, current_version: str) -> dict:
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
        "release_url": _release_url(version, data),
    }


def _save_cache(data: dict, *, manifest_url: str, checked_at: str) -> None:
    payload = dict(data)
    payload["_manifest_url"] = manifest_url
    payload["_checked_at"] = checked_at
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        # 更新缓存只是诊断和离线兜底，不能阻断正常升级。
        pass


def load_cached_update(*, current_version: str = __version__) -> dict:
    path = _cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError("本地没有可用的更新清单缓存") from exc
    info = _validated_info(payload, current_version=current_version)
    info.update({
        "cached": True,
        "checked_at": str(payload.get("_checked_at") or "").strip(),
        "manifest_url": str(payload.get("_manifest_url") or _manifest_url()),
        "elapsed_ms": 0,
    })
    return info


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).strip().lstrip("vV").split("."))
    except ValueError as exc:
        raise UpdateError(f"版本号无效：{value}") from exc


def check_update(manifest_url: str | None = None, *, current_version: str = __version__) -> dict:
    target = manifest_url or _manifest_url()
    started = time.monotonic()
    data = _http_json(target)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = _validated_info(data, current_version=current_version)
    info.update({
        "cached": False,
        "checked_at": checked_at,
        "manifest_url": target,
        "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)),
    })
    _save_cache(data, manifest_url=target, checked_at=checked_at)
    return info


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
    last_error = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
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
            if total > 0 and downloaded != total:
                raise UpdateError(f"升级包下载不完整：{downloaded}/{total} 字节")
            actual = _sha256(target)
            if actual != expected:
                raise UpdateError("升级包 SHA-256 校验失败")
            return target
        except Exception as exc:
            last_error = exc
            target.unlink(missing_ok=True)
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time.sleep(DOWNLOAD_RETRY_DELAYS[attempt])
    shutil.rmtree(temp_dir, ignore_errors=True)
    if isinstance(last_error, UpdateError):
        raise UpdateError(f"{last_error}（已尝试 {DOWNLOAD_ATTEMPTS} 次）") from last_error
    raise UpdateError(
        f"下载升级包失败（已尝试 {DOWNLOAD_ATTEMPTS} 次）：{last_error}"
    ) from last_error


def install_downloaded(archive: Path, *, wait_seconds: int = 10) -> dict:
    root = app_root()
    target = root / APP_EXE_NAME
    if not target.is_file():
        raise UpdateError(f"未找到主程序：{target}")
    updater = next(
        (root / name for name in (UPDATER_EXE_NAME, LEGACY_UPDATER_EXE_NAME)
         if (root / name).is_file()),
        None,
    )
    if updater is None:
        raise UpdateError("完整包缺少 updater.exe，请手动解压新完整包")
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
