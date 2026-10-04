"""抓取 odds.500.com 单场页的亚指/大小盘，产出 cache/odds_pages_<期号>.json。

复用你自己 Chrome 的已验证会话（走 CDP 调试端口）。先完全退出 Chrome，再带调试端口
启动，然后用 setup_cdp_profile.py 把默认 profile 的 cookie 复制到该 profile 下::

    python setup_cdp_profile.py
    python tools/scrape_odds_pages.py --issue 26135

脚本只借用会话，不关闭浏览器，也只开一个标签页复用。撞到 EdgeOne 验证页时，
在 Chrome 里那个标签页勾选一次即可，脚本会自动继续。

单 IP 抓得太密会触发 EdgeOne「请求过于频繁」限流；脚本默认每页间隔 ``--delay``
秒并带随机抖动，检测到限流页立即停止。

只做这次改动是因为自带 Chromium 那条路已验证走不通：它没有已验证 cookie，
实测恒定停在验证页/限流页，且窗口与日常 Chrome 难以分辨、每次都要人工勾选。

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


DEFAULT_CDP = "http://127.0.0.1:9222"
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

CDP_HINT = """\u8fde\u4e0d\u4e0a Chrome \u8c03\u8bd5\u7aef\u53e3\u3002\u8bf7\u6309\u987a\u5e8f\u64cd\u4f5c\uff1a
  1) \u5148\u5b8c\u5168\u9000\u51fa Chrome\uff08\u7a97\u53e3\u5173\u6389\u8fd8\u4e0d\u591f\uff0c\u6258\u76d8\u56fe\u6807\u4e5f\u8981\u9000\uff09
  2) \u8fd0\u884c  python setup_cdp_profile.py  \u2014\u2014 \u5b83\u4f1a\u590d\u5236\u5df2\u9a8c\u8bc1 cookie \u5e76\u542f\u52a8\u5e26\u8c03\u8bd5\u7aef\u53e3\u7684 Chrome\n  3) \u786e\u8ba4 http://127.0.0.1:9222/json/version \u80fd\u6253\u5f00\uff0c\u518d\u91cd\u8dd1\u672c\u811a\u672c\nCookie \u590d\u5236\u4e0d\u80fd\u7701\uff1a\u6ca1\u5b83\u4f1a\u4e00\u76f4\u5361\u5728\u9a8c\u8bc1\u9875\u3002"""


class RateLimited(Exception):
    """命中 EdgeOne 的请求频率限制。"""


class BrowserGone(Exception):
    """浏览器/CDP 连接已断开。"""


def _safe(text: str) -> str:
    return text.encode("gbk", "replace").decode("gbk")


def log(message: str) -> None:
    # Windows 控制台默认 GBK，emoji 等字符会直接抛 UnicodeEncodeError。
    print(_safe(message), flush=True)


def acquire(pw, endpoint: str):
    """连上已启动的 Chrome，返回 (context, page)。这是唯一模式。

    context 属于用户，不能关；调用方只负责关自己开的那一个标签页。
    """
    browser = pw.chromium.connect_over_cdp(endpoint)
    if not browser.contexts:
        raise RuntimeError("CDP 连上了但没有可用上下文")
    context = browser.contexts[0]
    return context, context.new_page()


def browser_alive(page) -> bool:
    """探活：浏览器被关掉后 Playwright 的 sync API 可能永久阻塞，先做个廉价调用探一下。"""
    try:
        page.evaluate("() => 1")
        return True
    except PWError:
        return False


# 抓取结果状态。ok=拿到公司行；empty=页面正常渲染但零公司行（上游没开盘）；
# timeout/error=抓取失败。这三者必须区分，否则一次部分失败的抓取会把好数据
# 静默覆盖成空，报告显示成「无市场」，看起来像事实。
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"


def _error(detail: str) -> dict:
    return {"status": STATUS_ERROR, "rows": [], "detail": detail}


def fetch_page(page, url: str, wait_seconds: float) -> dict:
    """打开单场页取数据；撞到 EdgeOne 验证页则置前窗口并等待人工勾选。

    返回 {"status": ..., "rows": [...]}，status 取上面四个常量之一。
    """
    if not browser_alive(page):
        raise BrowserGone()
    try:
        page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
    except PWError as exc:
        detail = str(exc).splitlines()[0][:90]
        log(f"    [x] 打开失败：{detail}")
        return _error(detail)

    deadline = time.time() + wait_seconds
    warned = False
    settled = 0
    while time.time() < deadline:
        try:
            title = page.title() or ""
        except PWError as exc:
            return _error(str(exc).splitlines()[0][:90])

        if VERIFY_MARK in title:
            settled = 0
            if not warned:
                warned = True
                try:
                    page.bring_to_front()
                except PWError:
                    pass
                log("    [!] EdgeOne 验证页。")
                log("        请切到 Chrome 里刚打开的那个标签页（标题 Security Verification），")
                log("        勾选一次复选框；勾完本脚本会自动继续。")
        elif not title:
            # 还在导航，等下一轮
            settled = 0
        else:
            # 标题已是真实赔率页。只给 JS 渲染留几拍时间。
            try:
                body = page.evaluate(BODY_JS) or ""
                if any(mark in body for mark in RATE_LIMIT_MARKS):
                    raise RateLimited(url)
                rows = page.evaluate(EXTRACT_JS)
            except RateLimited:
                raise
            except PWError as exc:
                return _error(str(exc).splitlines()[0][:90])
            if rows:
                return {"status": STATUS_OK, "rows": rows}
            settled += 1
            if settled >= SETTLE_POLLS:
                # 真实页面已渲染但零公司行 = 上游确实没开盘，属正常结果
                return {"status": STATUS_EMPTY, "rows": []}

        # 等待期间页面可能因勾选而重载，不能用 page.wait_for_timeout（会抛），
        # 也不该在循环里做别的 Playwright 调用，纯 sleep 最稳。
        time.sleep(POLL_SECONDS)

    # 等到超时仍未拿到真实页面，多半是没人勾验证
    return {"status": STATUS_TIMEOUT, "rows": []}


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
        default=DEFAULT_CDP,
        help=f"已启动 Chrome 的调试端口，默认 {DEFAULT_CDP}",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=900.0, help="总时长上限（看门狗），默认 900"
    )
    args = parser.parse_args(argv)

    if sync_playwright is None:
        log("缺少 playwright：pip install playwright && playwright install chromium")
        return 2

    cache_dir = Path(args.cache_dir)
    matches, issue = collect_sfc_matches(args.issue, Fetcher(cache_dir))
    fids = [row["fixture_id"] for row in matches]
    total = len(fids) * len(KINDS)
    log(f"期号 {issue}：{len(fids)} 场，{total} 个页面，Chrome 调试端口：{args.cdp}")
    log(f"页面间隔约 {args.delay:.1f}s，预计耗时 {args.delay * total:.0f}s")

    result: dict[str, dict] = {}
    done = 0

    with sync_playwright() as pw:
        try:
            context, page = acquire(pw, args.cdp)
        except PWError as exc:
            log("")
            log(f"[x] 无法连接 Chrome 调试端口：{str(exc).splitlines()[0][:100]}")
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
                        payload = fetch_page(page, url, args.wait)
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
                    result[f"{fid}|{kind}"] = payload
                    st = payload.get("status")
                    note = "" if st == STATUS_OK else f"  <{st}>"
                    log(f"  [{done:>2}/{total}] {kind}-{fid}: {len(payload['rows'])} 家{note}")
                    time.sleep(args.delay * random.uniform(0.6, 1.4))
        finally:
            # 只关自己开的那一个标签页；用户的浏览器和其余标签一律不碰
            try:
                page.close()
            except PWError:
                pass

    out = cache_dir / f"odds_pages_{issue}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for page in result.values() if page.get("status") == STATUS_OK)
    empty = sum(1 for page in result.values() if page.get("status") == STATUS_EMPTY)
    bad = [k for k, page in result.items()
           if page.get("status") not in (STATUS_OK, STATUS_EMPTY)]
    log("")
    log(f"落盘 {out}")
    log(f"有盘口 {ok}   上游无盘口 {empty}   抓取失败 {len(bad)}")
    if bad:
        log("以下页面抓取失败（已带 status 标记，下游会忽略并告警，不会当成「无盘口」）：")
        for key in bad:
            log(f"  {key}  {result[key].get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
