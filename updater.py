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
    __version__ = "1.3.21"

DEFAULT_MANIFEST_URL = (
    "https://github.com/jackybee6933-code/zucaikan/releases/latest/download/latest.json"
)
RELEASE_API_URL = "https://api.github.com/repos/jackybee6933-code/zucaikan/releases/latest"
UPDATE_CACHE_FILENAME = ".update_manifest.json"
RELEASES_BASE_URL = "https://github.com/jackybee6933-code/zucaikan/releases/tag/v"
MANIFEST_ATTEMPTS = 3
MANIFEST_RETRY_DELAYS = (1, 3)
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAYS = (2, 5)
APP_EXE_NAME = "football_analysis.exe"
UPDATER_EXE_NAME = "updater.exe"
LEGACY_UPDATER_EXE_NAME = "足球更新器.exe"
INSTALLER_NAME = "football_analysis_setup.exe"
ARCHIVE_NAME = "football_analysis_full.zip"


class UpdateError(RuntimeError):
    pass


def _manifest_url() -> str:
    return os.environ.get("FA_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL)


def _http_json(url: str, *, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "football-analysis-updater"})
    last_error: Exception | None = None
    for attempt in range(MANIFEST_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise UpdateError("更新清单格式无效")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            retryable = exc.code in {408, 429} or exc.code >= 500
            message = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = exc
            retryable = True
            if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
                message = "访问超时"
            else:
                message = str(exc)
        except UpdateError:
            raise
        except Exception as exc:
            last_error = exc
            retryable = False
            message = str(exc)
        if not retryable or attempt + 1 >= MANIFEST_ATTEMPTS:
            if message == "访问超时":
                raise UpdateError(
                    "访问 GitHub 更新清单超时，请检查网络或代理（如 Clash）后重试"
                ) from last_error
            raise UpdateError(f"无法读取更新清单：{message}") from last_error
        time.sleep(MANIFEST_RETRY_DELAYS[attempt])
    raise UpdateError("无法读取更新清单")


def _release_manifest(data: dict) -> tuple[str, dict] | None:
    for asset in data.get("assets") or []:
        if str(asset.get("name") or "") == "latest.json":
            url = str(asset.get("browser_download_url") or "").strip()
            if url:
                return url, _http_json(url)
    return None


def _cache_path() -> Path:
    return app_root() / "cache" / UPDATE_CACHE_FILENAME


def _release_url(version: str, data: dict) -> str:
    value = str(data.get("release_url") or data.get("html_url") or "").strip()
    return value or f"{RELEASES_BASE_URL}{version.lstrip('vV')}"


def _validated_info(data: dict, *, current_version: str) -> dict:
    version = str(data.get("version", "")).strip()
    installer_url = str(data.get("installer_url") or data.get("url") or "").strip()
    installer_sha256 = str(
        data.get("installer_sha256") or data.get("sha256") or ""
    ).strip().lower()
    archive_url = str(data.get("archive_url") or "").strip()
    archive_sha256 = str(data.get("archive_sha256") or "").strip().lower()
    has_installer = bool(installer_url) and len(installer_sha256) == 64
    has_archive = bool(archive_url) and len(archive_sha256) == 64
    if not version or (not has_installer and not has_archive):
        raise UpdateError("更新清单缺少安装包或升级包校验信息")
    latest = _version_tuple(version)
    current = _version_tuple(current_version)
    return {
        "current_version": current_version,
        "latest_version": version,
        "update_available": latest > current,
        "url": installer_url,
        "package_type": "inno-setup" if has_installer else "zip",
        "sha256": installer_sha256,
        "archive_url": archive_url,
        "archive_sha256": archive_sha256,
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
    data = None
    resolved_manifest_url = target
    last_error: UpdateError | None = None
    candidates = [target]
    if target == DEFAULT_MANIFEST_URL:
        candidates.append(RELEASE_API_URL)
    for candidate in candidates:
        try:
            payload = _http_json(candidate)
            if candidate == RELEASE_API_URL:
                resolved = _release_manifest(payload)
                if resolved is None:
                    raise UpdateError("GitHub Releases API 未找到 latest.json")
                resolved_manifest_url, data = resolved
            else:
                data = payload
                resolved_manifest_url = candidate
            break
        except UpdateError as exc:
            last_error = exc
            continue
    if data is None:
        raise last_error or UpdateError("无法读取更新清单，请检查网络后重试")
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = _validated_info(data, current_version=current_version)
    info.update({
        "cached": False,
        "checked_at": checked_at,
        "manifest_url": resolved_manifest_url,
        "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)),
    })
    _save_cache(data, manifest_url=resolved_manifest_url, checked_at=checked_at)
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_package(info: dict, *, progress=None, phase=None) -> tuple[Path, str]:
    primary_url = str(info.get("url", ""))
    primary_sha = str(info.get("sha256", "")).lower()
    primary_type = "inno-setup" if primary_url.lower().endswith(".exe") else "zip"
    candidates = [
        (primary_type, primary_url, primary_sha),
        ("zip", str(info.get("archive_url", "")), str(info.get("archive_sha256", ""))),
    ]
    candidates = [item for item in candidates if item[1] and len(item[2]) == 64]
    if not candidates:
        raise UpdateError("更新信息无效")

    last_error: Exception | None = None
    for package_type, url, expected in candidates:
        result = _download_candidate(url, expected, progress=progress, phase=phase)
        if result[0] is not None:
            return result[0], package_type
        last_error = result[1]
    raise UpdateError(f"下载升级包失败：{last_error}")


def _download_candidate(
    url: str,
    expected: str,
    *,
    progress=None,
    phase=None,
) -> tuple[Path | None, Exception | None]:
    temp_dir = Path(tempfile.mkdtemp(prefix="fa_update_"))
    target = temp_dir / ("package.exe" if url.lower().endswith(".exe") else "package.zip")
    last_error: Exception | None = None
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
            if phase:
                phase("verify")
            actual = _sha256(target)
            if actual != expected:
                raise UpdateError("升级包 SHA-256 校验失败")
            if phase:
                phase("ready")
            return target, None
        except Exception as exc:
            last_error = exc
            target.unlink(missing_ok=True)
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time.sleep(DOWNLOAD_RETRY_DELAYS[attempt])
    shutil.rmtree(temp_dir, ignore_errors=True)
    return None, last_error


def install_downloaded(
    archive: Path,
    *,
    wait_seconds: int = 10,
    package_type: str = "zip",
) -> dict:
    root = app_root()
    target = root / APP_EXE_NAME
    if not target.is_file():
        raise UpdateError(f"未找到主程序：{target}")
    if str(archive.suffix).lower() == ".exe" or str(package_type).lower() == "inno-setup":
        installer_exe = root / "updater.exe"
        marker = root / ".fa_update_restart"
        marker.unlink(missing_ok=True)
        process = subprocess.Popen(
            [
                str(installer_exe),
                "--installer", str(archive),
                "--target", str(target),
                "--kill-pid", str(os.getpid()),
                "--restart",
            ],
            cwd=str(root),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return {"updater_pid": process.pid, "wait_seconds": wait_seconds}
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

