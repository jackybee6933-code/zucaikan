import hashlib
import json
import os
import urllib.parse
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
PACKAGE_NAME = "足球分析系统完整包.zip"


def main() -> None:
    version = os.environ.get("VERSION", "").lstrip("v")
    if not version:
        raise SystemExit("VERSION is required")
    package = DIST / PACKAGE_NAME
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    # 资产名含中文，url 必须百分号编码，否则 releases/download 返回 404
    asset_url = urllib.parse.quote(PACKAGE_NAME)
    manifest = {
        "version": version,
        "url": f"https://github.com/jackybee6933-code/zucaikan/releases/download/v{version}/{asset_url}",
        "sha256": digest,
        "notes": "",
    }
    (DIST / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
