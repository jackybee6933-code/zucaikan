from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_from_bytes

from .bets import parse_kickoff, summarize_bets
from .odds_pages import (
    COMPANY_NAMES,
    PAGES_META_FILENAME,
    PAGES_STALE_HOURS,
    load_odds_pages_raw,
    page_failures,
    pages_age_hours,
    pages_kind_age_hours,
)
from .report_index import build_issue_report_index
from . import __version__
from .llm import normalize_team_read


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _data_freshness(
    rows: list[dict],
    analyses: list[dict],
    *,
    issue: str,
    cache_dir: str | Path | None,
) -> dict[str, str]:
    """Summarize DB capture time plus independent odds/data artifact health."""
    snapshots = list(analyses)
    if not snapshots:
        snapshots = rows
    captured_values = sorted(
        {
            str(item.get("captured_at") or "").strip()
            for item in snapshots
            if str(item.get("captured_at") or "").strip()
        }
    )
    if captured_values:
        captured = captured_values[-1].replace("T", " ", 1)
        captured_text = f"抓取时间：{captured}"
    else:
        captured_text = "抓取时间：未记录"

    if not issue:
        return {
            "class": "freshness-muted",
            "captured": captured_text,
            "pages": "盘口产物：选择期号后查看",
            "data": "数据产物：选择期号后查看",
        }

    raw = load_odds_pages_raw(cache_dir, issue)
    file_age = pages_age_hours(cache_dir, issue)
    meta_exists = bool(
        cache_dir
        and (Path(cache_dir) / PAGES_META_FILENAME.format(issue=issue)).exists()
    )
    has_data_pages = any(
        str(key).rsplit("|", 1)[-1] == "shuju" for key in (raw or {})
    )
    failures = page_failures(raw)

    def artifact(kind_label: str, kinds: tuple[str, ...]) -> tuple[str, str]:
        keys = [
            str(key)
            for key in (raw or {})
            if str(key).rsplit("|", 1)[-1] in kinds
        ]
        if not keys:
            return f"{kind_label}：未找到", "freshness-warn"
        ages = [
            pages_kind_age_hours(cache_dir, issue, kind)
            for kind in kinds
            if any(key.endswith(f"|{kind}") for key in keys)
        ]
        ages = [age for age in ages if age is not None]
        if ages:
            age = max(ages)
        elif meta_exists:
            # A metadata file that lacks this kind means its timestamp is
            # unknown; do not fall back to the shared JSON mtime, which may
            # have been touched by an unrelated page-kind scrape.
            age = None
        elif kind_label == "盘口产物" and has_data_pages:
            # Legacy artifacts can be merged by AI before per-kind metadata
            # exists. The shared file mtime then describes shuju, not odds.
            age = None
        else:
            age = file_age
        if age is None:
            return (
                f"{kind_label}：未记录" if keys else f"{kind_label}：未找到",
                "freshness-warn",
            )
        age_text = "刚刚" if age < 0.1 else f"{age:.1f} 小时前"
        text = f"{kind_label}：{age_text}"
        css = "freshness-stale" if age > PAGES_STALE_HOURS else "freshness-ok"
        failed = sum(1 for key in failures if key.rsplit("|", 1)[-1] in kinds)
        empty = sum(
            1
            for key, page in (raw or {}).items()
            if str(key).rsplit("|", 1)[-1] in kinds
            and isinstance(page, dict)
            and str(page.get("status") or "").strip() == "empty"
        )
        details = []
        if failed:
            details.append(f"抓取失败 {failed} 页")
        if empty:
            details.append(f"上游未开盘 {empty} 页")
        if age > PAGES_STALE_HOURS:
            details.append(f"超过 {PAGES_STALE_HOURS:.0f}h")
        if details:
            text += "（" + "；".join(details) + "）"
            if failed or empty:
                css = "freshness-warn"
        return text, css

    pages_text, pages_class = artifact("盘口产物", ("yazhi", "daxiao"))
    data_text, data_class = artifact("数据产物", ("shuju",))
    freshness_class = (
        "freshness-warn"
        if "freshness-warn" in (pages_class, data_class)
        else "freshness-stale"
        if "freshness-stale" in (pages_class, data_class)
        else "freshness-ok"
    )
    return {
        "class": freshness_class,
        "captured": captured_text,
        "pages": pages_text,
        "data": data_text,
        "pages_class": pages_class,
        "data_class": data_class,
    }


def _badge(level: str) -> str:
    css = "stable"
    if level == "重点关注":
        css = "hot"
    elif level == "关注":
        css = "watch"
    return f'<span class="badge badge-{css}">{_esc(level)}</span>'


def _league_badge(league: str, color: str) -> str:
    """500 赛事栏配色：有颜色走实心标签，没颜色退回纯文本。"""
    name = _esc(league)
    if not color:
        return name
    return f'<span class="league-badge" style="background:{color};">{name}</span>'


def _kind_badge(kind: str, *, muted: bool = False) -> str:
    if kind == "退盘":
        kind = "降盘"
    # 旧 run 里存的是「大盘降 / 大盘升」，按新文案回显
    kind = {"大盘降": "大小盘降", "大盘升": "大小盘升"}.get(kind, kind)
    css = {
        "降盘": "kind-drop",
        "升盘": "kind-rise",
        "水位反转": "kind-reverse",
        "水位摆动": "kind-swing",
    }.get(kind, "kind-watch")
    if muted and kind in ("降盘", "升盘"):
        css += " kind-muted"
    return f'<span class="kind {css}">{_esc(kind or "无")}</span>'

def _split_html(row: dict, uid: str) -> str:
    """Render all companies in a market-split popup."""
    blocks = row.get("split") or []
    if not isinstance(blocks, list):
        blocks = []
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        picked = str(block.get("picked") or "")
        rows = [item for item in block.get("rows") or [] if isinstance(item, dict)]
        if not any(
            str(item.get("initial") or "") != str(item.get("current") or "")
            for item in rows
        ):
            continue
        trs = []
        for item in rows:
            raw_cid = str(item.get("cid") or "")
            cid = _esc(raw_cid)
            name = _esc(str(item.get("name") or "").strip() or COMPANY_NAMES.get(raw_cid, ""))
            label = f"{name}({cid})" if name else cid
            cur = _esc(str(item.get("current") or "\u2014"))
            cur_cell = f"<b>{cur}</b>" if str(item.get("cid")) == picked else cur
            trs.append(
                f"<tr><td>{label}</td><td>{_esc(str(item.get('initial') or '\u2014'))} \u2192 {cur_cell}</td></tr>"
            )
        parts.append(
            f'<div class="split-card"><div class="split-card-title">'
            f'{_esc(str(block.get("market") or ""))}</div>'
            f'<table class="split-table"><thead><tr><th>\u516c\u53f8</th><th>\u521d\u76d8 \u2192 \u5373\u65f6</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>'
        )
    if not parts:
        return ""
    body = f'<div class="split-cards">{"".join(parts)}</div>'
    return (
        f'<div class="kind-split" data-split-for="{_esc(uid)}">\u76d8\u53e3\u5206\u6b67</div>'
        f'<template id="{_esc(uid)}">{body}</template>'
    )


def _lean_cell(row: dict) -> str:
    """倾向列：模型读球队资料的亚盘赢盘倾向 + 大小球倾向（主行/次行）+ 盘口异动的 lean（末行）。"""
    parts: list[str] = []
    try:
        read = json.loads(str(row.get("team_read") or "") or "{}")
    except (json.JSONDecodeError, TypeError):
        read = {}
    if isinstance(read, dict):
        read = normalize_team_read(read)
    tendency = str(read.get("tendency") or "").strip()
    if tendency:
        try:
            confidence = float(read.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        conf = f" {confidence * 100:.0f}%" if confidence > 0 else ""
        parts.append(f'<div class="model-lean">模型 {_esc(tendency)}{_esc(conf)}</div>')
        reason = str(read.get("reason") or "").strip()
        if reason:
            parts.append(
                f'<div class="model-reason" title="{_esc(reason)}">{_esc(reason)}</div>'
            )
    over_tendency = str(read.get("over_tendency") or "").strip()
    try:
        over_confidence = float(read.get("over_confidence") or 0.0)
    except (TypeError, ValueError):
        over_confidence = 0.0
    if over_tendency and over_confidence > 0:
        over_conf = f" {over_confidence * 100:.0f}%" if over_confidence > 0 else ""
        parts.append(f'<div class="model-lean">模型 {over_tendency}{over_conf}</div>')
        over_reason = str(read.get("over_reason") or "").strip()
        if over_reason:
            parts.append(
                f'<div class="model-reason" title="{_esc(over_reason)}">{_esc(over_reason)}</div>'
            )
    lean = str(row.get("lean") or "").split("（")[0].strip()
    if lean:
        # 老库的 lean 直接存了球队名（如「博德闪耀」），新库才存「主队」「客队」，
        # 后者才需要括注对应队名，前者本身已经是名字，避免叠成「博德闪耀（博德闪耀）」。
        home = str(row.get("home_team") or "").strip()
        away = str(row.get("away_team") or "").strip()
        if lean.startswith("主"):
            note = f"{lean}（{home}）"
        elif lean.startswith("客"):
            note = f"{lean}（{away}）"
        else:
            note = lean
        parts.append(f'<div class="lean-note">盘口 {_esc(note)}</div>')
    return "".join(parts) or "—"


def _kind_muted(row: dict) -> bool:
    """kind 升盘/降盘: when |line_change| < 0.5, strip the colored background."""
    kind = str(row.get("kind") or "")
    if kind not in ("升盘", "降盘"):
        return False
    try:
        lc = float(row.get("line_change") or 0)
    except (ValueError, TypeError):
        lc = 0.0
    return abs(lc) < 0.5



def _line_delta_html(row: dict) -> str:
    d = str(row.get("direction") or "").strip()
    try:
        lc = float(row.get("line_change") or 0)
    except (ValueError, TypeError):
        lc = 0.0
    # lc==0: 无让球线变化(如水位摆动) → 原样显示 direction，不加数字，不高亮
    if lc == 0:
        return f'<div class="line-delta">{_esc(d)}</div>' if d else ""
    if lc > 0:
        label = f"升盘 +{abs(lc):g}"
        cls = "delta-hot" if abs(lc) >= 0.5 else ""
    else:
        label = f"降盘 {abs(lc):g}"
        cls = "drop-big" if abs(lc) >= 0.5 else ""
    cls_attr = f" {cls}" if cls else ""
    return f'<div class="line-delta{cls_attr}">{_esc(label)}</div>'


def _total_goals_parts(payload_json: str) -> tuple[str, str, str]:
    """Return (initial, current, delta) for total-goals line."""
    try:
        data = json.loads(payload_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return ("无数据", "无数据", "")
    total = (data.get("odds_movement") or {}).get("total") or {}
    if not total.get("available"):
        return ("无市场", "无市场", "")
    init = total.get("initial") or {}
    curr = total.get("current") or {}

    def fmt(side: dict) -> str:
        line = str(side.get("line_text") or "").strip()
        over = side.get("over_water")
        under = side.get("under_water")
        if over is not None and under is not None and (over or under):
            return f"{line} {over:.2f}/{under:.2f}".strip()
        return line

    i_text = fmt(init) or "无数据"
    c_text = fmt(curr) or "无数据"
    delta = ""
    if init.get("line") is not None and curr.get("line") is not None:
        diff = round(float(curr["line"]) - float(init["line"]), 2)
        if diff > 0:
            delta = f"升盘 +{diff}"
        elif diff < 0:
            delta = f"降盘 {diff}"
    return (i_text, c_text, delta)


def _total_goals_html(payload_json: str) -> str:
    i, c, d = _total_goals_parts(payload_json)
    abs_diff = 0.0
    if d:
        try:
            num_part = d.replace("升盘 ", "").replace("降盘 ", "").replace("+", "")
            abs_diff = abs(float(num_part))
        except ValueError:
            pass
    is_drop = d.startswith("降盘")
    hot = abs_diff >= 0.5
    delta_html = ""
    if d:
        tag_cls = ("drop-big" if is_drop else "delta-hot") if hot else ""
        delta_html = f'<div class="line-delta {tag_cls}">{_esc(d)}</div>'
    return (
        f'<div class="line-pair"><span class="k">{_esc(chr(21021))}</span>{_esc(i)}</div>'
        f'<div class="line-pair"><span class="k">{_esc(chr(21363))}</span>{_esc(c)}</div>'
        + delta_html
    )


_FETCH_BLOCK = \
"""    <svg class="action-icon-sprite" aria-hidden="true">
      <symbol id="icon-download" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3v12m0 0-4-4m4 4 4-4M5 21h14"/>
      </symbol>
      <symbol id="icon-refresh" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 0 0-15.3-6.3L3 8m0 0V3m0 5h5M3 12a9 9 0 0 0 15.3 6.3L21 16m0 0v5m0-5h-5"/>
      </symbol>
      <symbol id="icon-sparkles" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m12 3 1.9 5.6L19 10.5l-5.1 1.9L12 18l-1.9-5.6L5 10.5l5.1-1.9L12 3Z"/>
        <path d="m19 17 .7 2.3L22 20l-2.3.7L19 23l-.7-2.3L16 20l2.3-.7L19 17Z"/>
      </symbol>
      <symbol id="icon-search" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="7"/>
        <path d="m20 20-4-4"/>
      </symbol>
      <symbol id="icon-file-text" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>
        <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>
      </symbol>
      <symbol id="icon-list-checks" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m3 7 2 2 4-4m-6 12 2 2 4-4M13 6h8M13 12h8M13 18h8"/>
      </symbol>
      <symbol id="icon-plug" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22v-5M9 8V2M15 8V2M4 8h16"/>
        <path d="M5 8v4a7 7 0 0 0 14 0V8"/>
      </symbol>
      <symbol id="icon-upload" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 16V4m0 0L7 9m5-5 5 5M4 20h16"/>
      </symbol>
      <symbol id="icon-arrow-up-circle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <path d="M12 16V8m0 0L8 12m4-4 4 4"/>
      </symbol>
      <symbol id="icon-check-circle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <path d="m8 12 2.5 2.5L16 9"/>
      </symbol>
      <symbol id="icon-x" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m18 6-12 12M6 6l12 12"/>
      </symbol>
      <symbol id="icon-settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/>
        <path d="m19.4 15 .1.1a2 2 0 1 1-2.8 2.8l-.1-.1a2 2 0 0 0-3.4 1.4V19a2 2 0 1 1-4 0v-.2a2 2 0 0 0-3.4-1.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A2 2 0 0 0 3.6 11H3a2 2 0 1 1 0-4h.6a2 2 0 0 0 1.4-3.4l-.1-.1A2 2 0 1 1 7.7.7l.1.1A2 2 0 0 0 11.2 0H12a2 2 0 1 1 4 0v.6a2 2 0 0 0 3.4 1.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A2 2 0 0 0 20.8 8h.2a2 2 0 1 1 0 4h-.2a2 2 0 0 0-1.4 3.4Z"/>
      </symbol>
    </svg>
    <form class="fetch-bar query-toolbar" id="fetch-form">
      <label>抓取期号
        <input name="issue" placeholder="如 26120" style="width:120px">
      </label>
      <button type="submit" class="action-primary"><svg class="action-icon" aria-hidden="true"><use href="#icon-download"></use></svg>获取比赛</button>
      <button type="button" id="refresh-odds-btn" class="action-secondary"><svg class="action-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg>刷新盘口</button>
      <button type="button" id="ai-analyze-btn" class="action-ai" title="读 500 数据分析页球队资料，大模型给出亚盘赢盘倾向（只展示，不改推荐）"><svg class="action-icon" aria-hidden="true"><use href="#icon-sparkles"></use></svg>AI 分析</button>
      <span id="fetch-status" class="muted"></span>
      <span id="refresh-odds-status" class="muted"></span>
      <span id="ai-analyze-status" class="muted"></span>
      <div id="refresh-odds-progress" style="display:none;flex:1 0 100%;margin:4px 0 0;height:4px;background:#e5e7eb;border-radius:2px;overflow:hidden">
        <div id="refresh-odds-progress-bar" style="height:100%;width:0;background:#2563eb;transition:width .3s"></div>
      </div>
      <div id="ai-progress" style="display:none;flex:1 0 100%;margin:4px 0 0;height:4px;background:#e5e7eb;border-radius:2px;overflow:hidden">
        <div id="ai-progress-bar" style="height:100%;width:0;background:#2563eb;transition:width .3s"></div>
      </div>
    </form>
    <script>
    const fetchForm = document.getElementById("fetch-form");
    const statusEl = document.getElementById("fetch-status");
    const refreshBtn = document.getElementById("refresh-odds-btn");
    const refreshStatus = document.getElementById("refresh-odds-status");
    const refreshProgress = document.getElementById("refresh-odds-progress");
    const refreshProgressBar = document.getElementById("refresh-odds-progress-bar");
    function _renderRefreshProgress(s) {
      if (!s || !s.progress) {
        refreshProgress.style.display = "none";
        refreshProgressBar.style.width = "0";
        return;
      }
      refreshProgress.style.display = "block";
      const p = s.progress;
      const total = p.total || 0;
      const done = p.done || 0;
      const pct = total > 0 ? Math.round(done / total * 100) : 0;
      refreshProgressBar.style.width = pct + "%";
      if (p.phase === "aborted" && p.reason) {
        refreshStatus.textContent = "盘口刷新 " + s.issue + " 中 — 抓取中止: " + p.reason;
      } else if (p.phase === "done") {
        refreshStatus.textContent = "盘口刷新 " + s.issue + " 中 — 正在入库...";
        refreshProgressBar.style.width = "100%";
      } else {
        refreshStatus.textContent = "盘口刷新 " + s.issue + " 中 — 抓取 " + done + "/" + total + " (" + pct + "%)";
      }
    }
    if (refreshBtn) {
      refreshBtn.addEventListener("click", async () => {
        const issue = new URLSearchParams(new FormData(fetchForm)).get("issue") || "";
        if (!issue) { refreshStatus.textContent = "请先填写期号"; return; }
        refreshBtn.disabled = true;
        refreshStatus.textContent = "盘口刷新中...";
        refreshBtn.scrollIntoView({ block: "nearest" });
        try {
          const resp = await fetch("/refresh-odds", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue})});
          // busy = 已有任务在跑（可能是别的期号）：跟随它，不重复发抓取
          const started = await resp.json();
          if (started.status === "busy") {
            refreshStatus.textContent = "已有刷新任务在进行（" + started.issue + "），等待完成...";
          }
        } catch (e) {
          refreshBtn.disabled = false;
          refreshStatus.textContent = "请求失败：" + e;
          return;
        }
        const poll = setInterval(async () => {
          try {
            const r = await fetch("/refresh-odds-status");
            const s = await r.json();
            if (s.running) { refreshStatus.textContent = "盘口刷新 " + s.issue + " 中..."; _renderRefreshProgress(s); return; }
            clearInterval(poll);
            refreshBtn.disabled = false;
            refreshStatus.textContent = s.error || s.result || "完成";
            _renderRefreshProgress(null);
            refreshStatus.scrollIntoView({ block: "nearest" });
            if (s.result && !s.error) location.href = "?issue=" + encodeURIComponent(issue);
          } catch (e) { /* 轮询瞬时失败，下个 tick 重试 */ }
        }, 2000);
      });
    }
    const aiProgress = document.getElementById("ai-progress");
    const aiProgressBar = document.getElementById("ai-progress-bar");
    function _renderAiProgress(s) {
      if (!s || !s.progress) {
        aiProgress.style.display = "none";
        aiProgressBar.style.width = "0";
        return;
      }
      aiProgress.style.display = "block";
      const p = s.progress;
      const total = p.total || 0;
      const done = p.done || 0;
      const pct = total > 0 ? Math.round(done / total * 100) : 0;
      aiProgressBar.style.width = pct + "%";
      if (p.phase === "llm") {
        aiStatus.textContent = "AI 分析 " + s.issue + " 中 — 模型分析 " + done + "/" + total + " (" + pct + "%)";
      } else if (p.phase === "aborted" && p.reason) {
        aiStatus.textContent = "AI 分析 " + s.issue + " 中 — 抓取中止: " + p.reason;
      } else if (p.phase === "done") {
        aiStatus.textContent = "AI 分析 " + s.issue + " 中 — 正在写入结果...";
        aiProgressBar.style.width = "100%";
      } else {
        aiStatus.textContent = "AI 分析 " + s.issue + " 中 — 抓取 " + done + "/" + total + " (" + pct + "%)";
      }
    }
    const aiBtn = document.getElementById("ai-analyze-btn");
    const aiStatus = document.getElementById("ai-analyze-status");
    if (aiBtn) {
      aiBtn.addEventListener("click", async () => {
        const issue = new URLSearchParams(new FormData(fetchForm)).get("issue") || "";
        if (!issue) { aiStatus.textContent = "请先填写期号"; return; }
        aiBtn.disabled = true;
        aiStatus.textContent = "AI 分析中...";
        aiBtn.scrollIntoView({ block: "nearest" });
        try {
          const resp = await fetch("/ai-analyze", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue})});
          const started = await resp.json();
          if (started.status === "busy") {
            aiStatus.textContent = "已有 AI 分析任务在进行（" + started.issue + "），等待完成...";
          }
        } catch (e) {
          aiBtn.disabled = false;
          aiStatus.textContent = "请求失败：" + e;
          return;
        }
        const poll = setInterval(async () => {
          try {
            const r = await fetch("/ai-analyze-status");
            const s = await r.json();
            if (s.running) {
              aiStatus.textContent = "AI 分析 " + s.issue + " 中...";
              _renderAiProgress(s);
              return;
            }
            clearInterval(poll);
            aiBtn.disabled = false;
            aiStatus.textContent = s.error || s.result || "完成";
            _renderAiProgress(null);
            aiStatus.scrollIntoView({ block: "nearest" });
            if (s.result && !s.error) location.href = "?issue=" + encodeURIComponent(issue);
          } catch (e) { /* 轮询瞬时失败，下一 tick 重试 */ }
        }, 2000);
      });
    }
    if (fetchForm) {
      fetchForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const issue = new URLSearchParams(new FormData(fetchForm)).get("issue") || "";
        if (!issue) return;
        statusEl.textContent = "抓取中...";
        await fetch("/fetch", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue})});
        const poll = setInterval(async () => {
          const r = await fetch("/fetch-status");
          const s = await r.json();
          if (s.running) { statusEl.textContent = "抓取 " + s.issue + " 中..."; }
          else {
            clearInterval(poll);
            statusEl.textContent = s.error || s.result || "完成";
            if (s.result && !s.error) location.href = "?issue=" + encodeURIComponent(issue);
          }
        }, 2000);
      });
    }
    </script>
    <dialog id="settings-dialog">
      <form id="settings-form">
        <div class="settings-head">
          <strong>AI 模型设置</strong>
          <button type="button" id="settings-close" class="icon-btn" title="关闭" aria-label="关闭"><svg class="settings-icon" aria-hidden="true"><use href="#icon-x"></use></svg></button>
        </div>
        <div class="settings-grid">
          <fieldset>
            <legend>主接入点</legend>
            <label>API Key
              <input id="settings-primary-key" type="password" autocomplete="off" placeholder="留空保持现有 Key">
            </label>
            <label>URL
              <input id="settings-primary-url" type="url" placeholder="https://api.openai.com/v1">
            </label>
            <label>模型名
              <input id="settings-primary-model" type="text" placeholder="模型名称">
            </label>
            <label class="settings-clear"><input id="settings-primary-clear" type="checkbox"> 清空已保存 Key</label>
            <div id="settings-primary-status" class="muted"></div>
            <div class="settings-test-row">
              <button type="button" class="settings-test settings-button" data-name="primary"><svg class="settings-icon" aria-hidden="true"><use href="#icon-plug"></use></svg>测试连接</button>
              <span id="settings-primary-test-status" class="muted"></span>
            </div>
          </fieldset>
          <fieldset>
            <legend>备用接入点</legend>
            <label>API Key
              <input id="settings-fallback-key" type="password" autocomplete="off" placeholder="留空保持现有 Key">
            </label>
            <label>URL
              <input id="settings-fallback-url" type="url" placeholder="https://api.openai.com/v1">
            </label>
            <label>模型名
              <input id="settings-fallback-model" type="text" placeholder="模型名称">
            </label>
            <label class="settings-clear"><input id="settings-fallback-clear" type="checkbox"> 清空已保存 Key</label>
            <div id="settings-fallback-status" class="muted"></div>
            <div class="settings-test-row">
              <button type="button" class="settings-test settings-button" data-name="fallback"><svg class="settings-icon" aria-hidden="true"><use href="#icon-plug"></use></svg>测试连接</button>
              <span id="settings-fallback-test-status" class="muted"></span>
            </div>
          </fieldset>
        </div>
        <div id="settings-error" class="settings-error"></div>
        <div class="settings-backup">
          <strong>本地数据</strong>
          <span class="muted">备份包含 SQLite 和 AI 设置，不包含可再生成的缓存与报告。</span>
          <div class="settings-backup-actions">
            <button type="button" id="backup-download" class="settings-button"><svg class="settings-icon" aria-hidden="true"><use href="#icon-download"></use></svg>下载备份</button>
            <label class="backup-restore-label settings-button"><svg class="settings-icon" aria-hidden="true"><use href="#icon-upload"></use></svg>恢复备份
              <input type="file" id="backup-file" accept=".zip,application/zip" hidden>
            </label>
          </div>
          <div id="backup-status" class="muted"></div>
        </div>
        <div class="settings-backup">
          <strong>软件升级</strong>
          <span class="muted">当前版本：v{_esc(__version__)}。升级包来自 GitHub Releases，下载后会校验 SHA-256。</span>
          <div class="settings-backup-actions">
            <button type="button" id="update-check" class="settings-button"><svg class="settings-icon" aria-hidden="true"><use href="#icon-refresh"></use></svg>检查更新</button>
            <button type="button" id="update-install" class="settings-button settings-button-primary" disabled><svg class="settings-icon" aria-hidden="true"><use href="#icon-arrow-up-circle"></use></svg>一键升级</button>
          </div>
          <div id="update-status" class="muted"></div>
          <div id="update-progress-wrap" class="update-progress-wrap" hidden>
            <progress id="update-progress" max="100" value="0"></progress>
            <span id="update-progress-label" class="muted"></span>
          </div>
          <div id="update-details" class="update-details" hidden>
            <div id="update-versions"></div>
            <div id="update-notes"></div>
            <div id="update-diagnostics" class="muted"></div>
            <a id="update-release" href="https://github.com/jackybee6933-code/zucaikan/releases" target="_blank" rel="noopener">打开 GitHub Releases</a>
          </div>
        </div>
        <div class="settings-actions">
          <button type="button" id="settings-cancel" class="settings-button"><svg class="settings-icon" aria-hidden="true"><use href="#icon-x"></use></svg>取消</button>
          <button type="submit" id="settings-save" class="settings-button settings-button-primary"><svg class="settings-icon" aria-hidden="true"><use href="#icon-check-circle"></use></svg>保存设置</button>
        </div>
      </form>
    </dialog>
    <script>
    (function () {
      const dialog = document.getElementById("settings-dialog");
      const openBtn = document.getElementById("settings-btn");
      if (!dialog || !openBtn) return;
      const error = document.getElementById("settings-error");
      const saveBtn = document.getElementById("settings-save");
      const testButtons = document.querySelectorAll(".settings-test");
      function field(name) { return document.getElementById(name); }
      function fill(name, data) {
        field("settings-" + name + "-url").value = data.base_url || "";
        field("settings-" + name + "-model").value = data.model || "";
        field("settings-" + name + "-key").value = "";
        field("settings-" + name + "-clear").checked = false;
        field("settings-" + name + "-status").textContent =
          data.api_key_configured ? ("已配置 " + (data.api_key_hint || "")) : "未配置";
        const testStatus = field("settings-" + name + "-test-status");
        testStatus.textContent = "";
        testStatus.className = "muted";
      }
      async function load() {
        error.textContent = "";
        try {
          const response = await fetch("/settings");
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "读取设置失败");
          fill("primary", data.primary || {});
          fill("fallback", data.fallback || {});
        } catch (e) {
          error.textContent = e.message || String(e);
        }
      }
      function endpoint(name) {
        return {
          api_key: field("settings-" + name + "-key").value,
          base_url: field("settings-" + name + "-url").value,
          model: field("settings-" + name + "-model").value,
          clear_api_key: field("settings-" + name + "-clear").checked
        };
      }
      testButtons.forEach(function (button) {
        button.addEventListener("click", async function () {
          const name = button.getAttribute("data-name");
          const status = field("settings-" + name + "-test-status");
          button.disabled = true;
          status.textContent = "测试中...";
          try {
            const response = await fetch("/settings/test", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({name: name, [name]: endpoint(name)})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "测试失败");
            status.textContent = data.ok ? "✓ " + data.message : "× " + data.message;
            status.className = data.ok ? "settings-test-status ok" : "settings-test-status fail";
          } catch (e) {
            status.textContent = "× " + (e.message || String(e));
            status.className = "settings-test-status fail";
          } finally {
            button.disabled = false;
          }
        });
      });
      openBtn.addEventListener("click", async function () {
        dialog.showModal();
        await load();
      });
      document.getElementById("settings-close").addEventListener("click", () => dialog.close());
      document.getElementById("settings-cancel").addEventListener("click", () => dialog.close());
      document.getElementById("settings-form").addEventListener("submit", async function (event) {
        event.preventDefault();
        error.textContent = "";
        saveBtn.disabled = true;
        try {
          const response = await fetch("/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              primary: endpoint("primary"),
              fallback: endpoint("fallback"),
            })
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "保存设置失败");
          fill("primary", data.primary || {});
          fill("fallback", data.fallback || {});
          error.textContent = "设置已保存";
        } catch (e) {
          error.textContent = e.message || String(e);
        } finally {
          saveBtn.disabled = false;
        }
      });
      const backupStatus = document.getElementById("backup-status");
      const updateStatus = document.getElementById("update-status");
      const updateInstall = document.getElementById("update-install");
      const updateCheck = document.getElementById("update-check");
      const updateDetails = document.getElementById("update-details");
      const updateVersions = document.getElementById("update-versions");
      const updateNotes = document.getElementById("update-notes");
      const updateDiagnostics = document.getElementById("update-diagnostics");
      const updateRelease = document.getElementById("update-release");
      const updateProgressWrap = document.getElementById("update-progress-wrap");
      const updateProgress = document.getElementById("update-progress");
      const updateProgressLabel = document.getElementById("update-progress-label");
      let updateInfo = null;
      let updatePollTimer = null;
      function stopUpdatePolling() {
        if (updatePollTimer) clearTimeout(updatePollTimer);
        updatePollTimer = null;
      }
      function pollUpdateStatus(targetVersion) {
        stopUpdatePolling();
        updatePollTimer = setTimeout(async function poll() {
          try {
            const response = await fetch("/update/install-status");
            const state = await response.json();
            if (!response.ok) throw new Error(state.error || "读取升级进度失败");
            if (state.phase === "download" && state.total > 0) {
              const pct = Math.min(100, Math.floor(state.downloaded * 100 / state.total));
              updateProgress.removeAttribute("value");
              updateProgress.value = pct;
              updateProgress.setAttribute("value", pct);
              updateProgressLabel.textContent =
                `${pct}%　${state.downloaded.toLocaleString()} / ${state.total.toLocaleString()} 字节`;
              updateStatus.textContent = `正在下载 v${targetVersion}...`;
            } else if (state.phase === "download") {
              updateProgress.removeAttribute("value");
              updateProgressLabel.textContent = "正在下载...";
              updateStatus.textContent = `正在下载 v${targetVersion}...`;
            } else if (state.phase === "verify") {
              updateProgress.value = 100;
              updateProgress.setAttribute("value", "100");
              updateProgressLabel.textContent = "下载完成，正在校验 SHA-256...";
              updateStatus.textContent = "正在验证升级包完整性...";
            } else if (state.phase === "ready") {
              updateProgress.value = 100;
              updateProgress.setAttribute("value", "100");
              updateProgressLabel.textContent = "SHA-256 校验通过，正在准备安装...";
              updateStatus.textContent = "升级包验证完成";
            } else if (state.phase === "installing") {
              updateProgress.value = 100;
              updateProgress.setAttribute("value", "100");
              updateProgressLabel.textContent = "校验通过，正在启动更新器...";
              updateStatus.textContent = "升级器即将替换程序并重启";
            } else if (state.phase === "complete") {
              stopUpdatePolling();
              updateProgressWrap.hidden = true;
              updateStatus.textContent = state.result || "升级已启动";
              setTimeout(() => location.reload(), 8000);
              return;
            } else if (state.phase === "error") {
              stopUpdatePolling();
              updateProgressWrap.hidden = true;
              updateStatus.textContent = state.error || "升级失败";
              updateInstall.disabled = false;
              return;
            } else if (state.running) {
              updateProgress.removeAttribute("value");
              updateProgressLabel.textContent = "正在准备升级...";
            } else {
              stopUpdatePolling();
              updateProgressWrap.hidden = true;
              updateInstall.disabled = false;
              return;
            }
            if (state.running) updatePollTimer = setTimeout(poll, 500);
          } catch (e) {
            stopUpdatePolling();
            updateProgressWrap.hidden = true;
            updateStatus.textContent = e.message || String(e);
            updateInstall.disabled = false;
          }
        }, 250);
      }
      async function checkForUpdate() {
        function formatElapsedSeconds(milliseconds) {
          return (Number(milliseconds || 0) / 1000).toFixed(2) + " 秒";
        }
        updateCheck.disabled = true;
        updateInstall.disabled = true;
        updateStatus.textContent = "正在检查更新清单...";
        updateDetails.hidden = true;
        try {
          const response = await fetch("/update/check");
          const data = await response.json();
          if (!response.ok) {
            updateStatus.textContent = (data.error || "检查更新失败") +
              "。可检查网络/代理后重试，或打开 GitHub Releases 手动查看。";
            updateDiagnostics.textContent = "清单：" + (data.manifest_url || "未取得") +
              "；耗时 " + formatElapsedSeconds(data.elapsed_ms);
            updateDetails.hidden = false;
            updateRelease.href = "https://github.com/jackybee6933-code/zucaikan/releases";
            return;
          }
          updateInfo = data;
          const offline = Boolean(data.cached);
          updateStatus.textContent = offline
            ? `GitHub 暂不可达，以下为缓存清单（${data.checked_at || "时间未知"}），仅供查看。`
            : data.update_available
              ? `发现新版本 v${data.latest_version}`
              : `当前已是最新版本 v${data.current_version}`;
          updateVersions.textContent =
            `当前版本：v${data.current_version}　最新版本：v${data.latest_version}`;
          updateNotes.textContent = data.notes ? "更新说明：" + data.notes : "暂无更新说明";
          updateDiagnostics.textContent =
            `清单：${data.manifest_url || "未知"}；检查耗时 ${formatElapsedSeconds(data.elapsed_ms)}` +
            (offline ? "；离线缓存" : "");
          const releaseUrl = String(data.release_url || "");
          updateRelease.href = releaseUrl.indexOf("https://github.com/jackybee6933-code/zucaikan/releases/") === 0
            ? releaseUrl
            : "https://github.com/jackybee6933-code/zucaikan/releases";
          updateRelease.textContent = `打开 v${data.latest_version} 发布页`;
          updateDetails.hidden = false;
          updateInstall.disabled = !data.update_available || offline;
        } catch (e) {
          updateStatus.textContent = e instanceof TypeError
            ? "无法连接本地更新服务，请刷新页面后重试。"
            : (e.message || String(e)) + "。可重试或打开 GitHub Releases。";
          updateRelease.href = "https://github.com/jackybee6933-code/zucaikan/releases";
          updateDetails.hidden = false;
        } finally {
          updateCheck.disabled = false;
        }
      }
      updateCheck.addEventListener("click", checkForUpdate);
      updateInstall.addEventListener("click", async function () {
        if (!updateInfo?.update_available) return;
        const targetVersion = `v${updateInfo.latest_version}`;
        if (!window.confirm(`将下载并安装 ${targetVersion}，校验通过后会关闭并重启程序。是否继续？`)) return;
        this.disabled = true;
        updateProgressWrap.hidden = false;
        updateProgress.removeAttribute("value");
        updateProgressLabel.textContent = "正在准备升级...";
        updateStatus.textContent = `正在下载并校验 ${targetVersion}，请保持程序运行...`;
        try {
          const response = await fetch("/update/install", {method: "POST"});
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || "升级失败");
          if (data.busy || data.started) {
            pollUpdateStatus(targetVersion);
            return;
          }
          updateStatus.textContent = data.message || "已是最新版本";
          updateProgressWrap.hidden = true;
          this.disabled = false;
        } catch (e) {
          updateStatus.textContent = e.message || String(e);
          updateProgressWrap.hidden = true;
          this.disabled = false;
        }
      });
      document.getElementById("backup-download").addEventListener("click", async function () {
        this.disabled = true;
        backupStatus.textContent = "正在生成备份...";
        try {
          const response = await fetch("/backup");
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || "备份失败");
          const bytes = Uint8Array.from(atob(payload.data), c => c.charCodeAt(0));
          const blob = new Blob([bytes], {type: "application/zip"});
          const link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = payload.filename || "football_analysis_backup.zip";
          link.click();
          URL.revokeObjectURL(link.href);
          backupStatus.textContent = "备份已下载";
        } catch (e) {
          backupStatus.textContent = e.message || String(e);
        } finally {
          this.disabled = false;
        }
      });
      document.getElementById("backup-file").addEventListener("change", async function () {
        const file = this.files && this.files[0];
        if (!file) return;
        if (!confirm("恢复会替换当前数据库和 AI 设置，恢复前系统会自动生成安全备份。继续吗？")) {
          this.value = "";
          return;
        }
        backupStatus.textContent = "正在校验并恢复...";
        try {
          const bytes = new Uint8Array(await file.arrayBuffer());
          let binary = "";
          for (let i = 0; i < bytes.length; i += 0x8000) {
            binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
          }
          const response = await fetch("/backup/restore", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({data: btoa(binary)})
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || "恢复失败");
          backupStatus.textContent = "恢复完成，正在刷新页面...";
          setTimeout(() => location.reload(), 500);
        } catch (e) {
          backupStatus.textContent = e.message || String(e);
        } finally {
          this.value = "";
        }
      });
    })();
    </script>
"""


_BET_BLOCK = """
<div id="bet-tip" hidden></div>
<div id="bet-flash" class="bet-flash" hidden></div>
<dialog id="bet-dialog">
  <form id="bet-form">
    <div class="bet-dialog-head">
      <div>
        <strong id="bet-title"></strong>
        <div id="bet-meta" class="muted"></div>
      </div>
      <button type="button" id="bet-close" class="icon-btn" title="关闭">×</button>
    </div>
    <div id="bet-existing"></div>
    <div id="bet-markets"></div>
    <label class="bet-stake">投注积分
      <input id="bet-stake-input" type="number" min="0.01" step="0.01" value="10">
    </label>
    <div id="bet-preview" class="bet-preview"></div>
    <div id="bet-error" class="bet-error"></div>
    <div class="bet-dialog-actions">
      <button type="button" id="bet-cancel">取消</button>
      <button type="button" id="bet-delete" hidden>删除注单</button>
      <button type="submit" id="bet-submit">确认下注</button>
    </div>
  </form>
</dialog>
<script>
(function () {
  var dialog = document.getElementById("bet-dialog");
  if (!dialog) return;
  var state = {options: null, bet: null};
  var titleEl = document.getElementById("bet-title");
  var metaEl = document.getElementById("bet-meta");
  var existingEl = document.getElementById("bet-existing");
  var marketsEl = document.getElementById("bet-markets");
  var stakeEl = document.getElementById("bet-stake-input");
  var previewEl = document.getElementById("bet-preview");
  var errorEl = document.getElementById("bet-error");
  var submitEl = document.getElementById("bet-submit");
  var deleteEl = document.getElementById("bet-delete");
  var tipEl = document.getElementById("bet-tip");
  var flashEl = document.getElementById("bet-flash");

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch];
    });
  }
  function money(value) {
    var n = Number(value || 0);
    return n.toFixed(2);
  }
  function selectedMarket() {
    var el = marketsEl.querySelector('input[name="bet-market"]:checked');
    return el ? el.value : "";
  }
  function selectedSelection(market) {
    if (!market) return "";
    var el = marketsEl.querySelector('input[name="bet-selection-' + market + '"]:checked');
    return el ? el.value : "";
  }
  function setError(text) {
    errorEl.textContent = text || "";
  }
  function flashAndReload(text, betId) {
    try {
      sessionStorage.setItem("bet-flash", JSON.stringify({text: text, id: betId || 0}));
    } catch (e) {}
    location.reload();
  }
  function showSavedFlash() {
    try {
      var raw = sessionStorage.getItem("bet-flash");
      if (!raw) return;
      var msg = JSON.parse(raw);
      if (flashEl) {
        flashEl.textContent = msg.text || "";
        flashEl.hidden = false;
        setTimeout(function () { flashEl.hidden = true; }, 4000);
      }
      if (msg.id) {
        var row = document.getElementById("bet-row-" + Number(msg.id));
        if (row) {
          row.classList.add("bet-highlight");
          row.scrollIntoView({block: "center"});
          setTimeout(function () { row.classList.remove("bet-highlight"); }, 2400);
        }
      } else {
        var section = document.getElementById("bets-section");
        if (section) section.scrollIntoView({block: "start"});
      }
      sessionStorage.removeItem("bet-flash");
    } catch (e) {}
  }
  function hideTip() {
    if (tipEl) tipEl.hidden = true;
  }
  function showTip(el) {
    if (!tipEl) return;
    var text = el.getAttribute("data-bet-tip") || "";
    if (!text) { hideTip(); return; }
    tipEl.textContent = text;
    tipEl.hidden = false;
    var box = el.getBoundingClientRect();
    var left = Math.max(8, Math.min(box.left, window.innerWidth - tipEl.offsetWidth - 8));
    var top = box.bottom + 6;
    if (top + tipEl.offsetHeight > window.innerHeight - 8) {
      top = Math.max(8, box.top - tipEl.offsetHeight - 6);
    }
    tipEl.style.left = left + "px";
    tipEl.style.top = top + "px";
  }
  document.querySelectorAll(".bet-action-group[data-bet-tip]").forEach(function (el) {
    el.addEventListener("mouseenter", function () { showTip(el); });
    el.addEventListener("focusin", function () { showTip(el); });
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("focusout", hideTip);
  });
  window.addEventListener("scroll", hideTip, {passive: true});
  function renderExisting() {
    var existing = state.options.existing || [];
    if (!existing.length) { existingEl.innerHTML = ""; return; }
    var html = '<div class="bet-existing-title">已有注单</div>';
    existing.forEach(function (bet) {
      html += '<div class="bet-existing-row">' +
        '<span>' + esc(bet.market_label) + ' · ' + esc(bet.selection_label) +
        ' · ' + money(bet.stake) + ' 积分 · ' + esc(bet.status_label) + '</span>' +
        '<span class="bet-existing-actions">' +
        (bet.can_edit ? '<button type="button" class="bet-inline-edit" data-bet-id="' + Number(bet.id) + '">修改</button>' : '') +
        (bet.can_delete ? '<button type="button" class="bet-inline-delete" data-bet-id="' + Number(bet.id) + '">删除</button>' : '') +
        '</span></div>';
    });
    existingEl.innerHTML = html;
    existingEl.querySelectorAll(".bet-inline-edit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = Number(btn.getAttribute("data-bet-id"));
        state.bet = (state.options.existing || []).find(function (b) { return Number(b.id) === id; }) || null;
        render();
      });
    });
    existingEl.querySelectorAll(".bet-inline-delete").forEach(function (btn) {
      btn.addEventListener("click", function () {
        deleteBet(Number(btn.getAttribute("data-bet-id")));
      });
    });
  }
  function renderMarkets() {
    var markets = state.options.markets || {};
    var existingMarkets = {};
    (state.options.existing || []).forEach(function (bet) { existingMarkets[bet.market] = true; });
    var keys = Object.keys(markets);
    var edit = state.bet;
    var available = keys.filter(function (key) {
      return edit ? key === edit.market : !existingMarkets[key];
    });
    var defaultMarket = edit ? edit.market : (available[0] || "");
    var html = "";
    keys.forEach(function (key) {
      var info = markets[key];
      var rec = info.recommendation || {};
      var disabled = edit ? key !== edit.market : !!existingMarkets[key];
      var checked = key === defaultMarket;
      var defaultSelection = edit && edit.market === key
        ? edit.selection
        : (rec.default || "");
      var marketLabel = esc(info.label) + " · " + esc(info.line_text);
      var recText = "模型 " + esc(rec.action || "无推荐") +
        (rec.side_text ? " · " + esc(rec.side_text) : "");
      html += '<div class="bet-market' + (checked ? " active" : "") + (disabled ? " disabled" : "") + '">' +
        '<label class="bet-market-head"><input type="radio" name="bet-market" value="' + esc(key) + '"' +
        (checked ? " checked" : "") + (disabled ? " disabled" : "") + '> ' + marketLabel + '</label>' +
        '<div class="bet-rec">' + recText + '</div>' +
        '<div class="bet-selections">';
      (info.selections || []).forEach(function (item) {
        var selChecked = defaultSelection === item.value;
        html += '<label class="bet-selection"><input type="radio" name="bet-selection-' + esc(key) +
          '" value="' + esc(item.value) + '"' + (selChecked ? " checked" : "") +
          (disabled ? " disabled" : "") + '> ' + esc(item.label) +
          ' <span class="muted">水位 ' + money(item.water) + '</span></label>';
      });
      html += '</div></div>';
    });
    marketsEl.innerHTML = html || '<div class="muted">当前没有可用盘口。</div>';
    marketsEl.querySelectorAll('input[name="bet-market"]').forEach(function (el) {
      el.addEventListener("change", function () {
        marketsEl.querySelectorAll(".bet-market").forEach(function (box) {
          box.classList.remove("active");
        });
        var box = el.closest(".bet-market");
        if (box) box.classList.add("active");
        updatePreview();
      });
    });
    marketsEl.querySelectorAll('input[type="radio"]').forEach(function (el) {
      el.addEventListener("change", updatePreview);
    });
    submitEl.disabled = !!state.options.locked || !defaultMarket;
    updatePreview();
  }
  function updatePreview() {
    var market = selectedMarket();
    var selection = selectedSelection(market);
    var info = (state.options.markets || {})[market];
    var item = info ? (info.selections || []).find(function (x) { return x.value === selection; }) : null;
    var stake = Number(stakeEl.value || 0);
    if (!item || !(stake > 0)) {
      previewEl.textContent = "";
      return;
    }
    var water = Number(item.water || 0);
    previewEl.textContent = "当前水位 " + money(water) +
      "：全赢 +" + money(stake * water) +
      "，赢半 +" + money(stake * water / 2) +
      "，输半 -" + money(stake / 2) +
      "，全输 -" + money(stake);
  }
  function render() {
    var match = state.options.match || {};
    titleEl.textContent = state.options.issue + " 第" + state.options.match_no + "场 " +
      (match.home_team || "") + " vs " + (match.away_team || "");
    metaEl.textContent = [match.league, match.match_time, "开赛 " + (state.options.kickoff_at || "未知")]
      .filter(Boolean).join(" · ");
    setError(state.options.locked ? state.options.locked_reason : "");
    renderExisting();
    renderMarkets();
    if (state.bet) stakeEl.value = money(state.bet.stake);
    deleteEl.hidden = !state.bet || !state.bet.can_delete;
    if (state.bet) submitEl.textContent = "保存修改";
    else submitEl.textContent = "确认下注";
  }
  async function openBet(issue, matchNo, betId) {
    setError("");
    try {
      var resp = await fetch("/bet-options?issue=" + encodeURIComponent(issue) +
        "&match_no=" + encodeURIComponent(matchNo));
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "读取盘口失败");
      state.options = data;
      state.bet = betId
        ? (data.existing || []).find(function (b) { return Number(b.id) === Number(betId); }) || null
        : null;
      stakeEl.value = state.bet ? money(state.bet.stake) : "10";
      dialog.showModal();
      render();
    } catch (err) {
      setError(err.message || String(err));
      if (!dialog.open) dialog.showModal();
    }
  }
  async function deleteBet(id) {
    if (!confirm("确定删除这张注单？")) return;
    setError("");
    try {
      var resp = await fetch("/bets/delete", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: id})
      });
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "删除失败");
      flashAndReload("注单已删除", 0);
    } catch (err) {
      setError(err.message || String(err));
    }
  }
  document.querySelectorAll(".bet-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openBet(btn.getAttribute("data-issue"), btn.getAttribute("data-match-no"), null);
    });
  });
  document.querySelectorAll(".bet-edit").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openBet(btn.getAttribute("data-issue"), btn.getAttribute("data-match-no"),
        Number(btn.getAttribute("data-bet-id")));
    });
  });
  document.querySelectorAll(".bet-delete").forEach(function (btn) {
    btn.addEventListener("click", function () {
      deleteBet(Number(btn.getAttribute("data-bet-id")));
    });
  });
  document.getElementById("bet-close").addEventListener("click", function () { dialog.close(); });
  document.getElementById("bet-cancel").addEventListener("click", function () { dialog.close(); });
  deleteEl.addEventListener("click", function () {
    if (state.bet) deleteBet(state.bet.id);
  });
  stakeEl.addEventListener("input", updatePreview);
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();
  });
  document.getElementById("bet-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    setError("");
    var market = selectedMarket();
    var selection = selectedSelection(market);
    var stake = Number(stakeEl.value || 0);
    if (!market || !selection) { setError("请选择市场和方向"); return; }
    if (!(stake > 0)) { setError("请输入大于 0 的投注积分"); return; }
    var payload = state.bet
      ? {id: state.bet.id, selection: selection, stake: stake}
      : {issue: state.options.issue, match_no: state.options.match_no,
         market: market, selection: selection, stake: stake};
    var url = state.bet ? "/bets/update" : "/bets";
    try {
      var resp = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "保存失败");
      var label = (data.market_label || "") + " " + (data.selection_label || "") +
        " " + money(data.stake) + " 积分";
      flashAndReload((state.bet ? "注单已更新：" : "已记录：") + label, data.id);
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  showSavedFlash();
})();
</script>
"""


def render_query_html(
    rows: list[dict],
    *,
    issues: list[dict],
    issue: str = "",
    team: str = "",
    level: str = "",
    kind: str = "",
    attention_only: bool = False,
    latest_only: bool = True,
    db_path: str = "",
    port: str = "",
    analyses: list[dict] = [],
    source_url: str = "",
    report_links: dict[str, str] | None = None,
    leagues: list[str] | None = None,
    bets: list[dict] | None = None,
    bet_stats: dict | None = None,
    bet_trend: list[dict] | None = None,
    cache_dir: str | Path | None = None,
) -> str:
    from .browser_report import render_browser_html
    report_links = report_links or {}
    leagues = leagues or []
    bets = bets or []
    bet_stats = bet_stats or summarize_bets(bets)
    bet_trend = bet_trend or []
    fetch_block = _FETCH_BLOCK.replace(
        "{_esc(__version__)}", _esc(__version__)
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    freshness = _data_freshness(
        rows,
        analyses,
        issue=issue,
        cache_dir=cache_dir,
    )
    # Build the unified analysis page when a full issue is requested
    unified_html = ""
    if analyses and issue:
        unified_html = render_browser_html(analyses, issue, source_url, datetime.now())
    # Build unified page only when a specific issue with full analyses is requested
    unified_html = ""
    if analyses and issue:
        unified_html = render_browser_html(analyses, issue, source_url, datetime.now())
    issue_options = ['<option value="">全部期号</option>']
    for item in sorted(issues, key=lambda x: str(x.get("issue", "")), reverse=True):
        selected = " selected" if str(item.get("issue")) == issue else ""
        issue_options.append(f'<option value="{_esc(item.get("issue"))}"{selected}>{_esc(item.get("issue"))}</option>')
    level_options = []
    for value in ("", "重点关注", "关注", "稳定", "无数据"):
        selected = " selected" if value == level else ""
        label = "全部级别" if value == "" else value
        level_options.append(f'<option value="{_esc(value)}"{selected}>{_esc(label)}</option>')
    kind_options = []
    for value in ("", "升盘", "降盘", "水位反转", "水位摆动"):
        selected = " selected" if value == kind else ""
        label = "全部类型" if value == "" else value
        kind_options.append(f'<option value="{_esc(value)}"{selected}>{_esc(label)}</option>')
    team_options = ['<option value="">全部联赛</option>']
    for value in leagues:
        selected = " selected" if value == team else ""
        team_options.append(f'<option value="{_esc(value)}"{selected}>{_esc(value)}</option>')
    bets_by_match: dict[tuple[str, str], list[dict]] = {}
    for bet in bets:
        key = (str(bet.get("issue") or ""), str(bet.get("match_no") or ""))
        bets_by_match.setdefault(key, []).append(bet)
    can_bet_ui = bool(port)
    now = datetime.now()
    table_rows = []
    for row_idx, row in enumerate(rows):
        issue_value = str(row.get("issue") or "")
        match_no = str(row.get("match_no") or "")
        match_bets = bets_by_match.get((issue_value, match_no), [])
        kickoff = parse_kickoff(
            issue_value,
            str(row.get("match_time") or ""),
            str(row.get("captured_at") or ""),
        )
        locked = bool(row.get("final_score")) or kickoff is None or now >= kickoff
        tip_lines = []
        for bet in match_bets:
            line = (
                f"{bet.get('market_label') or ''} "
                f"{bet.get('selection_label') or ''} "
                f"{int(round(float(bet.get('stake') or 0)))} 积分"
            ).strip()
            if bet.get("status") == "settled":
                line += f"（{bet.get('result_label')} {float(bet.get('profit') or 0):+.2f}）"
            tip_lines.append(line)
        bet_tip = "\n".join(tip_lines)
        group_attrs = ""
        if bet_tip:
            attr = "data-bet-tip" if can_bet_ui else "title"
            group_attrs = f' {attr}="{_esc(bet_tip)}"'
        more = (
            '<a class="bet-more" href="#bets-section" title="查看本期注单">&gt;</a>'
            if match_bets else ""
        )
        if not can_bet_ui:
            bet_cell = '<td class="bet-cell muted">-</td>'
        elif locked:
            button_title = "开赛时间未知" if kickoff is None and not row.get("final_score") else "比赛已开始，投注已关闭"
            bet_cell = (
                f'<td class="bet-cell"><span class="bet-action-group"{group_attrs}>'
                f'<button type="button" class="bet-btn" disabled title="{_esc(button_title)}">'
                f'投注</button>{more}</span></td>'
            )
        else:
            bet_cell = (
                f'<td class="bet-cell"><span class="bet-action-group"{group_attrs}>'
                f'<button type="button" class="bet-btn bet-open" '
                f'data-issue="{_esc(issue_value)}" data-match-no="{_esc(match_no)}" '
            f'>投注</button>{more}</span></td>'
            )
        table_rows.append(
            f"""
            <tr>
              <td class="issue-cell">{_esc(row.get("issue"))}</td>
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(row.get("match_no"))}</span>
                  {_badge(str(row.get("level") or "无数据"))}
                </div>
                <strong>{_esc(row.get("home_team"))} vs {_esc(row.get("away_team"))}</strong>
                <div class="muted">{_league_badge(row.get("league"), row.get("league_color"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td class="type-cell">{_kind_badge(str(row.get("kind") or ""), muted=_kind_muted(row))}{_split_html(row, f'splitpop-{row_idx}')}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_lean_cell(row)}</td>

              <td class="total-cell">
                {_total_goals_html(row.get('payload_json') or '')}
              </td>
              {bet_cell}
            </tr>
            """
        )
    empty = ""
    if not rows:
        empty = '<div class="no-alert">没有符合条件的异动记录。先运行分析入库后再查询。</div>'
    total_stake = float(bet_stats.get("stake") or 0)
    pending_bets = int(bet_stats.get("pending") or 0)
    settled_profit = float(bet_stats.get("profit") or 0)
    settled_bets = int(bet_stats.get("settled") or 0)
    hit_rate = float(bet_stats.get("hit_rate") or 0)
    roi = float(bet_stats.get("roi") or 0)
    asian_stats = (bet_stats.get("markets") or {}).get("asian", {})
    total_stats = (bet_stats.get("markets") or {}).get("total", {})
    follow_stats = (bet_stats.get("follows") or {})
    export_issue = quote_from_bytes(issue.encode("utf-8")) if issue else ""
    export_link = (
        f'<a class="report-btn" href="/bets.csv?issue={export_issue}">导出 CSV</a>'
        if can_bet_ui else ""
    )
    market_lines = (
        f"亚盘 {asian_stats.get('bets', 0)} 注 · 已结算 {asian_stats.get('settled', 0)} · "
        f"净收益 {asian_stats.get('profit', 0):+.2f} · 命中率 {asian_stats.get('hit_rate', 0):.1f}%",
        f"大小球 {total_stats.get('bets', 0)} 注 · 已结算 {total_stats.get('settled', 0)} · "
        f"净收益 {total_stats.get('profit', 0):+.2f} · 命中率 {total_stats.get('hit_rate', 0):.1f}%",
        f"跟随模型 {follow_stats.get('follow', {}).get('bets', 0)} 注 · "
        f"偏离模型 {follow_stats.get('deviate', {}).get('bets', 0)} 注 · "
        f"无推荐 {follow_stats.get('none', {}).get('bets', 0)} 注",
    )
    bet_table_rows = []
    for bet in bets:
        status = str(bet.get("status") or "")
        profit = (
            f"{float(bet.get('profit') or 0):+.2f}"
            if status == "settled"
            else "-"
        )
        rec_action = str(bet.get("rec_action") or "")
        rec_side = str(bet.get("rec_side_label") or "")
        rec_text = " ".join(part for part in (rec_action, rec_side) if part) or "无推荐"
        if int(bet.get("follows_recommendation") or 0):
            rec_text += " · 跟随"
        elif rec_action in ("主推", "轻仓"):
            rec_text += " · 偏离"
        bet_table_rows.append(
            f"""
            <tr id="bet-row-{int(bet.get('id') or 0)}">
              <td class="issue-cell">{_esc(bet.get("issue"))}</td>
              <td class="match-cell">
                <strong>{_esc(bet.get("home_team"))} vs {_esc(bet.get("away_team"))}</strong>
                <div class="muted">{_esc(bet.get("match_time"))}</div>
              </td>
              <td class="score-cell">{_esc(bet.get("final_score") or "未开赛")}</td>
              <td>{_esc(bet.get("market_label"))}</td>
              <td>
                {_esc(bet.get("selection_label"))}
                <div class="muted">{_esc(bet.get("line_text"))}</div>
                <div class="muted">水位 {_esc(f'{float(bet.get("water") or 0):.2f}')}</div>
              </td>
              <td>{int(round(float(bet.get("stake") or 0)))} 积分</td>
              <td>{_esc(bet.get("status_label"))}<div class="muted">{_esc(bet.get("result_label"))}</div></td>
              <td class="profit-cell">{profit}</td>
              <td class="rec-cell">{_esc(rec_text)}</td>
            </tr>
            """
        )
    trend_rows = [
        f"""
        <tr>
          <td>{_esc(row.get("issue"))}</td>
          <td>{int(row.get("bets") or 0)}</td>
          <td>{int(row.get("settled") or 0)}</td>
          <td>{int(round(float(row.get("stake") or 0)))} 积分</td>
          <td class="profit-cell">{float(row.get("profit") or 0):+.2f}</td>
          <td>{float(row.get("roi") or 0):+.1f}%</td>
          <td>{float(row.get("hit_rate") or 0):.1f}%</td>
        </tr>
        """
        for row in bet_trend
    ]
    trend_panel = (
        f"""
        <details class="bets-trend">
          <summary>跨期投注趋势</summary>
          <div class="table-scroll">
            <table class="bets-table">
              <thead><tr><th class="issue-cell">期号</th><th>注数</th><th>已结算</th><th>投注积分</th><th>净收益</th><th>ROI</th><th>命中率</th></tr></thead>
              <tbody>{''.join(trend_rows)}</tbody>
            </table>
          </div>
        </details>
        """
        if trend_rows else ""
    )
    bet_panel = f"""
    <section class="bets-section" id="bets-section">
      <div class="bets-head">
        <h2>本期投注</h2>
        <div class="bets-head-right">
        <span class="muted">注单 {len(bets)} · 投注积分 {int(round(total_stake))} · 已结算 {settled_bets} · 待结算 {pending_bets} · 净收益 {settled_profit:+.2f} · ROI {roi:+.1f}% · 命中率 {hit_rate:.1f}%</span>
          {export_link}
        </div>
      </div>
      <div class="bets-stats">
        <span>{market_lines[0]}</span>
        <span>{market_lines[1]}</span>
        <span>{market_lines[2]}</span>
      </div>
      <div class="table-scroll">
        <table class="bets-table">
          <thead>
            <tr><th class="issue-cell">期号</th><th class="match-cell">比赛</th><th class="score-cell">完场比分</th><th>市场</th><th>方向</th><th>积分</th><th>状态</th><th>收益</th><th>模型</th></tr>
          </thead>
          <tbody>{''.join(bet_table_rows) or '<tr><td colspan="9" class="bets-empty">本期还没有投注。</td></tr>'}</tbody>
        </table>
      </div>
      {trend_panel}
      <p class="bets-disclaimer">此分析工具纯属游戏娱乐，不构成投资建议，风险自担！</p>
    </section>
    """
    bet_block = _BET_BLOCK if can_bet_ui else ""
    bet_chip = (
        f'<a class="bet-chip" href="#bets-section">本期投注 {len(bets)} 注 · '
        f'{int(round(total_stake))} 积分 · 净收益 {settled_profit:+.2f}</a>'
        if bets else ""
    )
    freshness_class = freshness["class"]
    freshness_html = (
        f'<div class="data-freshness {freshness_class}">'
        f'<strong>数据状态</strong> {_esc(freshness["captured"])} · '
        f'<span class="freshness-item {freshness.get("pages_class", freshness_class)}">'
        f'{_esc(freshness["pages"])}</span> · '
        f'<span class="freshness-item {freshness.get("data_class", freshness_class)}">'
        f'{_esc(freshness.get("data", "数据产物：未记录"))}</span>'
        f'</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>足彩分析系统1.3.16</title>
  <style>
    :root {{
      --canvas: #f3f5f7; --panel: #ffffff; --ink: #1d2733; --muted: #64748b;
      --line: #d9dfe6; --red: #b42318; --red-soft: #fff1ef; --green: #16705a;
      --green-soft: #eaf8f2; --amber: #b45309;
      --blue: #2563eb; --blue-dark: #1d4ed8; --indigo: #4f46e5; --indigo-dark: #4338ca;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }}
    h1 {{ margin: 4px 0 6px; font-size: 28px; }}
    .meta, .muted {{ color: var(--muted); }}
    .data-freshness {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--muted); font-size: 12px; }}
    .data-freshness strong {{ color: var(--ink); }}
    .data-freshness.freshness-ok {{ border-color: #bbdecf; background: var(--green-soft); }}
    .data-freshness.freshness-stale, .data-freshness.freshness-warn {{ border-color: #f1d39a; background: #fff8e8; color: #8a5a08; }}
    .data-freshness.freshness-muted {{ background: #f8fafc; }}
    .freshness-item.freshness-ok {{ color: #16705a; }}
    .freshness-item.freshness-stale, .freshness-item.freshness-warn {{ color: #8a5a08; }}
    form {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 18px 0; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }}
    input, select, button {{ padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }}
    button {{ background: #1d2733; color: #fff; cursor: pointer; }}
    .query-toolbar {{ gap: 9px; }}
    .query-toolbar input, .query-toolbar select {{ border-radius: 6px; }}
    .query-toolbar > button {{ min-height: 36px; padding: 0 13px; border-radius: 6px; font-weight: 500; transition: background .15s, border-color .15s, color .15s, box-shadow .15s; }}
    .action-icon-sprite {{ position: absolute; width: 0; height: 0; overflow: hidden; }}
    .query-toolbar .action-icon {{ display: inline-block; flex: 0 0 15px; width: 15px; height: 15px; margin-right: 6px; line-height: 1; }}
    .query-toolbar > button:focus-visible {{ outline: 3px solid rgba(37, 99, 235, .22); outline-offset: 1px; }}
    .query-toolbar .action-primary {{ background: var(--blue); color: #fff; border-color: var(--blue); }}
    .query-toolbar .action-primary:hover {{ background: var(--blue-dark); border-color: var(--blue-dark); }}
    .query-toolbar .action-secondary {{ background: var(--panel); color: var(--blue); border-color: var(--blue); }}
    .query-toolbar .action-secondary:hover {{ background: #eff6ff; border-color: var(--blue-dark); color: var(--blue-dark); }}
    .query-toolbar .action-ai {{ background: var(--indigo); color: #fff; border-color: var(--indigo); }}
    .query-toolbar .action-ai:hover {{ background: var(--indigo-dark); border-color: var(--indigo-dark); }}
    .query-toolbar .action-neutral {{ background: #f8fafc; color: var(--ink); border-color: var(--line); }}
    .query-toolbar .action-neutral:hover {{ background: #fff; color: var(--blue); border-color: var(--blue); }}
    .query-toolbar .action-ghost {{ background: transparent; color: var(--muted); border-color: transparent; }}
    .query-toolbar .action-ghost:hover {{ background: #f8fafc; color: var(--ink); border-color: var(--line); }}
    .query-toolbar > button:disabled {{ background: #e2e8f0; color: #64748b; border-color: #cbd5e1; cursor: not-allowed; }}
    .checks {{ display: flex; gap: 12px; align-items: center; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; color: #475569; white-space: nowrap; }}
    .match-cell {{ min-width: 220px; white-space: normal; overflow: hidden; }}
    .match-cell strong {{ display: block; white-space: normal; }}
    .match-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.time-cell {{ white-space: nowrap; color: #475569; }}
.total-cell {{ white-space: normal; font-size: 13px; }}
.line-delta.delta-hot {{ color: #fff; background: var(--red); border-radius: 4px; padding: 1px 6px; display: inline-block; }}
    .alert-table .line-change {{ white-space: normal; }}
    .line-pair {{ display: flex; gap: 6px; align-items: baseline; }}
    .line-pair .k {{ flex: 0 0 1.2em; color: var(--muted); font-size: 12px; }}
    .line-delta {{ margin-top: 5px; color: #475569; font-size: 12px; font-weight: 600; }}
    .lean-cell {{ font-weight: 600; color: #334155; }}
    .model-lean {{ color: #0f172a; }}
    .model-reason {{
      margin-top: 2px; font-weight: 400; font-size: 12px; color: var(--muted);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .lean-note {{ margin-top: 2px; font-weight: 600; font-size: 12px; color: #1d4ed8; }}
    .score-cell {{ min-width: 56px; white-space: nowrap; font-weight: 600; color: var(--ink); }}
    .alert-table {{ table-layout: fixed; }}
    .alert-table .issue-cell {{ width: 6%; white-space: nowrap; }}
    .alert-table .match-cell {{ width: 20%; }}
    .alert-table .score-cell {{ width: 8%; }}
    .alert-table .type-cell {{ width: 8%; }}
    .alert-table .line-change {{ width: 18%; }}
    .alert-table .lean-cell {{ width: 22%; }}
    .alert-table .total-cell {{ width: 10%; }}
.report-btn {{ background: var(--green); color: #fff; margin-left: 8px; }}
.report-btn:disabled {{ background: #cbd5e1; cursor: not-allowed; }}
.settings-btn {{ margin-left: auto; }}
#settings-dialog {{ width: min(760px, calc(100vw - 24px)); max-height: calc(100vh - 24px); padding: 0; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--ink); box-shadow: 0 20px 60px rgba(15, 23, 42, .3); }}
#settings-dialog::backdrop {{ background: rgba(15, 23, 42, .45); }}
.update-progress-wrap {{ display:flex; align-items:center; gap:10px; margin-top:8px; }}
.update-progress-wrap[hidden] {{ display:none; }}
.update-progress-wrap progress {{ width:min(100%, 360px); height:8px; accent-color:#2563eb; }}
.settings-backup {{ margin: 14px 20px 0; padding: 12px; border: 1px solid var(--line); border-radius: 8px; display: grid; gap: 6px; }}
.settings-backup-actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.settings-button {{ display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 32px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); font-size: 13px; font-weight: 500; line-height: 1; text-decoration: none; white-space: nowrap; }}
.settings-button:hover {{ background: #f8fafc; border-color: #94a3b8; }}
.settings-button:disabled {{ background: #e2e8f0; color: #64748b; border-color: #cbd5e1; cursor: not-allowed; }}
.settings-button-primary {{ background: var(--blue); color: #fff; border-color: var(--blue); }}
.settings-button-primary:hover {{ background: var(--blue-dark); color: #fff; border-color: var(--blue-dark); }}
.settings-icon {{ display: inline-block; flex: 0 0 15px; width: 15px; height: 15px; }}
.update-details {{ display: grid; gap: 5px; overflow-wrap: anywhere; }}
.update-details a {{ width: fit-content; }}
.backup-restore-label {{ flex-direction: row; cursor: pointer; }}
#settings-form {{ display: block; margin: 0; padding: 18px; background: transparent; border: 0; }}
.settings-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
.settings-head strong {{ font-size: 17px; }}
.settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
.settings-grid fieldset {{ min-width: 0; margin: 0; padding: 12px; border: 1px solid var(--line); border-radius: 6px; }}
.settings-grid legend {{ padding: 0 5px; color: var(--muted); font-size: 12px; }}
.settings-grid label {{ margin-top: 8px; }}
.settings-grid input[type="text"], .settings-grid input[type="url"], .settings-grid input[type="password"] {{ width: 100%; }}
.settings-clear {{ flex-direction: row; align-items: center; gap: 6px; }}
.settings-clear input {{ margin: 0; }}
.settings-grid .muted {{ margin-top: 7px; font-size: 12px; }}
.settings-test-row {{ display: flex; align-items: center; gap: 8px; margin-top: 10px; min-height: 28px; }}
.settings-test {{ min-height: 28px; padding: 5px 9px; font-size: 12px; }}
.settings-test-status {{ margin-top: 0 !important; }}
.settings-test-status.ok {{ color: var(--green); }}
.settings-test-status.fail {{ color: var(--red); }}
.settings-error {{ min-height: 20px; margin-top: 10px; color: var(--green); font-size: 12px; }}
.settings-input {{ width: 120px; min-height: 30px; padding: 4px 8px; }}
.settings-actions {{ display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }}
@media (max-width: 640px) {{ .settings-grid {{ grid-template-columns: 1fr; }} }}
.bet-cell {{ width: 8%; white-space: nowrap; }}
.bet-action-group {{ display: inline-flex; align-items: center; gap: 4px; }}
.bet-btn {{ padding: 5px 9px; background: var(--green); color: #fff; border-color: var(--green); font-size: 12px; }}
.bet-btn:disabled {{ background: #e2e8f0; color: #64748b; border-color: #cbd5e1; cursor: not-allowed; }}
.bet-more {{ display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 26px; padding: 0 4px; border: 1px solid var(--line); border-radius: 4px; background: #fff; color: var(--ink); text-decoration: none; font-weight: 700; line-height: 1; }}
.bet-more:hover {{ border-color: #94a3b8; background: #f8fafc; }}
#bet-tip {{ position: fixed; z-index: 60; max-width: min(320px, calc(100vw - 16px)); padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px; background: rgba(255, 255, 255, .96); color: var(--ink); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); font-size: 12px; line-height: 1.6; white-space: pre-line; pointer-events: none; }}
#bet-tip[hidden] {{ display: none; }}
.bets-section {{ margin-top: 24px; }}
.bets-head {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }}
.bets-head-right {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: flex-end; }}
.bets-head h2 {{ margin: 0; font-size: 18px; }}
.bets-stats {{ display: flex; flex-wrap: wrap; gap: 6px 18px; margin-bottom: 8px; color: var(--muted); font-size: 12px; }}
.bets-trend {{ margin-top: 10px; }}
.bets-trend summary {{ cursor: pointer; color: var(--muted); font-size: 13px; margin-bottom: 6px; }}
.bet-chip {{ display: inline-block; padding: 3px 9px; border: 1px solid #bfdbfe; border-radius: 4px; background: #eff6ff; color: #1d4ed8; text-decoration: none; font-weight: 600; }}
.bet-flash {{ position: fixed; z-index: 70; top: 18px; left: 50%; transform: translateX(-50%); padding: 9px 16px; border-radius: 6px; background: #166534; color: #fff; box-shadow: 0 8px 24px rgba(15, 23, 42, .24); font-size: 13px; }}
.bet-flash[hidden] {{ display: none; }}
.bet-highlight td {{ background: #fff3cd !important; }}
.bets-table {{ table-layout: fixed; }}
.bets-table th, .bets-table td {{ padding: 8px 10px; font-size: 13px; }}
.bets-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
.bets-table .issue-cell {{ width: 6%; white-space: nowrap; }}
.bets-table .match-cell {{ width: 32%; white-space: normal; }}
.bets-table .score-cell {{ width: 8%; white-space: nowrap; font-weight: 600; }}
.bets-table .match-cell strong {{ display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.bets-table .profit-cell {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
.bets-table .rec-cell {{ font-size: 12px; color: #475569; }}
.bet-existing-actions button {{ margin-right: 4px; padding: 4px 7px; background: #fff; color: var(--ink); border-color: var(--line); font-size: 12px; }}
.bets-empty {{ padding: 16px; color: var(--muted); }}
.bets-disclaimer {{ margin: 10px 0 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--red-soft); color: var(--red); font-size: 12px; font-weight: 600; }}
#bet-dialog {{ width: min(640px, calc(100vw - 24px)); max-height: calc(100vh - 24px); padding: 0; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--ink); box-shadow: 0 20px 60px rgba(15, 23, 42, .3); }}
#bet-dialog::backdrop {{ background: rgba(15, 23, 42, .45); }}
#bet-form {{ display: block; margin: 0; padding: 16px; background: transparent; border: 0; }}
.bet-dialog-head {{ display: flex; gap: 12px; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }}
.bet-dialog-head strong {{ display: block; font-size: 16px; margin-bottom: 4px; }}
.icon-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0; background: transparent; color: var(--muted); border-color: transparent; }}
.icon-btn:hover {{ background: #f1f5f9; color: var(--ink); }}
.bet-existing {{ margin-bottom: 10px; padding: 8px 10px; background: #f8fafc; border: 1px solid var(--line); border-radius: 6px; }}
.bet-existing-title {{ margin-bottom: 5px; font-size: 12px; font-weight: 600; color: var(--muted); }}
.bet-existing-row {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; padding: 3px 0; font-size: 12px; }}
.bet-market {{ margin-top: 8px; padding: 9px 10px; border: 1px solid var(--line); border-radius: 6px; }}
.bet-market.active {{ border-color: #2563eb; background: #f8fbff; }}
.bet-market.disabled {{ opacity: .55; }}
.bet-market-head {{ display: block; font-size: 13px; color: var(--ink); }}
.bet-market-head input {{ margin-right: 5px; }}
.bet-rec {{ margin: 3px 0 6px 20px; color: #1d4ed8; font-size: 12px; font-weight: 600; }}
.bet-selections {{ display: flex; flex-wrap: wrap; gap: 12px; margin-left: 20px; }}
.bet-selection {{ display: inline-flex; flex-direction: row; gap: 4px; align-items: center; color: var(--ink); font-size: 13px; }}
.bet-stake {{ margin-top: 12px; max-width: 220px; }}
.bet-preview {{ min-height: 18px; margin-top: 6px; color: var(--muted); font-size: 12px; }}
.bet-error {{ min-height: 18px; margin-top: 4px; color: var(--red); font-size: 12px; }}
.bet-dialog-actions {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 10px; }}
#bet-delete {{ background: #fff; color: var(--red); border-color: #fecaca; }}
#bet-submit {{ background: #2563eb; border-color: #2563eb; }}

    .badge, .kind {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .badge-hot {{ background: var(--red-soft); color: var(--red); }}
    .badge-watch {{ background: #fff3d6; color: var(--amber); }}
    .badge-stable {{ background: var(--green-soft); color: var(--green); }}
  .league-badge {{ display: inline-block; padding: 1px 7px; border-radius: 4px; color: #fff; font-size: 12px; font-weight: 600; line-height: 1.6; }}
    .kind-drop {{ color: #0f766e; background: var(--green-soft); }}
    .kind-rise {{ color: #b42318; background: var(--red-soft); }}
    .kind-reverse {{ color: #9a5b06; background: #fff3d6; }}
.line-delta.drop-big {{ color: #fff; background: var(--green); display: inline-block; padding: 1px 6px; border-radius: 4px; }}
    .kind-swing, .kind-watch {{ color: #475569; background: #eef2f7; }}
  .kind-muted {{ background: transparent; }}
  .kind-split {{ margin-top: 4px; color: var(--amber); font-size: 11px; font-weight: 600; line-height: 1.5; cursor: help; }}
  #split-pop {{ position: fixed; z-index: 40; min-width: 240px; max-width: calc(100vw - 16px); padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .82); backdrop-filter: blur(3px); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); pointer-events: none; }}
  #split-pop[hidden] {{ display: none; }}
  .split-cards {{ display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }}
  .split-card {{ min-width: 0; }}
  .split-card-title {{ margin-bottom: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }}
  .split-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .split-table th {{ padding: 3px 10px 5px 0; background: transparent; color: var(--muted); font-weight: 600; text-align: left; white-space: nowrap; }}
  .split-table td {{ padding: 3px 10px 3px 0; border-top: 1px solid var(--line); color: #2b3644; white-space: nowrap; }}
  .split-table td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .split-table b {{ color: var(--amber); font-weight: 700; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .alert-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
    @media (max-width: 900px) {{
      .page {{ padding: 18px 10px 42px; }}
      .alert-table {{ min-width: 1080px; }}
      .bets-table {{ min-width: 820px; }}
      .bets-head {{ align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <h1>足彩分析系统1.3.16</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    {f'<div class="meta muted">当前服务端口：{_esc(port)}</div>' if port else ''}
    {f'<div class="meta">{bet_chip}</div>' if bet_chip else ''}
    {freshness_html}
    <form method="get" id="query-form" class="query-toolbar">
      <label>期号
        <select name="issue">{''.join(issue_options)}</select>
      </label>
      <label>联赛
        <select name="team">{''.join(team_options)}</select>
      </label>
      <label>级别
        <select name="level">{''.join(level_options)}</select>
      </label>
      <label>类型
        <select name="kind">{''.join(kind_options)}</select>
      </label>
      <button type="submit" class="action-primary"><svg class="action-icon" aria-hidden="true"><use href="#icon-search"></use></svg>查询</button>
      <button type="button" id="report-btn" class="action-neutral"><svg class="action-icon" aria-hidden="true"><use href="#icon-file-text"></use></svg>分析报告</button>
      <button type="button" id="review-btn" class="action-neutral"><svg class="action-icon" aria-hidden="true"><use href="#icon-list-checks"></use></svg>更新比分</button>
      <span id="report-hint" class="muted" style="font-size:12px"></span>
      <span id="review-hint" class="muted" style="font-size:12px"></span>
      <button type="button" id="settings-btn" class="action-ghost settings-btn"><svg class="action-icon" aria-hidden="true"><use href="#icon-settings"></use></svg>设置</button>
    </form>
    <script>
    (function() {{
      const queryForm = document.getElementById("query-form");
      function restoreQueryTop() {{
        try {{
          if (sessionStorage.getItem("query-scroll-top") !== "1") return;
          sessionStorage.removeItem("query-scroll-top");
        }} catch (e) {{}}
        if ("scrollRestoration" in history) history.scrollRestoration = "manual";
        window.scrollTo({{left: 0, top: 0, behavior: "auto"}});
        requestAnimationFrame(() => window.scrollTo(0, 0));
      }}
      if (queryForm) queryForm.addEventListener("submit", function() {{
        try {{ sessionStorage.setItem("query-scroll-top", "1"); }} catch (e) {{}}
        if ("scrollRestoration" in history) history.scrollRestoration = "manual";
        window.scrollTo({{left: 0, top: 0, behavior: "auto"}});
      }});
      restoreQueryTop();
      const sel = document.querySelector('select[name="issue"]');
      const btn = document.getElementById('report-btn');
      const hint = document.getElementById('report-hint');
      const reports = {json.dumps(report_links, ensure_ascii=False)};
      const rbtn = document.getElementById('review-btn');
      const rhint = document.getElementById('review-hint');
      function sync() {{
        const v = sel ? sel.value : "";
        const file = v ? (reports[v] || null) : null;
        btn.disabled = !file;
        btn.classList.toggle("action-primary", Boolean(file));
        btn.classList.toggle("action-neutral", !file);
        if (rbtn) rbtn.disabled = !v;
        if (file) {{
          btn.dataset.report = file;
          hint.textContent = "将打开 " + file;
        }} else {{
          btn.removeAttribute("data-report");
          hint.textContent = v ? "该期暂无报告文件" : "选择期号后可打开分析报告";
        }}
      }}
      if (sel) sel.addEventListener("change", sync);
      if (rbtn) rbtn.addEventListener("click", async function() {{
        const v = sel ? sel.value : "";
        if (!v) return;
        rbtn.disabled = true;
        rhint.textContent = "更新比分 " + v + " 中...";
        await fetch("/review", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{issue: v, refresh: true}})
        }});
        const poll = setInterval(async () => {{
          const r = await fetch("/review-status");
          const s = await r.json();
          if (s.running) return;
          clearInterval(poll);
          rbtn.disabled = false;
          rhint.textContent = s.error || s.result || "完成";
          // 复盘总是刷新比分；重载前把结果暂存，载入后还原。
          if (s.result && !s.error) {{
            try {{ sessionStorage.setItem("review-msg", s.result); }} catch (e) {{}}
            setTimeout(function() {{ location.reload(); }}, 1200);
          }}
        }}, 1500);
      }});
      try {{
        var savedMsg = sessionStorage.getItem("review-msg");
        if (savedMsg) {{
          rhint.textContent = savedMsg;
          sessionStorage.removeItem("review-msg");
        }}
      }} catch (e) {{}}
      if (btn) btn.addEventListener("click", function() {{
        const name = btn.getAttribute("data-report");
        if (name) window.open("/report/" + encodeURIComponent(name), "_blank");
      }});
      sync();
    }})();
    </script>
    {fetch_block}
    {empty}
    <div class="table-scroll">
      <table class="alert-table">
        <thead>
          <tr>
            <th class="issue-cell">期号</th><th class="match-cell">比赛</th><th class="score-cell">比分</th><th class="type-cell">类型</th><th class="line-change">盘口变化</th><th class="lean-cell">倾向</th><th class="total-cell">大小球</th><th class="bet-cell">操作</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
    {bet_panel}
  </div>
  {bet_block}
  <div id="split-pop" hidden></div>
  <script>
  (function () {{
    var pop = document.getElementById('split-pop');
    if (!pop) return;
    document.querySelectorAll('[data-split-for]').forEach(function (el) {{
      var tpl = document.getElementById(el.getAttribute('data-split-for'));
      if (!tpl) return;
      function show() {{
        pop.innerHTML = tpl.innerHTML;
        pop.hidden = false;
        var r = el.getBoundingClientRect();
        var left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8));
        var top = r.bottom + 6;
        if (top + pop.offsetHeight > window.innerHeight - 8) {{
          top = Math.max(8, r.top - pop.offsetHeight - 6);
        }}
        pop.style.left = left + 'px';
        pop.style.top = top + 'px';
      }}
      el.addEventListener('mouseenter', show);
      el.addEventListener('focus', show);
      el.addEventListener('mouseleave', function () {{ pop.hidden = true; }});
      el.addEventListener('blur', function () {{ pop.hidden = true; }});
    }});
  }})();
  </script>
</body>
</html>
"""

