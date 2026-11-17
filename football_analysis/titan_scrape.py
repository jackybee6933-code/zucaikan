from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .cdp import CdpError, CdpPage
from .h2h import fetch_issue_schedule, match_no_to_tian_match
from .http import Fetcher
from .odds_pages import (
    PAGES_META_FILENAME,
    PAGES_STALE_HOURS,
    load_odds_pages_raw,
    pages_kind_age_hours,
)
from .pipeline import collect_sfc_matches
from .titan_odds import TITAN_ASIAN_URL, TITAN_TOTAL_URL


STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"

TITAN_EXTRACT_JS = r"""() => {
  const out = [];
  const table = document.querySelector('#odds') || document.querySelector('table.odds');
  if (!table) return out;
  table.querySelectorAll('tr').forEach((tr) => {
    const input = tr.querySelector('input[name="oddsShow"][data-id], [data-id]');
    const tds = Array.from(tr.children).filter((node) => node.tagName === 'TD');
    if (!input || tds.length < 8) return;
    if (tr.classList.contains('maxTr') || tr.classList.contains('minTr')) return;
    const values = tds.slice(2, 8).map((td) => td.innerText.trim());
    if (values.length < 6) return;
    out.push([
      input.getAttribute('data-id') || '',
      tds[1].innerText.trim(),
      ...values,
    ]);
  });
  return out;
}"""


def _status(page: object) -> str:
    if not isinstance(page, dict):
        return ""
    return str(page.get("status") or "")


def _valid(page: object) -> bool:
    return _status(page) in (STATUS_OK, STATUS_EMPTY)


def _has_market_rows(page: object) -> bool:
    if not isinstance(page, dict) or _status(page) != STATUS_OK:
        return False
    return any(
        isinstance(row, (list, tuple)) and len(row) > 3 and str(row[3] or "").strip()
        for row in (page.get("rows") or [])
    )


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _save_titan_timestamp(cache_dir: Path, issue: str, kind: str) -> None:
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
    metadata[kind] = time.time()
    _atomic_write(path, metadata)


def _targets(issue: str, cache_dir: Path) -> tuple[list[tuple[str, str, str]], str]:
    """Return (500 fixture id, Titan id, market kind) targets needing fallback."""
    fetcher = Fetcher(cache_dir)
    matches, actual_issue = collect_sfc_matches(issue, fetcher)
    schedule_map, _date, schedule_error = fetch_issue_schedule(fetcher, matches, actual_issue)
    mapping = match_no_to_tian_match(matches, schedule_map=schedule_map, schedules=[])
    raw = load_odds_pages_raw(cache_dir, actual_issue)
    result: list[tuple[str, str, str]] = []
    for row in matches:
        fixture_id = str(row.get("fixture_id") or "")
        titan_id = str(mapping.get(str(row.get("match_no") or ""), "") or "")
        if not fixture_id or not titan_id:
            continue
        for market in ("yazhi", "daxiao"):
            page_500 = raw.get(f"{fixture_id}|{market}")
            if _has_market_rows(page_500):
                continue
            titan_kind = f"titan_{market}"
            page_titan = raw.get(f"{fixture_id}|{titan_kind}")
            age = pages_kind_age_hours(cache_dir, actual_issue, titan_kind)
            if _valid(page_titan) and (age is None or age <= PAGES_STALE_HOURS):
                continue
            result.append((fixture_id, titan_id, market))
    return result, schedule_error


def run_titan_fallback(
    issue: str,
    cache_dir: str | Path,
    endpoint: str,
    *,
    delay: float = 5.0,
    wait: float = 30.0,
) -> str | None:
    """Scrape Titan only for markets missing from the 500 per-match pages."""
    from . import scrape_odds_pages as base

    cache_dir = Path(cache_dir)
    try:
        targets, schedule_error = _targets(issue, cache_dir)
    except Exception as exc:  # noqa: BLE001
        return f"Titan 备用源准备失败：{exc}"
    if not targets:
        return None

    if schedule_error:
        base.log(f"Titan 赛程映射部分失败：{schedule_error}")
    base.log(f"Titan 备用盘口：补抓 {len(targets)} 个市场页")
    raw = load_odds_pages_raw(cache_dir, issue)
    try:
        page = CdpPage(endpoint)
    except CdpError as exc:
        return f"Titan 备用源无法连接 Chrome：{str(exc).splitlines()[0][:100]}"

    failed = 0
    try:
        for index, (fixture_id, titan_id, market) in enumerate(targets, 1):
            kind = f"titan_{market}"
            url = (
                TITAN_ASIAN_URL.format(match_id=titan_id)
                if market == "yazhi"
                else TITAN_TOTAL_URL.format(match_id=titan_id)
            )
            try:
                payload = base.fetch_page(
                    page,
                    url,
                    wait,
                    extract_js=TITAN_EXTRACT_JS,
                )
            except base.RateLimited as exc:
                base.KEEP_BROWSER_OPEN = True
                try:
                    page.bring_to_front()
                    page.reveal_window()
                except CdpError:
                    pass
                payload = {
                    "status": "rate_limited",
                    "rows": [],
                    "detail": str(exc)[:120],
                }
            except base.BrowserGone:
                failed += 1
                base.log(f"  [{index:>2}/{len(targets)}] Titan {kind}-{titan_id}: 浏览器断开")
                continue
            if payload.get("status") not in (STATUS_OK, STATUS_EMPTY):
                failed += 1
            raw[f"{fixture_id}|{kind}"] = payload
            if payload.get("status") in (STATUS_OK, STATUS_EMPTY):
                _atomic_write(cache_dir / f"odds_pages_{issue}.json", raw)
                _save_titan_timestamp(cache_dir, issue, kind)
            base.log(
                f"  [{index:>2}/{len(targets)}] Titan {kind}-{titan_id}: "
                f"{len(payload.get('rows') or [])} 家"
                + (f"  <{payload.get('status')}>" if payload.get("status") != STATUS_OK else "")
            )
            time.sleep(delay)
    finally:
        if not base.keep_browser_open():
            try:
                page.close()
            except CdpError:
                pass
    if failed:
        return f"Titan 备用源有 {failed} 个页面抓取失败，已保存成功页"
    return None
