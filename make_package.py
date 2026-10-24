"""Build the complete deliverable zip: exe at top level + cache/data/reports."""
import shutil
import zipfile
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / "build" / "pkg"
ZIP = ROOT / "dist" / f"足彩分析工具_完整包_{date.today():%Y%m%d}.zip"
EXE = "football_analysis.exe"
UPDATER = "updater.exe"


def reset_stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)


def copy_tree(src: Path, dest_name: str, *, limit: int | None = None) -> int:
    # 跳过 . 开头的目录/文件：cache/.browser-profile 是抓取工具的 Chromium 配置
    # （含本机 EdgeOne 验证 cookie，17MB），属于开发态残留，不进交付包。
    files = sorted(
        p
        for p in src.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src).parts)
    )
    if limit is not None:
        files = files[-limit:]
    out = STAGE / dest_name
    for f in files:
        target = out / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
    return len(files)


reset_stage()

# 1) exe 必须在压缩包顶层
shutil.copy2(ROOT / "dist" / EXE, STAGE / EXE)
print(f"exe            -> {EXE}")

# 2) 更新器：自动升级链路需要它和主程序同目录（updater.install_downloaded 在 app 根找它）
shutil.copy2(ROOT / "dist" / UPDATER, STAGE / UPDATER)
print(f"updater        -> {UPDATER}")

# 3) 异动库：用 dist/data，历史更全（24 期 / 970 场已录比分）
n = copy_tree(ROOT / "dist" / "data", "data")
print(f"data           -> {n} 个文件")

# 4) cache：.raw 供离线重跑，加 odds_pages_<期号>.json（缺它就没有让球/大小球）
n = copy_tree(ROOT / "cache", "cache")
print(f"cache          -> {n} 个文件")

# 5) reports：最近 7 份，与旧包体量一致
n = copy_tree(ROOT / "reports", "reports", limit=7)
print(f"reports        -> {n} 个文件")

if ZIP.exists():
    ZIP.unlink()
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for f in sorted(p for p in STAGE.rglob("*") if p.is_file()):
        zf.write(f, f.relative_to(STAGE).as_posix())

shutil.rmtree(STAGE)
print(f"\n输出: {ZIP.name}  {ZIP.stat().st_size / 1048576:.1f} MB")
