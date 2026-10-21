import hashlib
import json
import os
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
PACKAGE_NAME = "football_analysis_full.zip"


def main() -> None:
    version = os.environ.get("VERSION", "").lstrip("v")
    if not version:
        raise SystemExit("VERSION is required")
    package = DIST / PACKAGE_NAME
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = {
        "version": version,
        "url": f"https://github.com/jackybee6933-code/zucaikan/releases/download/v{version}/{PACKAGE_NAME}",
        "sha256": digest,
        "notes": "",
    }
    (DIST / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
