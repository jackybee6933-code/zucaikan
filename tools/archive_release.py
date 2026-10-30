"""Archive the current local release artifacts before a version change."""
from __future__ import annotations

import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
HISTORY = DIST / "history"

# Runtime data is intentionally excluded: it is large, mutable, and not part
# of the rollback package.
ARTIFACTS = (
    "football_analysis.exe",
    "updater.exe",
    "football_analysis_setup.exe",
    "football_analysis_full.zip",
    "latest.json",
    "tools/scrape_odds_pages.py",
)


def _version_from_source(root: Path) -> str:
    text = (root / "football_analysis" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([\d.]+)"', text)
    return match.group(1) if match else "unknown"


def _archive_name(version: str, now: datetime) -> str:
    stamp = now.strftime("%Y%m%d_%H%M%S")
    return f"football_analysis_v{version}_{stamp}.zip"


def archive_current_release(
    *,
    root: Path = ROOT,
    dist: Path | None = None,
    history: Path | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Create a timestamped rollback zip and return its path.

    Missing dist artifacts are skipped so a first release from a clean source
    checkout can still update the source version. If no artifact exists, no
    archive is created and ``None`` is returned.
    """
    dist = dist or (root / "dist")
    history = history or (dist / "history")
    now = now or datetime.now()
    version = _version_from_source(root)
    files = [(rel, dist / rel) for rel in ARTIFACTS if (dist / rel).is_file()]
    if not files:
        print("未找到可归档的旧版发布文件，跳过 dist/history 归档")
        return None

    history.mkdir(parents=True, exist_ok=True)
    archive = history / _archive_name(version, now)
    suffix = 2
    while archive.exists():
        archive = history / f"{archive.stem}_{suffix}{archive.suffix}"
        suffix += 1

    manifest = {
        "version": version,
        "created_at": now.isoformat(timespec="seconds"),
        "files": [rel for rel, _ in files],
    }
    temp = archive.with_suffix(".tmp.zip")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for rel, path in files:
                zf.write(path, rel)
            zf.writestr(
                "archive_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        temp.replace(archive)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    size_mb = archive.stat().st_size / 1048576
    print(f"已归档旧版 v{version}: {archive} ({size_mb:.1f} MB)")
    return archive


def main() -> int:
    try:
        archive_current_release()
    except Exception as exc:
        print(f"归档旧版失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
