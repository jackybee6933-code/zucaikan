"""把默认 Chrome profile 的 EdgeOne 验证 cookie 复制到可挂调试端口的独立 profile，并启动 Chrome。

Chrome >= 136 拒绝在默认 user-data-dir 上开 --remote-debugging-port，所以要用独立
目录；cookie 靠同一 Windows 用户的 DPAPI 解密，复制过去仍可读。对源 profile 只读。

原本是独立脚本 setup_cdp_profile.py；收到包内后 exe 的「刷新盘口分歧」也能直接调用，
不必再 subprocess 一个 .py（exe 里没有 Python 解释器）。
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

SRC = Path.home() / "AppData/Local/Google/Chrome/User Data"
DST = Path.home() / "AppData/Local/fb-chrome-profile"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

_COPY_FILES = ("Local State", "Default/Preferences", "Default/Network/Cookies")

# 窗口默认待在屏幕外：复制来的 Default/Preferences 带着用户 profile 的
# app_window_placement，新 Chrome 会开在和用户窗口一模一样的位置和大小
# （2026-10-12 实测 left=10,top=10,1265x1372），盖住查询页，看起来就像
# 查询页被顶掉了。屏幕外窗口实测 visibilityState 仍为 visible、定时器不被
# 节流，抓取不受影响；碰到验证页 scraper 会调 reveal_window 把它挪回来。
OFFSCREEN_ARGS = ("--window-position=-32000,-32000", "--window-size=1200,900")

_CHROME_PROC: subprocess.Popen | None = None


def copy_profile() -> tuple[list[str], list[str]]:
    """复制验证所需文件到独立 profile 目录，返回 (已复制, 被锁跳过)。

    默认 Chrome 运行时会独占锁住 Network/Cookies（SQLite），此时复制必失败。
    目标 profile 里通常已有上次复制的可用 EdgeOne cookie，跳过即可；
    若 cookie 真过期，Chrome 会停在验证页等人勾选，脚本本来就支持这个分支。
    """
    (DST / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for rel in _COPY_FILES:
        s = SRC / rel
        if not s.exists():
            continue
        try:
            shutil.copy2(s, DST / rel)
            copied.append(f"{rel} ({s.stat().st_size / 1024:.0f} KB)")
        except OSError as exc:
            skipped.append(f"{rel}（{type(exc).__name__} {exc.errno}，默认 Chrome 正在运行？）")
    return copied, skipped


def _probe(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def _find_chrome() -> str:
    """返回可用的 Chrome 路径；找不到时抛 FileNotFoundError。

    依次尝试默认 64/32 位安装路径，最后回退到 PATH 里的 chrome。
    """
    import os

    candidates = [CHROME, r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    exe = shutil.which("chrome") or shutil.which("chrome.exe")
    if exe:
        candidates.append(exe)
    for path in dict.fromkeys(candidates):
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("未找到 Google Chrome（检查过默认 64/32 位安装路径与 PATH）；请先安装 Chrome 再使用「刷新盘口分歧」/「AI 分析」。")


def launch_cdp_chrome(port: int = 9222, wait_seconds: float = 10.0) -> tuple[bool, list[str]]:
    """复制 cookie 并启动带调试端口的 Chrome，等端口就绪。返回 (是否就绪, 日志)。"""
    copied, skipped = copy_profile()
    logs = ["已复制:"] + [f"  {c}" for c in copied]
    if skipped:
        logs.append("跳过（被默认 Chrome 锁住，沿用已有 cookie）:")
        logs += [f"  {s}" for s in skipped]
    global _CHROME_PROC
    try:
        chrome_path = _find_chrome()
    except FileNotFoundError as exc:
        return False, [str(exc), "请先安装 Chrome（或确认默认安装路径），再重试。"]
    try:
        proc = subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={DST}",
                "--no-first-run",
                "--no-default-browser-check",
                *OFFSCREEN_ARGS,
                "about:blank",
            ],
            shell=False,
        )
    except OSError as exc:
        return False, [f"Chrome 启动失败：{exc}", f"路径：{chrome_path}"]
    _CHROME_PROC = proc
    logs.append(f"Chrome 已启动（{chrome_path} pid={proc.pid}），等待调试端口...")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(0.5)
        if _probe(port):
            logs.append(f"CDP 就绪: http://127.0.0.1:{port}")
            return True, logs
    logs.append(
        "CDP 未就绪（Chrome 已启动但 10 秒内调试端口没通）。请手动检查："
        "1) 防火墙/杀毒软件是否拦截了 127.0.0.1:9222 或 Chrome；"
        "2) 企业策略（组策略/MDM）是否禁用了远程调试；"
        "3) 是否有别的程序占用了 9222 端口。"
    )
    return False, logs


def ensure_cdp_chrome(port: int = 9222) -> str | None:
    """端口通就直接返回 None；否则启动 Chrome 并等待。失败返回提示。"""
    if _probe(port):
        return None
    ok, logs = launch_cdp_chrome(port)
    for line in logs:
        print(line)
    if ok:
        return None
    return f"http://127.0.0.1:{port} 仍连不上，请检查 Chrome 是否被拦截。"


def shutdown_cdp_chrome() -> None:
    """关闭 launch_cdp_chrome 启动的 Chrome 进程；未启动过则空操作。"""
    global _CHROME_PROC
    if _CHROME_PROC is None:
        return
    proc, _CHROME_PROC = _CHROME_PROC, None
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass
