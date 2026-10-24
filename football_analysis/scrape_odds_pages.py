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

只用 stdlib（football_analysis/cdp.py）通过 CDP 远程端口驱动系统 Chrome，不再依赖 playwright，exe 里也能跑。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from .http import Fetcher
from .pipeline import collect_sfc_matches
from .odds_pages import PAGES_META_FILENAME
from .cdp_profile import ensure_cdp_chrome
from .cdp import CdpError, CdpPage


DEFAULT_CDP = "http://127.0.0.1:9222"
VERIFY_MARK = "Security Verification"
RATE_LIMIT_MARKS = ("频繁", "过于频繁", "Too Many Requests", "too many requests")
PAGE_TIMEOUT_MS = 25_000
POLL_SECONDS = 1.5
# 真实页面出现后，等这几拍让 JS 把公司行渲染出来；到点仍为空即视为「无盘口」
SETTLE_POLLS = 3
# shuju（数据分析页）只取球队资料文本，供大模型阅读；放在最后，限流中断时先保住盘口页。
KINDS = ("yazhi", "daxiao", "shuju")


# 每行结构：[cid, 公司名(打码), 即时主水, 即时盘, 即时客水, 初主水, 初盘, 初客水]
# 公司名取公司单元格里的可见名，未登录时 500 会打码成「威***」「*门」等，原样保留。
EXTRACT_JS = """() => {
  const grab = (td) => Array.from(td.querySelectorAll('td')).map((x) => x.textContent.trim());
  const out = [];
  document.querySelectorAll('tr').forEach((tr) => {
    const a = tr.querySelector('a[href*="cid="]');
    if (!a) return;
    const tds = Array.from(tr.children).filter((c) => c.tagName === 'TD');
    if (tds.length < 6) return;
    const cell = tds[1];
    const span = cell.querySelector('.quancheng');
    const link = cell.querySelector('a[title]');
    out.push([
        (a.getAttribute('href').match(/cid=(\\d+)/) || [])[1] || '',
        ((span && span.textContent) || (link && link.getAttribute('title')) || '').trim(),
        ...grab(tds[2]).slice(0, 3),
      ...grab(tds[4]).slice(0, 3),
    ]);
  });
  return out;
}"""

RATECHECK_JS = (
    "() => { const t = document.body ? document.body.innerText : '';"
    " return %s.some(m => t.indexOf(m) >= 0); }" % json.dumps(list(RATE_LIMIT_MARKS))
)

# 数据分析页：按最近的小标题给表格分组，只留分析用的块（积分排名/交战历史/近期战绩/
# 未来赛事/平均数据），丢掉登录框、彩票列表、预计阵容这些噪音。
SHUJU_JS = r"""() => {
  const HEADS = 'h1,h2,h3,h4,.tit,.sub_title,.teamTitle,.lstit,.maintitle';
  const heads = Array.from(document.querySelectorAll(HEADS));
  const labelFor = (el) => {
    let best = '';
    for (const h of heads) {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
        const t = h.textContent.trim().replace(/\s+/g, ' ');
        if (t && t.length <= 24) best = t;
      }
    }
    return best;
  };
  const SKIP_LABEL = ['用户登录', '免费注册', '忘记密码', '500彩票', '预计阵容'];
  const out = [];
  document.querySelectorAll('table').forEach((t) => {
    const rows = Array.from(t.querySelectorAll('tr'));
    if (rows.length < 2 || rows.length > 40) return;
    const label = labelFor(t);
    if (SKIP_LABEL.some((s) => label.includes(s))) return;
    const body = rows.map((tr) => tr.textContent.replace(/\s+/g, '')).join('');
    if (body.includes('用户名') || body.includes('验证码') || body.includes('心水推荐')) return;
    out.push('## ' + (label || '表'));
    rows.slice(0, 14).forEach((tr) => {
      const line = Array.from(tr.children)
        .map((c) => c.textContent.trim().replace(/\s+/g, ' '))
        .join(' | ');
      if (line.replace(/[|\s]/g, '')) out.push(line);
    });
  });
  return out.join('\n').slice(0, 12000);
}"""

CDP_HINT = """已自动尝试启动带调试端口的 Chrome（独立 profile，cookie 复制），但仍连不上。请检查：
  1) 防火墙/杀毒软件是否拦截了 127.0.0.1:9222 或 Chrome
  2) 企业策略（组策略/MDM）是否禁用了远程调试
  3) 是否有别的程序占用了 9222 端口
  4) 是否安装了 Google Chrome（默认 64/32 位安装路径）
Cookie 复制不能省：没有它页面会一直卡在验证页。"""


class RateLimited(Exception):
    """命中 EdgeOne 的请求频率限制。"""


class BrowserGone(Exception):
    """浏览器/CDP 连接已断开。"""


def _safe(text: str) -> str:
    return text.encode("gbk", "replace").decode("gbk")


def log(message: str) -> None:
    # Windows 控制台默认 GBK，emoji 等字符会直接抛 UnicodeEncodeError。
    print(_safe(message), flush=True)


def _write_progress(cache_dir: Path, issue: str, **fields) -> None:
    """把进度快照写进 cache/.ai_scrape_progress.json，供查询页进度条轮询。
    只写文件不读文件，写失败（只读目录等）直接忽略，不影响抓取主流程。"""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / ".ai_scrape_progress"
        path.write_text(json.dumps({"issue": issue, **fields}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def acquire(endpoint: str) -> CdpPage:
    """连接已开 CDP 端口的 Chrome，开一个新标签页（唯一模式）。"""
    return CdpPage(endpoint)


def browser_alive(page) -> bool:
    """探活：浏览器被关掉后 Playwright 的 sync API 可能永久阻塞，先做个廉价调用探一下。"""
    try:
        page.evaluate("() => 1")
        return True
    except CdpError:
        return False


# 抓取结果状态。ok=拿到公司行；empty=页面正常渲染但零公司行（上游没开盘）；
# timeout/error=抓取失败。这三者必须区分，否则一次部分失败的抓取会把好数据
# 静默覆盖成空，报告显示成「无市场」，看起来像事实。
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"


def _error(detail: str, *, text_mode: bool = False) -> dict:
    payload = {"status": STATUS_ERROR, "detail": detail}
    payload["text" if text_mode else "rows"] = "" if text_mode else []
    return payload


# 断点文件：每抓完一页就落盘，限流/断网中断后下次只补没抓的页。
PARTIAL_TTL_HOURS = 6.0


def partial_path(cache_dir: Path, issue: str) -> Path:
    return cache_dir / f".odds_partial_{issue}.json"


def load_partial(path: Path, wanted: set[str]) -> dict[str, dict]:
    """读断点；过期或损坏当没有。只认 ok/empty，timeout/error 下次重抓。"""
    if not path.exists():
        return {}
    try:
        if time.time() - path.stat().st_mtime > PARTIAL_TTL_HOURS * 3600:
            path.unlink(missing_ok=True)
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: page
        for key, page in data.items()
        if key in wanted
        and isinstance(page, dict)
        and page.get("status") in (STATUS_OK, STATUS_EMPTY)
    }


def save_partial(path: Path, result: dict[str, dict]) -> None:
    """只落已确定的页（ok/empty）；先写临时文件再替换，避免半截 JSON。"""
    pages = {
        key: page
        for key, page in result.items()
        if isinstance(page, dict) and page.get("status") in (STATUS_OK, STATUS_EMPTY)
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save_kind_metadata(cache_dir: Path, issue: str, fids: list[str], active_kinds: list[str], result: dict[str, dict]) -> None:
    """Record completion time per page kind so different buttons do not share freshness."""
    path = cache_dir / PAGES_META_FILENAME.format(issue=issue)
    metadata: dict[str, float] = {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(old, dict):
            metadata.update(
                {str(k): float(v) for k, v in old.items() if isinstance(v, (int, float))}
            )
    except (OSError, ValueError, TypeError):
        pass
    now = time.time()
    for kind in active_kinds:
        complete = all(
            isinstance(result.get(f"{fid}|{kind}"), dict)
            and result[f"{fid}|{kind}"].get("status") in (STATUS_OK, STATUS_EMPTY)
            for fid in fids
        )
        if complete:
            metadata[kind] = now
    if not metadata:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def fetch_page(
    page,
    url: str,
    wait_seconds: float,
    *,
    extract_js: str = EXTRACT_JS,
    text_mode: bool = False,
) -> dict:
    """打开单场页取数据；撞到 EdgeOne 验证页则置前窗口并等待人工勾选。

    返回 {"status": ..., "rows": [...]}；text_mode 下改为 {"status": ..., "text": "..."}
    （数据分析页只留球队资料文本给大模型读）。status 取上面四个常量之一。
    """
    if not browser_alive(page):
        raise BrowserGone()
    try:
        page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
    except CdpError as exc:
        detail = str(exc).splitlines()[0][:90]
        log(f"    [x] 打开失败：{detail}")
        return _error(detail, text_mode=text_mode)

    deadline = time.time() + wait_seconds
    warned = False
    settled = 0
    while time.time() < deadline:
        try:
            title = page.title() or ""
        except CdpError as exc:
            return _error(str(exc).splitlines()[0][:90], text_mode=text_mode)

        if VERIFY_MARK in title:
            settled = 0
            if not warned:
                warned = True
                try:
                    page.bring_to_front()
                    # 窗口平时在屏幕外，验证页需要人工勾选时挪回屏幕中央
                    page.reveal_window()
                except CdpError:
                    pass
                log("    [!] EdgeOne 验证页（窗口已挪回屏幕中央）。")
                log("        请切到 Chrome 里刚打开的那个标签页（标题 Security Verification），")
                log("        勾选一次复选框；勾完本脚本会自动继续。")
        elif not title:
            # 还在导航，等下一轮
            settled = 0
        else:
            # 标题已是真实赔率页。只给 JS 渲染留几拍时间。
            try:
                if page.evaluate(RATECHECK_JS):
                    raise RateLimited(url)
                data = page.evaluate(extract_js)
            except RateLimited:
                raise
            except CdpError as exc:
                return _error(str(exc).splitlines()[0][:90], text_mode=text_mode)
            if data:
                return {"status": STATUS_OK, "text" if text_mode else "rows": data}
            settled += 1
            if settled >= SETTLE_POLLS:
                # 真实页面已渲染但零公司行 = 上游确实没开盘，属正常结果
                return {"status": STATUS_EMPTY, "text" if text_mode else "rows": "" if text_mode else []}

        # 等待期间页面可能因勾选而重载，不能用 page.wait_for_timeout（会抛），
        # 也不该在循环里做别的 Playwright 调用，纯 sleep 最稳。
        time.sleep(POLL_SECONDS)

    # 等到超时仍未拿到真实页面，多半是没人勾验证
    return {"status": STATUS_TIMEOUT, "text" if text_mode else "rows": "" if text_mode else []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取 odds.500.com 单场页盘口")
    parser.add_argument("--issue", default="", help="期号；留空则用当前最新一期")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--wait", type=float, default=120.0, help="撞到验证页时等待人工勾选的秒数")
    parser.add_argument(
        "--delay", type=float, default=5.0, help="每个页面之间的间隔秒数（带随机抖动）"
    )
    parser.add_argument(
        "--cdp",
        default=DEFAULT_CDP,
        help=f"已启动 Chrome 的调试端口，默认 {DEFAULT_CDP}",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=900.0, help="总时长上限（看门狗），默认 900"
    )
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=list(KINDS),
        default=None,
        help="只抓指定页种；不传则抓全部 KINDS（默认 yazhi daxiao shuju）",
    )
    args = parser.parse_args(argv)

    cache_dir = Path(args.cache_dir)
    _write_progress(cache_dir, args.issue, phase="collect", total=0, done=0, failed=0)
    try:
        matches, issue = collect_sfc_matches(args.issue, Fetcher(cache_dir))
    except Exception:
        _write_progress(cache_dir, args.issue, phase="collect_error", total=0, done=0, failed=0)
        raise
    fids = [row["fixture_id"] for row in matches]
    active_kinds = args.kinds or list(KINDS)
    total = len(fids) * len(active_kinds)
    log(f"期号 {issue}：{len(fids)} 场，{total} 个页面，Chrome 调试端口：{args.cdp}")

    out = cache_dir / f"odds_pages_{issue}.json"
    partial = partial_path(cache_dir, issue)
    wanted = {f"{fid}|{kind}" for fid in fids for kind in active_kinds}
    # 断点续传：沿用上次已抓好的页，只补剩下的。
    result: dict[str, dict] = load_partial(partial, wanted)
    done = len(result)
    failed = 0
    if done:
        log(f"断点续传：沿用已抓 {done}/{total} 页，只补剩余页面")
    _write_progress(cache_dir, issue, phase="scrape", total=total, done=done, failed=0)
    log(f"页面间隔约 {args.delay:.1f}s，剩余 {total - done} 页预计 {args.delay * (total - done):.0f}s")

    try:
        page = acquire(args.cdp)
    except CdpError as exc:
        log("")
        log(f"[x] 无法连接 Chrome 调试端口：{str(exc).splitlines()[0][:100]}")
        # 端口不通时自动尝试拉起带调试端口的 Chrome（独立 profile + cookie 复制），
        # 与 exe 里「刷新盘口分歧」的 _ensure_cdp 行为一致。
        if not args.cdp or args.cdp.startswith("http://127.0.0.1"):
            port = 9222
            try:
                from urllib.parse import urlparse as _urlparse

                port = _urlparse(args.cdp).port or 9222
            except Exception:  # noqa: BLE001
                pass
            err = ensure_cdp_chrome(port)
            if not err:
                log(f"已自动启动 Chrome，重试连接 {args.cdp}")
                try:
                    page = acquire(args.cdp)
                except CdpError as exc2:
                    log("")
                    log(f"[x] 重试仍无法连接：{str(exc2).splitlines()[0][:100]}")
                    for line in CDP_HINT.splitlines():
                        log(line)
                    return 4
            else:
                log("")
                for line in CDP_HINT.splitlines():
                    log(line)
                return 4
        else:
            log("")
            for line in CDP_HINT.splitlines():
                log(line)
            return 4

    deadline = time.time() + args.max_seconds
    try:
        for fid in fids:
            for kind in active_kinds:
                key = f"{fid}|{kind}"
                if key in result:
                    continue
                page_no = done + 1
                if time.time() > deadline:
                    log("")
                    log(f"[!] 超过总时长 {args.max_seconds}s，中止；已抓页面已保存，重试会从断点继续。")
                    _write_progress(cache_dir, issue, phase="aborted", total=total, done=page_no - 1, failed=failed, reason="timeout")
                    return 5
                url = f"https://odds.500.com/fenxi/{kind}-{fid}.shtml"
                text_mode = kind == "shuju"
                try:
                    payload = fetch_page(
                        page,
                        url,
                        args.wait,
                        extract_js=SHUJU_JS if text_mode else EXTRACT_JS,
                        text_mode=text_mode,
                    )
                except BrowserGone:
                    log("")
                    log("[!] 浏览器已断开（多半是被关掉了），中止；已抓页面已保存，重试会从断点继续。")
                    log("    重跑前请先重新启动带调试端口的 Chrome，且中途不要关闭窗口。")
                    _write_progress(cache_dir, issue, phase="aborted", total=total, done=page_no - 1, failed=failed, reason="browser_gone")
                    return 4
                except RateLimited:
                    log("")
                    log("[!] 触发 EdgeOne「请求过于频繁」限流，已停止；已抓页面已保存，等会儿重试会从断点继续。")
                    log("    等几十分钟再跑，或调大 --delay。")
                    _write_progress(cache_dir, issue, phase="aborted", total=total, done=page_no - 1, failed=failed, reason="rate_limited")
                    return 3
                except CdpError as exc:
                    log("")
                    log(f"[!] 浏览器中断：{str(exc).splitlines()[0][:90]}")
                    log("    已抓页面已保存，重新运行会从断点继续。")
                    _write_progress(cache_dir, issue, phase="aborted", total=total, done=page_no - 1, failed=failed, reason="cdp_error")
                    return 4
                done += 1
                failed += 1 if payload.get("status") not in (STATUS_OK, STATUS_EMPTY) else 0
                result[key] = payload
                if payload.get("status") in (STATUS_OK, STATUS_EMPTY):
                    save_partial(partial, result)
                st = payload.get("status")
                note = "" if st == STATUS_OK else f"  <{st}>"
                if text_mode:
                    log(f"  [{done:>2}/{total}] {kind}-{fid}: {len(payload.get('text') or '')} 字{note}")
                else:
                    log(f"  [{done:>2}/{total}] {kind}-{fid}: {len(payload.get('rows') or [])} 家{note}")
                _write_progress(cache_dir, issue, phase="scrape", total=total, done=done, failed=failed, last=f"{kind}-{fid}")
                time.sleep(args.delay * random.uniform(0.6, 1.4))
    finally:
        # 只关自己开的那一个标签页；用户的浏览器和其余标签一律不碰
        try:
            page.close()
        except CdpError:
            pass

    # 只抓了部分页种（--kinds）时，与已有产物合并，避免覆盖 yazhi/daxiao 数据。
    if args.kinds and out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing.update(result)
                result = existing
        except (json.JSONDecodeError, OSError):
            pass  # 旧产物损坏则只用新数据
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    save_kind_metadata(cache_dir, issue, fids, active_kinds, result)
    partial.unlink(missing_ok=True)
    ok = sum(1 for page in result.values() if page.get("status") == STATUS_OK)
    empty = sum(1 for page in result.values() if page.get("status") == STATUS_EMPTY)
    bad = [k for k, page in result.items()
           if page.get("status") not in (STATUS_OK, STATUS_EMPTY)]
    log("")
    log(f"落盘 {out}")
    log(f"成功 {ok}   上游无数据 {empty}   抓取失败 {len(bad)}（含 shuju 资料页）")
    _write_progress(cache_dir, issue, phase="done", total=total, done=done, failed=len(bad))
    # 等几秒让查询页轮询到「完成」态，再清掉进度文件
    time.sleep(5)
    try:
        (cache_dir / ".ai_scrape_progress").unlink(missing_ok=True)
    except OSError:
        pass
    if bad:
        log("以下页面抓取失败（已带 status 标记，下游会忽略并告警，不会当成「无盘口」）：")
        for key in bad:
            log(f"  {key}  {result[key].get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
