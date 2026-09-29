from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable




def _kickoff_key(item: dict) -> str:
    """Return a comparable time key; missing times sort to the end of their group."""
    match_time = str(item.get("match_time") or "").strip()
    return match_time if match_time else "9999-99-99 99:99"
def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _history_short(record: dict) -> str:
    if not record or not record.get("matches"):
        return "无"
    return (
        f"{record['matches']}场 {record['wins']}胜{record['draws']}平"
        f"{record['losses']}负 进{record['goals_for']}失{record['goals_against']}"
    )


def _h2h_short(h2h: dict | None) -> str:
    if not h2h or h2h.get("_error") or not h2h.get("total"):
        return "无"
    return (
        f"{h2h['total']}场 {h2h['home_wins']}胜{h2h['draws']}平"
        f"{h2h['away_wins']}负 进{h2h['goals_for']}失{h2h['goals_against']}"
    )


def _asian_display(item: dict) -> str:
    market = item.get("market", {})
    asian = market.get("asian", {})
    if not market.get("has_asian"):
        return "无"
    line = str(asian.get("line_text") or "")
    home_water = _float(asian.get("home_water"))
    away_water = _float(asian.get("away_water"))
    if home_water or away_water:
        return f"{line} {home_water:.2f}/{away_water:.2f}".strip()
    return line or "无"


def _total_display(item: dict) -> str:
    market = item.get("market", {})
    total = market.get("total", {})
    if not market.get("has_total"):
        return "无"
    line = str(total.get("line_text") or "")
    over_water = _float(total.get("over_water"))
    under_water = _float(total.get("under_water"))
    source = str(total.get("source") or "")
    suffix = f"（{source}）" if source else ""
    if over_water or under_water:
        return f"{line} {over_water:.2f}/{under_water:.2f}{suffix}".strip()
    return (line or "无") + suffix


def _movement_level(item: dict) -> str:
    movement = item.get("odds_movement") or {}
    return str(movement.get("level") or "无数据")


def _movement_reason(item: dict) -> str:
    movement = item.get("odds_movement") or {}
    return "；".join(str(reason) for reason in movement.get("reasons") or [])


def _movement_kind(item: dict) -> str:
    movement = item.get("odds_movement") or {}
    kind = str(movement.get("kind") or "").strip()
    if kind == "退盘":
        return "降盘"
    if kind:
        return kind
    reason = _movement_reason(item)
    for fallback, marker in (
        ("降盘", "让球盘降盘"),
        ("降盘", "让球盘退盘"),
        ("升盘", "让球盘升盘"),
        ("水位反转", "亚盘水位反转"),
        ("水位摆动", "亚盘水位摆动"),
        ("大盘变动", "大小盘升"),
        ("大盘变动", "大小盘降"),
        ("大水位摆动", "大小盘水位摆动"),
    ):
        if marker in reason:
            return fallback
    return "关注"


def _kind_badge(kind: str, *, muted: bool = False) -> str:
    css = {
        "降盘": "kind-drop",
        "升盘": "kind-rise",
        "水位反转": "kind-reverse",
        "水位摆动": "kind-swing",
        "大盘变动": "kind-swing",
        "大水位摆动": "kind-swing",
    }.get(kind, "kind-watch")
    if muted and kind in ("降盘", "升盘", "大盘变动", "大水位摇动"):
        css += " kind-muted"
    return f'<span class="kind {css}">{_esc(kind)}</span>'

def _kind_muted(item: dict) -> bool:
    """Return True when the kind badge should lose its colored background (|line-change| < 0.5)."""
    movement = item.get("odds_movement") or {}
    kind = str(movement.get("kind") or "").strip()
    if kind not in ("升盘", "降盘", "大盘变动", "大水位摇动"):
        return False
    asian = movement.get("asian") or {}
    total = movement.get("total") or {}
    lc_asian = abs(_float(asian.get("line_change")))
    lc_total = abs(_float(total.get("line_change")))
    lc = lc_total if kind in ("大盘变动", "大水位摇动") else lc_asian
    return lc < 0.5



def _asian_rec_text(item: dict) -> str:
    asian = (item.get("odds_analysis") or {}).get("asian_handicap")
    return _rec_cell(asian, "主队赢盘")


def _recommendation_summary(analyses: list[dict]) -> list[tuple[int, dict]]:
    rows = [
        (int(item.get("odds_analysis", {}).get("rank") or 0), item)
        for item in analyses
    ]
    rows.sort(key=lambda pair: (-pair[0], int(pair[1].get("match_no") or 0)))
    return rows


def _rec_cell(rec: dict | None, fallback: str) -> str:
    if not rec or not rec.get("available"):
        return "无市场"
    action = rec.get("action") or "观望"
    value = rec.get("recommended_side") or rec.get("final_direction") or fallback
    if action == "观望":
        return f"{value}?"
    return f"{value}（{action}）"

def _total_movement_old_new(item: dict) -> tuple[str, str]:
    total = (item.get("odds_movement") or {}).get("total") or {}
    if not total.get("available") or not total.get("initial") or not total.get("current"):
        return "无数据", ""

    def one(side: dict) -> str:
        line = str(side.get("line_display") or side.get("line_text") or "")
        over_water = _float(side.get("over_water"))
        under_water = _float(side.get("under_water"))
        water = f"{over_water:.2f}/{under_water:.2f}" if over_water or under_water else ""
        return f"{line} {water}".strip() or "无数据"

    return one(total.get("initial", {})), one(total.get("current", {}))

def _movement_old_new(item: dict) -> tuple[str, str]:
    asian = (item.get("odds_movement") or {}).get("asian") or {}
    if not asian.get("available") or not asian.get("initial") or not asian.get("current"):
        return "无数据", ""

    def one(side: dict) -> str:
        line = str(side.get("line_display") or side.get("line_text") or "")
        home_water = _float(side.get("home_water"))
        away_water = _float(side.get("away_water"))
        water = f"{home_water:.2f}/{away_water:.2f}" if home_water or away_water else ""
        return f"{line} {water}".strip() or "无数据"

    return one(asian.get("initial", {})), one(asian.get("current", {}))


def _badge(level: str) -> str:
    css = "stable"
    if level == "重点关注":
        css = "hot"
    elif level == "关注":
        css = "watch"
    return f'<span class="badge badge-{css}">{_esc(level)}</span>'


def _sorted_matches(analyses: list[dict]) -> list[dict]:
    return sorted(analyses, key=lambda item: int(item.get("match_no") or 0))


def _movement_delta(item: dict) -> str:
    asian = (item.get("odds_movement") or {}).get("asian") or {}
    if not asian.get("available"):
        return ""
    pieces = []
    if asian.get("direction"):
        line_delta = _float(asian.get("line_change"))
        line_text = ""
        if abs(line_delta) > 0.001:
            line_text = f"{line_delta:+.2f}"
        pieces.append(f"{asian['direction']}{line_text}")
    for label, key in (("主水", "home_water_change"), ("客水", "away_water_change")):
        delta = _float(asian.get(key))
        if abs(delta) >= 0.01:
            pieces.append(f"{label}{delta:+.2f}")
    return " ".join(pieces)


def _asian_suggestion(item: dict) -> str:
    rec = ((item.get("odds_analysis") or {}).get("asian_handicap") or {})
    if not rec.get("available"):
        return ""
    return str(rec.get("recommended_side") or rec.get("final_direction") or "").strip()


def _movement_lean(item: dict) -> str:
    asian = (item.get("odds_movement") or {}).get("asian") or {}
    if asian.get("direction") and asian.get("lean"):
        return str(asian["lean"])
    return ""


def _movement_tag(item: dict) -> tuple[str, str]:
    lean = _movement_lean(item)
    suggestion = _asian_suggestion(item)
    if not lean or not suggestion:
        return "", ""
    same_side = (
        ("主队" in lean and "主队" in suggestion)
        or ("客队" in lean and "客队" in suggestion)
        or ("赏走" in lean and "赏走" in suggestion)
        or ("走盘" in lean and "走盘" in suggestion)
    )
    return ("共振", "sync") if same_side else ("分歧", "conflict")


def _render_stats(
    analyses: list[dict],
    issue: str,
    generated_at: datetime,
) -> str:
    total = len(analyses)
    attention = sum(
        1 for item in analyses if (item.get("odds_movement") or {}).get("attention")
    )
    available = sum(
        1
        for item in analyses
        if (item.get("odds_movement") or {}).get("available")
    )
    return f"""
    <div class="masthead">
      <div class="title-block">
        <div class="eyebrow">足球分析系统</div>
        <h1>胜负彩 {_esc(issue)}</h1>
        <div class="meta">生成时间：{_esc(generated_at.strftime('%Y-%m-%d %H:%M:%S'))}</div>
      </div>
      <div class="stats">
        <div class="stat"><strong>{total}</strong><span>比赛场次</span></div>
        <div class="stat stat-alert"><strong>{attention}</strong><span>变盘异动</span></div>
        <div class="stat"><strong>{available}/{total}</strong><span>亚盘覆盖</span></div>
      </div>
    </div>
    """


def _alert_items(analyses: list[dict]) -> list[dict]:
    items = [
        item
        for item in analyses
        if (item.get("odds_movement") or {}).get("attention")
    ]
    items.sort(
        key=lambda item: (
            _kickoff_key(item),
            int(item.get("match_no") or 999),
            str(item.get("home_team") or ""),
            str(item.get("away_team") or ""),
        )
    )
    return items


def _render_alert_rows(analyses: list[dict]) -> str:
    rows: list[str] = []
    for item in _alert_items(analyses):
        level = _movement_level(item)
        kind = _movement_kind(item)
        old, new = _movement_old_new(item)
        lean = _movement_lean(item)
        total_old, total_new = _total_movement_old_new(item)
        delta_html = f'<div class="line-delta">{_esc(_movement_delta(item))}</div>' if _movement_delta(item) else ""
        tag_label, tag_css = _movement_tag(item)
        tag_html = (
            f'<div class="movement-tag {tag_css}">{_esc(tag_label)}</div>'
            if tag_label
            else ""
        )
        rows.append(
            f"""
            <tr data-alert="1" data-alert-kind="{_esc(kind)}">
              <td class="match-cell">
                <div class="match-head">
                  <span class="cell-no">{_esc(item.get("match_no"))}</span>
                  {_badge(level)}
                </div>
                <strong>{_esc(item.get("home_team"))} vs {_esc(item.get("away_team"))}</strong>
                <div class="muted">{_esc(item.get("league"))} · {_esc(item.get("match_time"))}</div>
              </td>
              <td class="score-cell">{_esc(item.get("final_score") or "未开赛")}</td>
<td class="kind-cell">{_kind_badge(kind, muted=_kind_muted(item))}</td>
              <td class="line-change">
                <div class="line-pair"><span class="k">初</span>{_esc(old)}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(new)}</div>
                {delta_html}
              </td>
              <td class="lean-cell">{_esc(lean or "-")}</td>
              <td class="asian-rec">
                <div>{_esc(_asian_rec_text(item))}</div>
                {tag_html}
              </td>
              <td class="total-cell">
                <div class="line-pair"><span class="k">初</span>{_esc(total_old)}</div>
                <div class="line-pair"><span class="k">即</span>{_esc(total_new)}</div>
              </td>
              <td class="analysis-link">{f'<a href="{_esc(item.get("analysis_url"))}" target="_blank" rel="noreferrer">分析</a>' if item.get("analysis_url") else ""}</td>
            </tr>
            """
        )
        narration = item.get("narration")
        if narration:
            rows.append(
                f'<tr data-alert-kind="{_esc(kind)}" class="narration-row">'
                f'<td colspan="8">'
                f'<span class="narration-label">大模型解读 · 仅依据已采集数据</span>'
                f"{_esc(narration)}"
                f"</td></tr>"
            )
    return "\n".join(rows)


def _render_summary_rows(analyses: list[dict]) -> str:
    rows: list[str] = []
    for _rank, item in _recommendation_summary(analyses):
        odds = item.get("odds_analysis", {})
        level = _movement_level(item)
        alert_css = "hot" if level == "重点关注" else "watch" if level == "关注" else "stable"
        rows.append(
            f"""
            <tr>
              <td class="cell-no">{_esc(item.get('match_no'))}</td>
              <td>{_esc(item.get('league'))}</td>
              <td class="time-cell">{_esc(item.get('match_time'))}</td>
              <td><strong>{_esc(item.get('home_team'))} vs {_esc(item.get('away_team'))}</strong></td>
              <td>{_esc(_rec_cell(odds.get('asian_handicap'), '主队赢盘'))}</td>
              <td>{_esc(_total_display(item))}</td>
              <td><span class="badge badge-{alert_css}">{_esc(level if level in ("重点关注", "关注") else "-")}</span></td>
            </tr>
            """
        )
    return "\n".join(rows)


def _render_detail_rows(analyses: list[dict]) -> str:
    rows: list[str] = []
    for item in _sorted_matches(analyses):
        alert = "1" if (item.get("odds_movement") or {}).get("attention") else "0"
        level = _movement_level(item)
        home_history = _history_short((item.get("history") or {}).get("home"))
        away_history = _history_short((item.get("history") or {}).get("away"))
        h2h = _h2h_short((item.get("history") or {}).get("h2h"))
        rows.append(
            f"""
            <tr data-match data-alert="{alert}" data-level="{_esc(level)}">
              <td class="cell-no">{_esc(item.get('match_no'))}</td>
              <td>
                <strong>{_esc(item.get('home_team'))} vs {_esc(item.get('away_team'))}</strong>
                <div class="muted">{_esc(item.get('league'))}</div>
              </td>
              <td class="time-cell">{_esc(item.get('match_time'))}</td>
              <td>{_esc(_asian_display(item))}</td>
              <td>{_esc(_total_display(item))}</td>
              <td class="compact-cell">{_esc(home_history)}</td>
              <td class="compact-cell">{_esc(away_history)}</td>
              <td class="compact-cell">{_esc(h2h)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def _render_body(analyses: list[dict], issue: str, source_url: str, generated_at: datetime) -> str:
    attention = sum(
        1 for item in analyses if (item.get("odds_movement") or {}).get("attention")
    )
    alert_items = _alert_items(analyses)
    alert_rows = (
        _render_alert_rows(analyses)
        if alert_items
        else '<tr class="empty-alert"><td colspan="5">本场没有达到关注线的亚洲盘让球变化。</td></tr>'
    )
    summary_rows = _render_summary_rows(analyses)
    detail_rows = _render_detail_rows(analyses)
    source_link = (
        f'<a href="{_esc(source_url)}" target="_blank" rel="noreferrer">'
        f"{_esc(source_url)}</a>"
    )
    return f"""
    {_render_stats(analyses, issue, generated_at)}
    <section class="alert-section" aria-label="变盘异动提醒">
      <div class="section-heading">
        <div>
          <h2>变盘异动提醒</h2>
          <div class="sub">上盘=让球方，下盘=受让方。上盘让球增加为升盘，减少为降盘。赛前指数快照，非完整逐笔时间线。</div>
        </div>
        <span class="count-badge">{attention}</span>
      </div>
      <div class="toolbar alert-toolbar">
        <div class="segmented" role="group" aria-label="异动类型筛选">
          <button class="active" type="button" data-alert-filter="all">全部</button>
          <button type="button" data-alert-filter="升盘">升盘</button>
          <button type="button" data-alert-filter="降盘">降盘</button>
          <button type="button" data-alert-filter="水位反转">水位反转</button>
          <button type="button" data-alert-filter="水位摆动">水位摆动</button>
          <button type="button" data-alert-filter="大盘变动">大盘变动</button>
        </div>
      </div>
      <div class="table-scroll alert-scroll">
        <table class="alert-table">
          <thead>
            <tr>
              <th>比赛</th>
              <th>比分</th>
              <th>类型</th>
              <th>盘口变化</th>
              <th>倾向</th>
              <th>亚盘建议</th>
              <th>大小球</th>
              <th>分析</th>
            </tr>
          </thead>
          <tbody>
            {alert_rows}
          </tbody>
        </table>
      </div>
      <div id="alert-empty" class="no-alert" hidden>没有符合筛选的异动。</div>
    </section>
    <section class="table-section">
      <div class="section-heading">
        <h2>整期推荐</h2>
      </div>
      <div class="table-scroll">
        <table id="summary-table">
          <thead>
            <tr>
              <th>场</th>
              <th>赛事</th>
              <th>比赛时间</th>
              <th>主队 vs 客队</th>
              <th>亚盘让球</th>
              <th>大小球</th>
              <th>变盘异动</th>
            </tr>
          </thead>
          <tbody>
            {summary_rows}
          </tbody>
        </table>
      </div>
    </section>
    <section>
      <div class="section-heading">
        <h2>全场比赛明细</h2>
      </div>
      <div class="toolbar">
        <div class="segmented" role="group" aria-label="比赛筛选">
          <button class="active" type="button" data-filter="all">全部</button>
          <button type="button" data-filter="alert">只看异动</button>
          <button type="button" data-filter="focus">重点关注</button>
        </div>
        <input id="matchSearch" type="search" placeholder="搜索球队或联赛" aria-label="搜索比赛">
      </div>
      <div class="table-scroll">
        <table id="detail-table">
          <thead>
            <tr>
              <th>场</th>
              <th>比赛</th>
              <th>比赛时间</th>
              <th>亚盘让球</th>
              <th>大小球</th>
              <th>主队近10</th>
              <th>客队近10</th>
              <th>历史交锋</th>
            </tr>
          </thead>
          <tbody>
            {detail_rows}
          </tbody>
        </table>
      </div>
    </section>
    <footer class="disclaimer">
      赛果分析由模型与市场先验融合，仅供研究参考，不构成投注建议。数据源：{source_link}
    </footer>
    """


def render_browser_html(
    analyses: list[dict],
    issue: str,
    source_url: str,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now()
    body = _render_body(analyses, issue, source_url, generated_at)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>胜负彩 {html.escape(issue or "当期")} 足球分析</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="page">
{body}
  </div>
  <script>{_SCRIPT}</script>
</body>
</html>
"""


_CSS = """
:root {
  --canvas: #f3f5f7;
  --panel: #ffffff;
  --ink: #1d2733;
  --muted: #64748b;
  --line: #d9dfe6;
  --blue: #2f6fed;
  --amber: #b45309;
  --amber-soft: #fff7ed;
  --red: #b42318;
  --red-soft: #fff1ef;
  --green: #16705a;
  --green-soft: #eaf8f2;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--canvas);
  color: var(--ink);
  font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  font-size: 14px;
}
.page { max-width: 1360px; margin: 0 auto; padding: 26px 20px 54px; }
.masthead {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}
.title-block { min-width: 0; }
.eyebrow { color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: 0; }
h1 { margin: 4px 0 6px; font-size: 28px; line-height: 1.25; letter-spacing: 0; }
h2 { margin: 0; font-size: 18px; line-height: 1.4; letter-spacing: 0; }
.meta, .sub, .muted { color: var(--muted); }
.meta { margin-top: 2px; }
.stats { display: flex; gap: 10px; flex-wrap: wrap; }
.stat {
  min-width: 104px;
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.stat strong { display: block; font-size: 20px; line-height: 1.2; }
.stat span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
.stat-alert { border-color: #f2b8ac; background: var(--red-soft); }
.stat-alert strong { color: var(--red); }
.alert-section,
.table-section {
  margin-top: 22px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  overflow: hidden;
}
.alert-section { border-color: #efb7a8; }
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px 10px;
}
.sub { margin: 3px 0 0; font-size: 12px; }
.count-badge {
  min-width: 34px;
  padding: 4px 9px;
  border-radius: 8px;
  background: var(--red);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  text-align: center;
}
.table-scroll { overflow-x: auto; }
.alert-scroll { overflow-x: hidden; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.alert-table { table-layout: fixed; }
th, td { padding: 10px 12px; border-top: 1px solid var(--line); text-align: left; vertical-align: middle; }
th { background: #f8fafc; color: #475569; font-weight: 600; white-space: nowrap; }
td { color: #2b3644; }
.alert-table td { border-top-color: #f7d9ce; overflow-wrap: anywhere; word-break: break-word; }
.alert-section tbody tr:nth-child(even) { background: #fffcf8; }
.alert-section tbody tr:nth-child(odd) { background: #fff7f4; }
.cell-no { width: 44px; font-variant-numeric: tabular-nums; }
.time-cell { min-width: 0; color: #475569; white-space: nowrap; font-variant-numeric: tabular-nums; }
.alert-table .match-cell { width: 28%; white-space: normal; }
.alert-table .match-cell strong { display: block; white-space: normal; }
.alert-table .match-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.alert-table .kind-cell { width: 78px; }
.alert-table .lean-cell { width: 18%; color: #334155; font-weight: 600; }
.line-change { width: 30%; white-space: normal; }
.line-pair { display: flex; gap: 6px; align-items: baseline; }
.line-pair .k { flex: 0 0 1.2em; color: var(--muted); font-size: 12px; }
.line-delta { margin-top: 5px; color: #475569; font-size: 12px; font-weight: 600; }
.movement-tag { margin-top: 5px; }
.movement-tag.sync { color: var(--green); }
.movement-tag.conflict { color: var(--amber); }
.alert-toolbar { margin: 0 16px 10px; }
.alert-table .asian-rec { min-width: 0; white-space: normal; }
.alert-table .score-cell { min-width: 56px; white-space: nowrap; font-weight: 600; }
.alert-table .total-cell { width: 110px; white-space: normal; }
.alert-table .total-cell .line-pair { font-size: 12px; }
.alert-table .analysis-link { width: 44px; text-align: center; }
.alert-table .analysis-link a { color: var(--blue); text-decoration: none; font-weight: 600; font-size: 13px; }
.alert-table .analysis-link a:hover { text-decoration: underline; }
.alert-table tbody tr.narration-row { background: #f6f9ff; }
.alert-table .narration-row td { padding-top: 0; border-top: 1px dashed var(--line); color: #334155; line-height: 1.7; }
.narration-label { display: inline-block; margin-right: 8px; padding: 1px 7px; border-radius: 5px; background: #e8efff; color: var(--blue); font-size: 11px; font-weight: 600; white-space: nowrap; }
.alert-table .empty-alert td { text-align: center; color: var(--green); background: var(--green-soft); }
.badge {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.badge-hot { background: var(--red-soft); color: var(--red); }
.badge-watch { background: #fff3d6; color: var(--amber); }
.badge-stable { background: var(--green-soft); color: var(--green); }
.kind {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid currentColor;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.kind-drop { color: #b42318; background: var(--red-soft); }
.kind-rise { color: #0f766e; background: var(--green-soft); }
.kind-reverse { color: #9a5b06; background: #fff3d6; }
.kind-swing { color: #475569; background: #eef2f7; }
.kind-watch { color: #475569; background: #eef2f7; }
.kind-muted { background: transparent; }
.asian-rec { min-width: 120px; white-space: nowrap; font-weight: 600; }
.no-alert { padding: 18px 16px; color: var(--green); background: var(--green-soft); }
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 10px 0;
}
.segmented { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--panel); }
.segmented button {
  border: 0;
  border-right: 1px solid var(--line);
  padding: 7px 14px;
  background: transparent;
  color: #334155;
  font-size: 13px;
  cursor: pointer;
}
.segmented button:last-child { border-right: 0; }
.segmented button.active { background: #1d2733; color: #fff; }
#matchSearch {
  width: 260px;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
}
.compact-cell { max-width: 210px; color: #475569; font-size: 13px; }
.disclaimer { margin-top: 18px; color: var(--muted); font-size: 12px; line-height: 1.7; }
.disclaimer a { color: var(--blue); text-decoration: none; }
@media (max-width: 760px) {
  .page { padding: 16px 10px 40px; }
  .masthead { display: block; }
  .stats { margin-top: 14px; }
  .stats .stat { flex: 1 1 90px; }
  .toolbar { align-items: stretch; flex-direction: column; }
  #matchSearch { width: 100%; }
}
"""


_SCRIPT = """
(() => {
  const table = document.getElementById("detail-table");
  const rows = Array.from(table ? table.querySelectorAll("tbody tr[data-match]") : []);
  const emptyRow = document.getElementById("empty-row");
  const search = document.getElementById("matchSearch");
  const buttons = Array.from(document.querySelectorAll(".segmented button"));
  const alertButtons = Array.from(document.querySelectorAll("[data-alert-filter]"));
  let filter = "all";
  let alertFilter = "all";

  function apply() {
    const query = (search && search.value.trim() || "").toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const isAlert = row.dataset.alert === "1";
      const level = row.dataset.level || "";
      const filterHit =
        filter === "all" ||
        (filter === "alert" && isAlert) ||
        (filter === "focus" && level === "重点关注");
      const searchHit = !query || row.textContent.toLowerCase().includes(query);
      const visible = filterHit && searchHit;
      row.hidden = !visible;
      if (visible) shown += 1;
    });
    if (emptyRow) emptyRow.hidden = shown > 0;
  }

  function applyAlertFilter() {
    const alertRows = Array.from(document.querySelectorAll(".alert-table tbody tr[data-alert-kind]"));
    let shown = 0;
    alertRows.forEach((row) => {
      const kind = row.dataset.alertKind || "";
      const visible = alertFilter === "all" || kind === alertFilter;
      row.hidden = !visible;
      if (visible) shown += 1;
    });
    const empty = document.getElementById("alert-empty");
    if (empty) empty.hidden = alertRows.length === 0 || shown > 0;
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      buttons.forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      filter = button.dataset.filter || "all";
      apply();
    });
  });

  alertButtons.forEach((button) => {
    button.addEventListener("click", () => {
      alertButtons.forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      alertFilter = button.dataset.alertFilter || "all";
      applyAlertFilter();
    });
  });

  if (search) search.addEventListener("input", apply);
  apply();
  applyAlertFilter();
})();
"""