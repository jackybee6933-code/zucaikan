"""Copy the EdgeOne verification cookie from the default Chrome profile into a
fresh profile dir that can be launched with --remote-debugging-port.

Chrome >= 136 refuses --remote-debugging-port on the default user-data-dir, so we
need a separate dir. Chrome decrypts cookies with DPAPI under the same Windows
user, so the copied cookie DB stays readable. Read-only on the source profile.
"""
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

SRC = Path.home() / "AppData/Local/Google/Chrome/User Data"
DST = Path.home() / "AppData/Local/fb-chrome-profile"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222

# 1) 建目录并复制必要文件
(DST / "Default" / "Network").mkdir(parents=True, exist_ok=True)
copied = []
for rel in ("Local State", "Default/Preferences", "Default/Network/Cookies"):
    s = SRC / rel
    d = DST / rel
    if s.exists():
        shutil.copy2(s, d)
        copied.append(f"{rel} ({s.stat().st_size / 1024:.0f} KB)")
print("已复制:")
for c in copied:
    print("  " + c)

# 2) 启动带调试端口的 Chrome
proc = subprocess.Popen(
    [
        CHROME,
        f"--remote-debugging-port={PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={DST}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ],
    shell=False,
)
print(f"\nChrome 已启动 pid={proc.pid}，等待调试端口...")

# 3) 等端口就绪
for _ in range(20):
    time.sleep(0.5)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
            print("CDP 就绪:", r.read().decode()[:200])
            break
    except Exception:
        continue
else:
    print("CDP 未就绪，请手动检查。")
