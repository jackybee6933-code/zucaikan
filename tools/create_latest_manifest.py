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
    package = DIST / PACKAGE_NAME
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    notes_path = Path(__file__).resolve().parents[1] / "docs" / "releases" / f"v{version}.md"
    notes = os.environ.get("RELEASE_NOTES", "").strip()
    if not notes and notes_path.is_file():
        notes = notes_path.read_text(encoding="utf-8").strip()
    manifest = {
        "version": version,
        "url": f"https://github.com/jackybee6933-code/zucaikan/releases/download/v{version}/{PACKAGE_NAME}",
        "sha256": digest,
        "release_url": f"https://github.com/jackybee6933-code/zucaikan/releases/tag/v{version}",
        "notes": notes,
    }
    installer = DIST / INSTALLER_NAME
    if installer.is_file():
        manifest["installer_url"] = (
            f"https://github.com/jackybee6933-code/zucaikan/releases/download/"
            f"v{version}/{INSTALLER_NAME}"
        )
        manifest["installer_sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
        manifest["installer_package_type"] = "inno-setup"
    (DIST / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
