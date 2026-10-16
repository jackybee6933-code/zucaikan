from __future__ import annotations

import html
import json
from datetime import datetime
from urllib.parse import quote_from_bytes

from .report_index import build_issue_report_index


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


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
    """盘口分歧标记 + 悬停弹层模板；弹层列出各公司初盘→即时盘口。"""
    blocks = row.get("split") or []
    if not isinstance(blocks, list):
        blocks = []
    blocks = [b for b in blocks if isinstance(b, dict) and b.get("rows")]
    if not blocks:
        return ""
    parts = []
    for block in blocks:
        picked = str(block.get("picked") or "")
        trs = []
        for item in block["rows"]:
            if not isinstance(item, dict):
                continue
            cid = _esc(str(item.get("cid") or ""))
            name = _esc(str(item.get("name") or ""))
            label = f"{name}({cid})" if name else cid
            cur = _esc(str(item.get("current") or "—"))
            # 报告实际采用的那家加粗，一眼看出信号来自谁
            cur_cell = f"<b>{cur}</b>" if str(item.get("cid")) == picked else cur
            trs.append(
                f"<tr><td>{label}</td><td>{_esc(str(item.get('initial') or '—'))} \u2192 {cur_cell}</td></tr>"
            )
        parts.append(
            f'<div class="split-card"><div class="split-card-title">'
            f'{_esc(str(block.get("market") or ""))}</div>'
            f'<table class="split-table"><thead><tr><th>公司</th><th>初盘 \u2192 即时</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>'
        )
    body = "".join(parts)
    return (
        f'<div class="kind-split" data-split-for="{_esc(uid)}">盘口分歧</div>'
        f'<template id="{_esc(uid)}">{body}</template>'
    )


def _lean_cell(row: dict) -> str:
    """倾向列：模型读球队资料的亚盘赢盘倾向（主行）+ 盘口异动的 lean（次行）。"""
    parts: list[str] = []
    try:
        read = json.loads(str(row.get("team_read") or "") or "{}")
    except (json.JSONDecodeError, TypeError):
        read = {}
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
"""    <form class="fetch-bar" id="fetch-form">
      <label>抓取期号
        <input name="issue" placeholder="如 26120" style="width:120px">
      </label>
      <button type="submit">抓取并入库</button>
      <button type="button" id="refresh-odds-btn">刷新盘口分歧</button>
      <button type="button" id="ai-analyze-btn" style="background:#2563eb;color:#fff;border-color:#2563eb" title="读 500 数据分析页球队资料，大模型给出亚盘赢盘倾向（只展示，不改推荐）">AI 分析</button>
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
) -> str:
    from .browser_report import render_browser_html
    report_links = report_links or {}
    leagues = leagues or []
    fetch_block = _FETCH_BLOCK
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    table_rows = []
    for row_idx, row in enumerate(rows):
        table_rows.append(
            f"""
            <tr>
              <td>{_esc(row.get("issue"))}</td>
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(row.get("match_no"))}</span>
                  {_badge(str(row.get("level") or "无数据"))}
                </div>
                <strong>{_esc(row.get("home_team"))} vs {_esc(row.get("away_team"))}</strong>
                <div class="muted">{_league_badge(row.get("league"), row.get("league_color"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td>{_kind_badge(str(row.get("kind") or ""), muted=_kind_muted(row))}{_split_html(row, f'splitpop-{row_idx}')}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_lean_cell(row)}</td>

              <td class="total-cell">
                {_total_goals_html(row.get('payload_json') or '')}
              </td>
            </tr>
            """
        )
    empty = ""
    if not rows:
        empty = '<div class="no-alert">没有符合条件的异动记录。先运行分析入库后再查询。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>变盘异动查询</title>
  <style>
    :root {{
      --canvas: #f3f5f7; --panel: #ffffff; --ink: #1d2733; --muted: #64748b;
      --line: #d9dfe6; --red: #b42318; --red-soft: #fff1ef; --green: #16705a;
      --green-soft: #eaf8f2; --amber: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }}
    h1 {{ margin: 4px 0 6px; font-size: 28px; }}
    .meta, .muted {{ color: var(--muted); }}
    form {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 18px 0; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }}
    input, select, button {{ padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }}
    button {{ background: #1d2733; color: #fff; cursor: pointer; }}
    .checks {{ display: flex; gap: 12px; align-items: center; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; color: #475569; white-space: nowrap; }}
    .match-cell {{ min-width: 220px; white-space: normal; }}
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
    .alert-table .match-cell {{ width: 26%; }}
    .alert-table .line-change {{ width: 28%; }}
    .alert-table .score-cell {{ width: 9%; }}
.alert-table .lean-cell {{ width: 18%; }}
.alert-table .total-cell {{ width: 12%; }}
.report-btn {{ background: var(--green); color: #fff; margin-left: 8px; }}
.report-btn:disabled {{ background: #cbd5e1; cursor: not-allowed; }}

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
  #split-pop {{ position: fixed; z-index: 40; min-width: 240px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .82); backdrop-filter: blur(3px); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); pointer-events: none; }}
  #split-pop[hidden] {{ display: none; }}
  .split-card + .split-card {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }}
  .split-card-title {{ margin-bottom: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }}
  .split-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .split-table th {{ padding: 3px 10px 5px 0; background: transparent; color: var(--muted); font-weight: 600; text-align: left; white-space: nowrap; }}
  .split-table td {{ padding: 3px 10px 3px 0; border-top: 1px solid var(--line); color: #2b3644; white-space: nowrap; }}
  .split-table td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .split-table b {{ color: var(--amber); font-weight: 700; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .alert-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>变盘异动查询</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    {f'<div class="meta muted">当前服务端口：{_esc(port)}</div>' if port else ''}
    <form method="get">
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
      <div class="checks">
        <label><input type="checkbox" name="attention" value="1" {"checked" if attention_only else ""}> 只看异动</label>
        <label><input type="checkbox" name="latest" value="1" {"checked" if latest_only else ""}> 每期最新一次</label>
      </div>
      <button type="submit">查询</button>
      <button type="button" id="report-btn" class="report-btn">分析报告</button>
      <button type="button" id="review-btn" class="report-btn">复盘</button>
      <label class="muted" style="font-size:12px"><input type="checkbox" id="review-refresh" checked> 刷新比分</label>
      <span id="report-hint" class="muted" style="font-size:12px"></span>
      <span id="review-hint" class="muted" style="font-size:12px"></span>
    </form>
    <script>
    (function() {{
      const sel = document.querySelector('select[name="issue"]');
      const btn = document.getElementById('report-btn');
      const hint = document.getElementById('report-hint');
      const reports = {json.dumps(report_links, ensure_ascii=False)};
      const rbtn = document.getElementById('review-btn');
      const rhint = document.getElementById('review-hint');
      const rrefresh = document.getElementById('review-refresh');
      function sync() {{
        const v = sel ? sel.value : "";
        const file = v ? (reports[v] || null) : null;
        btn.disabled = !file;
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
        rhint.textContent = "复盘 " + v + " 中...";
        await fetch("/review", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{issue: v, refresh: !!(rrefresh && rrefresh.checked)}})
        }});
        const poll = setInterval(async () => {{
          const r = await fetch("/review-status");
          const s = await r.json();
          if (s.running) return;
          clearInterval(poll);
          rbtn.disabled = false;
          rhint.textContent = s.error || s.result || "完成";
          // 只有勾了「刷新比分」才需要重载看新比分；重载前把结果暂存，载入后还原。
          if (s.result && !s.error && rrefresh && rrefresh.checked) {{
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
            <th>期号</th><th>比赛</th><th>比分</th><th>类型</th><th>盘口变化</th><th>倾向</th><th>大小球</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
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
"""    <form class="fetch-bar" id="fetch-form">
      <label>抓取期号
        <input name="issue" placeholder="如 26120" style="width:120px">
      </label>
      <button type="submit">抓取并入库</button>
      <button type="button" id="refresh-odds-btn">刷新盘口分歧</button>
      <button type="button" id="ai-analyze-btn" style="background:#2563eb;color:#fff;border-color:#2563eb" title="读 500 数据分析页球队资料，大模型给出亚盘赢盘倾向（只展示，不改推荐）">AI 分析</button>
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
) -> str:
    from .browser_report import render_browser_html
    report_links = report_links or {}
    leagues = leagues or []
    fetch_block = _FETCH_BLOCK
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    table_rows = []
    for row_idx, row in enumerate(rows):
        table_rows.append(
            f"""
            <tr>
              <td>{_esc(row.get("issue"))}</td>
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(row.get("match_no"))}</span>
                  {_badge(str(row.get("level") or "无数据"))}
                </div>
                <strong>{_esc(row.get("home_team"))} vs {_esc(row.get("away_team"))}</strong>
                <div class="muted">{_league_badge(row.get("league"), row.get("league_color"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td>{_kind_badge(str(row.get("kind") or ""), muted=_kind_muted(row))}{_split_html(row, f'splitpop-{row_idx}')}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_lean_cell(row)}</td>

              <td class="total-cell">
                {_total_goals_html(row.get('payload_json') or '')}
              </td>
            </tr>
            """
        )
    empty = ""
    if not rows:
        empty = '<div class="no-alert">没有符合条件的异动记录。先运行分析入库后再查询。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>变盘异动查询</title>
  <style>
    :root {{
      --canvas: #f3f5f7; --panel: #ffffff; --ink: #1d2733; --muted: #64748b;
      --line: #d9dfe6; --red: #b42318; --red-soft: #fff1ef; --green: #16705a;
      --green-soft: #eaf8f2; --amber: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }}
    h1 {{ margin: 4px 0 6px; font-size: 28px; }}
    .meta, .muted {{ color: var(--muted); }}
    form {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 18px 0; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }}
    input, select, button {{ padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }}
    button {{ background: #1d2733; color: #fff; cursor: pointer; }}
    .checks {{ display: flex; gap: 12px; align-items: center; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; color: #475569; white-space: nowrap; }}
    .match-cell {{ min-width: 220px; white-space: normal; }}
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
    .alert-table .match-cell {{ width: 26%; }}
    .alert-table .line-change {{ width: 28%; }}
    .alert-table .score-cell {{ width: 9%; }}
.alert-table .lean-cell {{ width: 18%; }}
.alert-table .total-cell {{ width: 12%; }}
.report-btn {{ background: var(--green); color: #fff; margin-left: 8px; }}
.report-btn:disabled {{ background: #cbd5e1; cursor: not-allowed; }}

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
  #split-pop {{ position: fixed; z-index: 40; min-width: 240px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .82); backdrop-filter: blur(3px); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); pointer-events: none; }}
  #split-pop[hidden] {{ display: none; }}
  .split-card + .split-card {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }}
  .split-card-title {{ margin-bottom: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }}
  .split-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .split-table th {{ padding: 3px 10px 5px 0; background: transparent; color: var(--muted); font-weight: 600; text-align: left; white-space: nowrap; }}
  .split-table td {{ padding: 3px 10px 3px 0; border-top: 1px solid var(--line); color: #2b3644; white-space: nowrap; }}
  .split-table td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .split-table b {{ color: var(--amber); font-weight: 700; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .alert-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>变盘异动查询</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    {f'<div class="meta muted">当前服务端口：{_esc(port)}</div>' if port else ''}
    <form method="get">
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
      <div class="checks">
        <label><input type="checkbox" name="attention" value="1" {"checked" if attention_only else ""}> 只看异动</label>
        <label><input type="checkbox" name="latest" value="1" {"checked" if latest_only else ""}> 每期最新一次</label>
      </div>
      <button type="submit">查询</button>
      <button type="button" id="report-btn" class="report-btn">分析报告</button>
      <button type="button" id="review-btn" class="report-btn">复盘</button>
      <label class="muted" style="font-size:12px"><input type="checkbox" id="review-refresh" checked> 刷新比分</label>
      <span id="report-hint" class="muted" style="font-size:12px"></span>
      <span id="review-hint" class="muted" style="font-size:12px"></span>
    </form>
    <script>
    (function() {{
      const sel = document.querySelector('select[name="issue"]');
      const btn = document.getElementById('report-btn');
      const hint = document.getElementById('report-hint');
      const reports = {json.dumps(report_links, ensure_ascii=False)};
      const rbtn = document.getElementById('review-btn');
      const rhint = document.getElementById('review-hint');
      const rrefresh = document.getElementById('review-refresh');
      function sync() {{
        const v = sel ? sel.value : "";
        const file = v ? (reports[v] || null) : null;
        btn.disabled = !file;
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
        rhint.textContent = "复盘 " + v + " 中...";
        await fetch("/review", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{issue: v, refresh: !!(rrefresh && rrefresh.checked)}})
        }});
        const poll = setInterval(async () => {{
          const r = await fetch("/review-status");
          const s = await r.json();
          if (s.running) return;
          clearInterval(poll);
          rbtn.disabled = false;
          rhint.textContent = s.error || s.result || "完成";
          // 只有勾了「刷新比分」才需要重载看新比分；重载前把结果暂存，载入后还原。
          if (s.result && !s.error && rrefresh && rrefresh.checked) {{
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
            <th>期号</th><th>比赛</th><th>比分</th><th>类型</th><th>盘口变化</th><th>倾向</th><th>大小球</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
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
"""    <form class="fetch-bar" id="fetch-form">
      <label>抓取期号
        <input name="issue" placeholder="如 26120" style="width:120px">
      </label>
      <button type="submit">抓取并入库</button>
      <button type="button" id="refresh-odds-btn">刷新盘口分歧</button>
      <button type="button" id="ai-analyze-btn" style="background:#2563eb;color:#fff;border-color:#2563eb" title="读 500 数据分析页球队资料，大模型给出亚盘赢盘倾向（只展示，不改推荐）">AI 分析</button>
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
            if (s.running) {
              aiStatus.textContent = "AI 分析 " + s.issue + " 中...";
              _renderAiProgress(s);
              return;
            }
            clearInterval(poll);
            aiBtn.disabled = false;
            aiStatus.textContent = s.error || s.result || "完成";
            _renderAiProgress(null);
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
) -> str:
    from .browser_report import render_browser_html
    report_links = report_links or {}
    leagues = leagues or []
    fetch_block = _FETCH_BLOCK
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    table_rows = []
    for row_idx, row in enumerate(rows):
        table_rows.append(
            f"""
            <tr>
              <td>{_esc(row.get("issue"))}</td>
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(row.get("match_no"))}</span>
                  {_badge(str(row.get("level") or "无数据"))}
                </div>
                <strong>{_esc(row.get("home_team"))} vs {_esc(row.get("away_team"))}</strong>
                <div class="muted">{_league_badge(row.get("league"), row.get("league_color"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td>{_kind_badge(str(row.get("kind") or ""), muted=_kind_muted(row))}{_split_html(row, f'splitpop-{row_idx}')}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_lean_cell(row)}</td>

              <td class="total-cell">
                {_total_goals_html(row.get('payload_json') or '')}
              </td>
            </tr>
            """
        )
    empty = ""
    if not rows:
        empty = '<div class="no-alert">没有符合条件的异动记录。先运行分析入库后再查询。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>变盘异动查询</title>
  <style>
    :root {{
      --canvas: #f3f5f7; --panel: #ffffff; --ink: #1d2733; --muted: #64748b;
      --line: #d9dfe6; --red: #b42318; --red-soft: #fff1ef; --green: #16705a;
      --green-soft: #eaf8f2; --amber: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }}
    h1 {{ margin: 4px 0 6px; font-size: 28px; }}
    .meta, .muted {{ color: var(--muted); }}
    form {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 18px 0; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }}
    input, select, button {{ padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }}
    button {{ background: #1d2733; color: #fff; cursor: pointer; }}
    .checks {{ display: flex; gap: 12px; align-items: center; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; color: #475569; white-space: nowrap; }}
    .match-cell {{ min-width: 220px; white-space: normal; }}
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
    .alert-table .match-cell {{ width: 26%; }}
    .alert-table .line-change {{ width: 28%; }}
    .alert-table .score-cell {{ width: 9%; }}
.alert-table .lean-cell {{ width: 18%; }}
.alert-table .total-cell {{ width: 12%; }}
.report-btn {{ background: var(--green); color: #fff; margin-left: 8px; }}
.report-btn:disabled {{ background: #cbd5e1; cursor: not-allowed; }}

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
  #split-pop {{ position: fixed; z-index: 40; min-width: 240px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .82); backdrop-filter: blur(3px); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); pointer-events: none; }}
  #split-pop[hidden] {{ display: none; }}
  .split-card + .split-card {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }}
  .split-card-title {{ margin-bottom: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }}
  .split-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .split-table th {{ padding: 3px 10px 5px 0; background: transparent; color: var(--muted); font-weight: 600; text-align: left; white-space: nowrap; }}
  .split-table td {{ padding: 3px 10px 3px 0; border-top: 1px solid var(--line); color: #2b3644; white-space: nowrap; }}
  .split-table td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .split-table b {{ color: var(--amber); font-weight: 700; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .alert-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>变盘异动查询</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    {f'<div class="meta muted">当前服务端口：{_esc(port)}</div>' if port else ''}
    <form method="get">
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
      <div class="checks">
        <label><input type="checkbox" name="attention" value="1" {"checked" if attention_only else ""}> 只看异动</label>
        <label><input type="checkbox" name="latest" value="1" {"checked" if latest_only else ""}> 每期最新一次</label>
      </div>
      <button type="submit">查询</button>
      <button type="button" id="report-btn" class="report-btn">分析报告</button>
      <button type="button" id="review-btn" class="report-btn">复盘</button>
      <label class="muted" style="font-size:12px"><input type="checkbox" id="review-refresh" checked> 刷新比分</label>
      <span id="report-hint" class="muted" style="font-size:12px"></span>
      <span id="review-hint" class="muted" style="font-size:12px"></span>
    </form>
    <script>
    (function() {{
      const sel = document.querySelector('select[name="issue"]');
      const btn = document.getElementById('report-btn');
      const hint = document.getElementById('report-hint');
      const reports = {json.dumps(report_links, ensure_ascii=False)};
      const rbtn = document.getElementById('review-btn');
      const rhint = document.getElementById('review-hint');
      const rrefresh = document.getElementById('review-refresh');
      function sync() {{
        const v = sel ? sel.value : "";
        const file = v ? (reports[v] || null) : null;
        btn.disabled = !file;
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
        rhint.textContent = "复盘 " + v + " 中...";
        await fetch("/review", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{issue: v, refresh: !!(rrefresh && rrefresh.checked)}})
        }});
        const poll = setInterval(async () => {{
          const r = await fetch("/review-status");
          const s = await r.json();
          if (s.running) return;
          clearInterval(poll);
          rbtn.disabled = false;
          rhint.textContent = s.error || s.result || "完成";
          // 只有勾了「刷新比分」才需要重载看新比分；重载前把结果暂存，载入后还原。
          if (s.result && !s.error && rrefresh && rrefresh.checked) {{
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
            <th>期号</th><th>比赛</th><th>比分</th><th>类型</th><th>盘口变化</th><th>倾向</th><th>大小球</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
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
"""    <form class="fetch-bar" id="fetch-form">
      <label>抓取期号
        <input name="issue" placeholder="如 26120" style="width:120px">
      </label>
      <button type="submit">抓取并入库</button>
      <button type="button" id="refresh-odds-btn">刷新盘口分歧</button>
      <button type="button" id="ai-analyze-btn" style="background:#2563eb;color:#fff;border-color:#2563eb" title="读 500 数据分析页球队资料，大模型给出亚盘赢盘倾向（只展示，不改推荐）">AI 分析</button>
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
) -> str:
    from .browser_report import render_browser_html
    report_links = report_links or {}
    leagues = leagues or []
    fetch_block = _FETCH_BLOCK
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    table_rows = []
    for row_idx, row in enumerate(rows):
        table_rows.append(
            f"""
            <tr>
              <td>{_esc(row.get("issue"))}</td>
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(row.get("match_no"))}</span>
                  {_badge(str(row.get("level") or "无数据"))}
                </div>
                <strong>{_esc(row.get("home_team"))} vs {_esc(row.get("away_team"))}</strong>
                <div class="muted">{_league_badge(row.get("league"), row.get("league_color"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td>{_kind_badge(str(row.get("kind") or ""), muted=_kind_muted(row))}{_split_html(row, f'splitpop-{row_idx}')}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_lean_cell(row)}</td>

              <td class="total-cell">
                {_total_goals_html(row.get('payload_json') or '')}
              </td>
            </tr>
            """
        )
    empty = ""
    if not rows:
        empty = '<div class="no-alert">没有符合条件的异动记录。先运行分析入库后再查询。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>变盘异动查询</title>
  <style>
    :root {{
      --canvas: #f3f5f7; --panel: #ffffff; --ink: #1d2733; --muted: #64748b;
      --line: #d9dfe6; --red: #b42318; --red-soft: #fff1ef; --green: #16705a;
      --green-soft: #eaf8f2; --amber: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; }}
    .page {{ max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }}
    h1 {{ margin: 4px 0 6px; font-size: 28px; }}
    .meta, .muted {{ color: var(--muted); }}
    form {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 18px 0; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    label {{ display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }}
    input, select, button {{ padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }}
    button {{ background: #1d2733; color: #fff; cursor: pointer; }}
    .checks {{ display: flex; gap: 12px; align-items: center; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    th {{ background: #f8fafc; color: #475569; white-space: nowrap; }}
    .match-cell {{ min-width: 220px; white-space: normal; }}
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
    .alert-table .match-cell {{ width: 26%; }}
    .alert-table .line-change {{ width: 28%; }}
    .alert-table .score-cell {{ width: 9%; }}
.alert-table .lean-cell {{ width: 18%; }}
.alert-table .total-cell {{ width: 12%; }}
.report-btn {{ background: var(--green); color: #fff; margin-left: 8px; }}
.report-btn:disabled {{ background: #cbd5e1; cursor: not-allowed; }}

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
  #split-pop {{ position: fixed; z-index: 40; min-width: 240px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .82); backdrop-filter: blur(3px); box-shadow: 0 8px 24px rgba(15, 23, 42, .18); pointer-events: none; }}
  #split-pop[hidden] {{ display: none; }}
  .split-card + .split-card {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }}
  .split-card-title {{ margin-bottom: 6px; color: var(--muted); font-size: 11px; font-weight: 600; }}
  .split-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .split-table th {{ padding: 3px 10px 5px 0; background: transparent; color: var(--muted); font-weight: 600; text-align: left; white-space: nowrap; }}
  .split-table td {{ padding: 3px 10px 3px 0; border-top: 1px solid var(--line); color: #2b3644; white-space: nowrap; }}
  .split-table td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .split-table b {{ color: var(--amber); font-weight: 700; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .alert-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>变盘异动查询</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    {f'<div class="meta muted">当前服务端口：{_esc(port)}</div>' if port else ''}
    <form method="get">
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
      <div class="checks">
        <label><input type="checkbox" name="attention" value="1" {"checked" if attention_only else ""}> 只看异动</label>
        <label><input type="checkbox" name="latest" value="1" {"checked" if latest_only else ""}> 每期最新一次</label>
      </div>
      <button type="submit">查询</button>
      <button type="button" id="report-btn" class="report-btn">分析报告</button>
      <button type="button" id="review-btn" class="report-btn">复盘</button>
      <label class="muted" style="font-size:12px"><input type="checkbox" id="review-refresh" checked> 刷新比分</label>
      <span id="report-hint" class="muted" style="font-size:12px"></span>
      <span id="review-hint" class="muted" style="font-size:12px"></span>
    </form>
    <script>
    (function() {{
      const sel = document.querySelector('select[name="issue"]');
      const btn = document.getElementById('report-btn');
      const hint = document.getElementById('report-hint');
      const reports = {json.dumps(report_links, ensure_ascii=False)};
      const rbtn = document.getElementById('review-btn');
      const rhint = document.getElementById('review-hint');
      const rrefresh = document.getElementById('review-refresh');
      function sync() {{
        const v = sel ? sel.value : "";
        const file = v ? (reports[v] || null) : null;
        btn.disabled = !file;
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
        rhint.textContent = "复盘 " + v + " 中...";
        await fetch("/review", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{issue: v, refresh: !!(rrefresh && rrefresh.checked)}})
        }});
        const poll = setInterval(async () => {{
          const r = await fetch("/review-status");
          const s = await r.json();
          if (s.running) return;
          clearInterval(poll);
          rbtn.disabled = false;
          rhint.textContent = s.error || s.result || "完成";
          // 只有勾了「刷新比分」才需要重载看新比分；重载前把结果暂存，载入后还原。
          if (s.result && !s.error && rrefresh && rrefresh.checked) {{
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
            <th>期号</th><th>比赛</th><th>比分</th><th>类型</th><th>盘口变化</th><th>倾向</th><th>大小球</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
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
