from __future__ import annotations

import html
import json
from datetime import datetime


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _badge(level: str) -> str:
    css = "stable"
    if level == "重点关注":
        css = "hot"
    elif level == "关注":
        css = "watch"
    return f'<span class="badge badge-{css}">{_esc(level)}</span>'


def _kind_badge(kind: str) -> str:
    if kind == "退盘":
        kind = "降盘"
    css = {
        "降盘": "kind-drop",
        "升盘": "kind-rise",
        "水位反转": "kind-reverse",
        "水位摆动": "kind-swing",
    }.get(kind, "kind-watch")
    return f'<span class="kind {css}">{_esc(kind or "无")}</span>'


def _line_delta_html(row: dict) -> str:
    d = str(row.get("direction") or "").strip()
    try:
        lc = float(row.get("line_change") or 0)
    except (ValueError, TypeError):
        lc = 0.0
    # lc==0: 无让球线变化(如水位摆动) → 原样显示 direction，不加数字，不高亮
    if lc == 0:
        return f'<div class="line-delta">{_esc(d)}</div>' if d else ""
    label = f"升盘 +{abs(lc):g}" if lc > 0 else f"降盘 -{abs(lc):g}"
    cls = "delta-hot" if abs(lc) >= 0.5 else ""
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
    hot = abs_diff >= 0.5
    delta_html = ""
    if d:
        tag_cls = "delta-hot" if hot else ""
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
      <span id="fetch-status" class="muted"></span>
    </form>
    <script>
    const fetchForm = document.getElementById("fetch-form");
    const statusEl = document.getElementById("fetch-status");
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
    analyses: list[dict] = [],
    source_url: str = "",
) -> str:
    from .browser_report import render_browser_html
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
    table_rows = []
    for row in rows:
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
                <div class="muted">{_esc(row.get("league"))} · {_esc(row.get("match_time"))}</div>

              </td>
              <td class="score-cell">{_esc(row.get("final_score") or "未开赛")}</td>
              <td>{_kind_badge(str(row.get("kind") or ""))}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(row.get("initial_line") or "无数据")}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(row.get("current_line") or "无数据")}</div>
                {_line_delta_html(row)}
              </td>
              <td class="lean-cell">{_esc(row.get("lean") or "—")}</td>

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
    .score-cell {{ min-width: 56px; white-space: nowrap; font-weight: 600; color: var(--ink); }}
    .alert-table {{ table-layout: fixed; }}
    .alert-table .match-cell {{ width: 26%; }}
    .alert-table .line-change {{ width: 28%; }}
    .alert-table .score-cell {{ width: 9%; }}
.alert-table .lean-cell {{ width: 16%; }}
.alert-table .total-cell {{ width: 12%; }}

    .badge, .kind {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .badge-hot {{ background: var(--red-soft); color: var(--red); }}
    .badge-watch {{ background: #fff3d6; color: var(--amber); }}
    .badge-stable {{ background: var(--green-soft); color: var(--green); }}
    .kind-drop {{ color: #b42318; background: var(--red-soft); }}
    .kind-rise {{ color: #0f766e; background: var(--green-soft); }}
    .kind-reverse {{ color: #9a5b06; background: #fff3d6; }}
    .kind-swing, .kind-watch {{ color: #475569; background: #eef2f7; }}
    .no-alert {{ padding: 18px 16px; color: var(--green); background: var(--green-soft); }}
    .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>变盘异动查询</h1>
    <div class="meta">数据库：{_esc(db_path)}　生成时间：{_esc(generated_at)}　上盘=让球方，下盘=受让方；上盘让球增加为升盘，减少为降盘。</div>
    <form method="get">
      <label>期号
        <select name="issue">{''.join(issue_options)}</select>
      </label>
      <label>球队/联赛
        <input name="team" value="{_esc(team)}" placeholder="巴萨 / 欧冠">
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
    </form>
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
</body>
</html>
"""
