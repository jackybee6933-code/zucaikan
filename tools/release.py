"""发版工具：同步版本号 -> 残留检查 -> 全量测试 -> 打 tag。

用法：
    python tools/release.py 1.4                 # 同步版本 + 测试
    python tools/release.py 1.3.1 --tag          # 同步 + 测试 + 打 v1.3.1 tag

只做「改版本号 + 校验 + 打 tag」，不做 git 提交；提交与推送由你手动完成。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (文件, 旧串模板, 新串模板)：模板里 {old}=旧版本、{v}=新版本
VERSION_EDITS = [
    ("football_analysis/__init__.py",
     '__version__ = "{old}"', '__version__ = "{v}"'),
    ("updater.py",
     '    __version__ = "{old}"', '    __version__ = "{v}"'),
    ("football_analysis/query_app.py",
     '足彩分析系统{old}', '足彩分析系统{v}'),
    ("football_analysis/query_report.py",
     '足彩分析系统{old}', '足彩分析系统{v}'),
    ("tests/test_store.py",
     'self.assertIn("足彩分析系统{old}"', 'self.assertIn("足彩分析系统{v}"'),
    ("tests/test_query_server.py",
     'self.assertIn("足彩分析系统{old}"', 'self.assertIn("足彩分析系统{v}"'),
]

# 残留检查只看这些文件（排除 dist/build/cache/data/reports 等生成物）
RESIDUAL_FILES = [
    "football_analysis/__init__.py", "updater.py",
    "football_analysis/query_app.py", "football_analysis/query_report.py",
    "tests/test_store.py", "tests/test_query_server.py",
]


def current_version() -> str:
    text = (ROOT / "football_analysis/__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([\d.]+)"', text)
    if not m:
        sys.exit("无法从 __init__.py 读取 __version__")
    return m.group(1)


def apply_edits(new: str, old: str) -> None:
    for rel, old_pat, new_pat in VERSION_EDITS:
        p = ROOT / rel
        src = p.read_text(encoding="utf-8")
        before = src
        src = src.replace(old_pat.format(old=old), new_pat.format(v=new))
        if src != before:
            p.write_text(src, encoding="utf-8")
            print(f"  改 {rel}: {old} -> {new}")
        else:
            print(f"  跳过 {rel}（无匹配或已是 {new}）")


def residual_check(new: str) -> int:
    bad = 0
    for rel in RESIDUAL_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        if rel not in ("football_analysis/__init__.py", "updater.py"):
            src = re.sub(r"足彩分析系统\d+\.\d+(?:\.\d+)?", "", src)
        for m in re.finditer(r"足彩分析系统(\d+\.\d+(?:\.\d+)?)", src):
            if m.group(1) != new:
                bad += 1
                print(f"  残留 {rel}: 足彩分析系统{m.group(1)}")
        for m in re.finditer(r'__version__\s*=\s*"([\d.]+)"', src):
            if m.group(1) != new and rel in ("football_analysis/__init__.py", "updater.py"):
                bad += 1
                print(f'  残留 {rel}: __version__ = "{m.group(1)}"')
    return bad


def run_tests() -> bool:
    print("运行全量测试 ...")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=ROOT, capture_output=True, text=True)
    # unittest 把结果摘要（OK / FAILED / Ran N tests）写到 stderr
    merged = r.stdout + "\n" + r.stderr
    tail = [l for l in merged.splitlines() if l.strip()][-12:]
    for line in tail:
        print("  " + line)
    ok = r.returncode == 0 and ("OK" in r.stderr or "OK" in r.stdout)
    if not ok:
        print(merged[-3000:])
    print("测试通过" if ok else "测试失败")
    return ok


def main() -> int:
    args = [a for a in sys.argv[1:]]
    do_tag = "--tag" in args
    vers = [a for a in args if not a.startswith("--")]
    if len(vers) != 1:
        print(__doc__)
        return 2
    new = vers[0].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", new):
        print(f"版本号需形如 1.4 或 1.3.1（收到 {new!r}）")
        return 2

    old = current_version()
    print(f"当前 {old} -> 目标 {new}")
    apply_edits(new, old)
    bad = residual_check(new)
    if bad:
        print(f"仍有 {bad} 处残留，请手动清理后重跑")
        return 1
    print("残留检查通过")
    if not run_tests():
        return 1
    print(f"\n版本同步完成（{old} -> {new}），测试全过。")
    if do_tag:
        r = subprocess.run(["git", "tag", f"v{new}"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"打 tag 失败（可能已存在）：{r.stderr.strip()}")
            return 1
        print(f"已打 tag v{new}")
        print(f'下一步：git add -A && git commit -m "..." 然后 git push origin v{new}')
    else:
        print(f"未打 tag；需要时：git tag v{new} && git push origin v{new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
