"""命令行入口：复制已验证 cookie 并启动带调试端口的 Chrome。

逻辑在 football_analysis/cdp_profile.py 里，exe 内的「刷新盘口分歧」直接调函数。
"""

from football_analysis.cdp_profile import ensure_cdp_chrome

if __name__ == "__main__":
    err = ensure_cdp_chrome()
    if err:
        print(err)
