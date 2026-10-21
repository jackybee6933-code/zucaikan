import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = os.environ.get("VERSION", "").lstrip("v")
APP = "足球胜负彩分析工具.exe"
UPDATER = "足球更新器.exe"


def main() -> None:
    stage = ROOT / "build" / "release-pkg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(DIST / APP, stage / APP)
    shutil.copy2(DIST / UPDATER, stage / UPDATER)
    zip_path = DIST / "足球分析系统完整包.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(stage).as_posix())
    shutil.rmtree(stage)
    print(zip_path)


if __name__ == "__main__":
    main()
