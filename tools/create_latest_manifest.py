import hashlib
import json
import os
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
PACKAGE_NAME = "football_analysis_full.zip"
INSTALLER_NAME = "football_analysis_setup.exe"


def main() -> None:
    version = os.environ.get("VERSION", "").lstrip("v")
    if not version:
        raise SystemExit("VERSION is required")
    notes_path = Path(__file__).resolve().parents[1] / "docs" / "releases" / f"v{version}.md"
    notes = os.environ.get("RELEASE_NOTES", "").strip()
    if not notes and notes_path.is_file():
        notes = notes_path.read_text(encoding="utf-8").strip()
    base = (
        "https://github.com/jackybee6933-code/zucaikan/releases/download/"
        f"v{version}"
    )
    manifest = {
        "version": version,
        "release_url": f"https://github.com/jackybee6933-code/zucaikan/releases/tag/v{version}",
        "notes": notes,
    }
    installer = DIST / INSTALLER_NAME
    if installer.is_file():
        manifest["installer_url"] = f"{base}/{INSTALLER_NAME}"
        manifest["installer_sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
        manifest["installer_package_type"] = "inno-setup"
    package = DIST / PACKAGE_NAME
    if package.is_file():
        manifest["archive_url"] = f"{base}/{PACKAGE_NAME}"
        manifest["archive_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    (DIST / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
