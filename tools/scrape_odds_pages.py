"""抓取 odds.500.com 单场页的亚指/大小盘，产出 cache/odds_pages_<期号>.json。

两种浏览器模式：

**--cdp（推荐，复用你自己的 Chrome）**
先完全退出 Chrome，再带调试端口重启，然后::

    python tools/scrape_odds_pages.py --issue 26135 --cdp http://127.0.0.1:9222

脚本只借用你的已验证会话，不会关闭浏览器，也只开一个标签页复用。

**默认（自带 Chromium）**
脚本自己拉起 Playwright Chromium，撞到 EdgeOne 验证页时需在**那个新窗口**里人工勾选::

    python tools/scrape_odds_pages.py --issue 26135

注意：单 IP 抓得太密会触发 EdgeOne「请求过于频繁」限流，脚本默认每页间隔 ``--delay``
秒并带随机抖动，检测到限流页立即停止。窗口标题带 "Chrome for Testing" 的才是脚本的
浏览器，和你日常用的 Chrome 互不相通。

依赖 playwright（仅开发期使用，不进 exe）：``pip install playwright && playwright install chromium``
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from football_analysis.http import Fetcher
from football_analysis.pipeline import collect_sfc_matches


try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright
except ImportError:  # 缺少依赖时也要能 import 本模块
    PWError = RuntimeError  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]


VERIFY_MARK = "Security Verification"
RATE_LIMIT_MARKS = ("频繁", "过于频繁", "Too Many Requests", "too many requests")
PAGE_TIMEOUT_MS = 25_000
POLL_SECONDS = 1.5
# 真实页面出现后，等这几拍让 JS 把公司行渲染出来；到点仍为空即视为「无盘口」
SETTLE_POLLS = 3
KINDS = ("yazhi", "daxiao")


EXTRACT_JS = """() => {
  const grab = (td) => Array.from(td.querySelectorAll('td')).map((x) => x.textContent.trim());
  const out = [];
  document.querySelectorAll('tr').forEach((tr) => {
    const a = tr.querySelector('a[href*="cid="]');
    if (!a) return;
    const tds = Array.from(tr.children).filter((c) => c.tagName === 'TD');
    if (tds.length < 6) return;
    out.push([
      (a.getAttribute('href').match(/cid=(\\d+)/) || [])[1] || '',
      ...grab(tds[2]).slice(0, 3),
      ...grab(tds[4]).slice(0, 3),
    ]);
  });
  return out;
}"""

BODY_JS = "() => (document.body ? document.body.innerText : '')"

CDP_HINT = """连不上 Chrome 调试端口。请按顺序操作：
  1) 完全退出 Chrome（窗口关掉还不够，要托盘图标也退出 / 任务管理器无 chrome.exe）
  2) 用带调试端口的方式启动：
     "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222
  3) 确认 http://127.0.0.1:9222/json/version 能打开，再重跑本脚本"""


class RateLimited(Exception):
    """命中 EdgeOne 的请求频率限制。"""


class BrowserGone(Exception):
    """浏览器/CDP 连接已断开。"""


def _safe(text: str) -> str:
    return text.encode("gbk", "replace").decode("gbk")


def log(message: str) -> None:
    # Windows 控制台默认 GBK，emoji 等字符会直接抛 UnicodeEncodeError。
    print(_safe(message), flush=True)


def acquire(pw, args, profile: Path):
    """返回 (context, page, owned)；owned=False 时不能关闭 context（那是用户的浏览器）。"""
    if args.cdp:
        browser = pw.chromium.connect_over_cdp(args.cdp)
        if not browser.contexts:
            raise RuntimeError("CDP 连上了但没有可用上下文")
        context = browser.contexts[0]
        page = context.new_page()
        return context, page, False

    profile.mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        str(profile),
        headless=args.headless,
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    )
    page = context.pages[0] if context.pages else context.new_page()
    return context, page, True


def browser_alive(page) -> bool:
    """探活：浏览器被关掉后 Playwright 的 sync API 可能永久阻塞，先做个廉价调用探一下。"""
    try:
        page.evaluate("() => 1")
        return True
    except PWError:
        return False


def fetch_page(page, url: str, wait_seconds: float) -> list[list]:
    """打开单场页取数据；撞到 EdgeOne 验证页则置前窗口并等待人工勾选。"""
    if not browser_alive(page):
        raise BrowserGone()
    try:
        page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
    except PWError as exc:
        log(f"    [x] 打开失败：{str(exc).splitlines()[0][:90]}")
        return []

    deadline = time.time() + wait_seconds
    warned = False
    settled = 0
    while time.time() < deadline:
        try:
            title = page.title() or ""
        except PWError:
            return []

        if VERIFY_MARK in title:
            settled = 0
            if not warned:
                warned = True
                try:
                    page.bring_to_front()
                except PWError:
                    pass
                log("    [!] EdgeOne 验证页。")
                log("        请切到**新弹出的那个 Chromium 窗口**（标题含 Security Verification），")
                log("        在里面勾选一次复选框。注意：不是你已经开着的 Chrome —— 两者互不相通。")
                log("        勾完本脚本会自动继续。")
        elif not title:
            # 还在导航，等下一轮
            settled = 0
        else:
            # 标题已是真实赔率页。只给 JS 渲染留几拍时间：之后无论有无公司行都算完成——
            # 零公司行是「上游没开盘」的正常结果，不能当成失败空转到超时。
            try:
                body = page.evaluate(BODY_JS) or ""
                if any(mark in body for mark in RATE_LIMIT_MARKS):
                    raise RateLimited(url)
                rows = page.evaluate(EXTRACT_JS)
            except RateLimited:
                raise
            except PWError:
                return []
            if rows:
                return rows
            settled += 1
            if settled >= SETTLE_POLLS:
                return []

        # 等待期间页面可能因勾选而重载，不能用 page.wait_for_timeout（会抛），
        # 也不该在循环里做别的 Playwright 调用，纯 sleep 最稳。
        time.sleep(POLL_SECONDS)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取 odds.500.com 单场页盘口")
    parser.add_argument("--issue", default="", help="期号；留空则用当前最新一期")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--wait", type=float, default=120.0, help="撞到验证页时等待人工勾选的秒数")
    parser.add_argument(
        "--delay", type=float, default=8.0, help="每个页面之间的间隔秒数（带随机抖动）"
    )
    parser.add_argument(
        "--cdp",
        default="",
        help="连已启动的 Chrome 调试端口，如 http://127.0.0.1:9222；留空则用自带 Chromium",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=900.0, help="总时长上限（看门狗），默认 900"
    )
    parser.add_argument("--headless", action="store_true", help="仅自带 Chromium 模式可用")
    args = parser.parse_args(argv)

    if sync_playwright is None:
        log("缺少 playwright：pip install playwright && playwright install chromium")
        return 2

    cache_dir = Path(args.cache_dir)
    matches, issue = collect_sfc_matches(args.issue, Fetcher(cache_dir))
    fids = [row["fixture_id"] for row in matches]
    total = len(fids) * len(KINDS)
    mode = f"CDP {args.cdp}" if args.cdp else "自带 Chromium"
    log(f"期号 {issue}：{len(fids)} 场，{total} 个页面，浏览器模式：{mode}")
    log(f"页面间隔约 {args.delay:.1f}s，预计耗时 {args.delay * total:.0f}s")

    result: dict[str, dict] = {}
    done = 0

    with sync_playwright() as pw:
        try:
            context, page, owned = acquire(pw, args, cache_dir / ".browser-profile")
        except PWError as exc:
            log("")
            log(f"[x] 无法连接浏览器：{str(exc).splitlines()[0][:100]}")
            if args.cdp:
                log("")
                for line in CDP_HINT.splitlines():
                    log(line)
            return 4

        deadline = time.time() + args.max_seconds
        try:
            for fid in fids:
                for kind in KINDS:
                    done += 1
                    if time.time() > deadline:
                        log("")
                        log(f"[!] 超过总时长 {args.max_seconds}s，中止（未写入文件）。")
                        return 5
                    url = f"https://odds.500.com/fenxi/{kind}-{fid}.shtml"
                    try:
                        rows = fetch_page(page, url, args.wait)
                    except BrowserGone:
                        log("")
                        log("[!] 浏览器已断开（多半是被关掉了），中止（未写入文件）。")
                        log("    重跑前请先重新启动带调试端口的 Chrome，且中途不要关闭窗口。")
                        return 4
                    except RateLimited:
                        log("")
                        log("[!] 触发 EdgeOne「请求过于频繁」限流，已停止（未写入文件）。")
                        log("    等几十分钟再跑，或调大 --delay。")
                        return 3
                    except PWError as exc:
                        log("")
                        log(f"[!] 浏览器中断：{str(exc).splitlines()[0][:90]}")
                        log("    未写入文件，重新运行即可。")
                        return 4
                    result[f"{fid}|{kind}"] = {"rows": rows}
                    note = "" if rows else "  无盘口"
                    log(f"  [{done:>2}/{total}] {kind}-{fid}: {len(rows)} 家{note}")
                    time.sleep(args.delay * random.uniform(0.6, 1.4))
        finally:
            # 只关自己开的；用户的浏览器绝不碰
            for closer in (lambda: page.close(), lambda: context.close() if owned else None):
                try:
                    closer()
                except PWError:
                    pass

    out = cache_dir / f"odds_pages_{issue}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    hit = sum(1 for page in result.values() if page["rows"])
    log("")
    log(f"落盘 {out}")
    log(f"有盘口页面 {hit}/{len(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
