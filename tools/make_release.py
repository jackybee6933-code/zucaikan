import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = os.environ.get("VERSION", "").lstrip("v")
APP = "足球胜负彩分析工具.exe"
UPDATER = "updater.exe"
# Release 资产名必须用 ASCII：GitHub Actions 在 Windows runner 上处理中文文件名会失败
APP_ASSET = "football_analysis.exe"
UPDATER_ASSET = "updater.exe"
PACKAGE_ASSET = "football_analysis_full.zip"


def main() -> None:
    stage = ROOT / "build" / "release-pkg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(DIST / APP, stage / APP)
    shutil.copy2(DIST / UPDATER, stage / UPDATER)
    zip_path = DIST / PACKAGE_ASSET
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(stage).as_posix())
    shutil.copy2(DIST / APP, DIST / APP_ASSET)
    shutil.copy2(DIST / UPDATER, DIST / UPDATER_ASSET)
    shutil.rmtree(stage)
    print(f"{APP_ASSET}, {UPDATER_ASSET}, {PACKAGE_ASSET}")


if __name__ == "__main__":
    main()
