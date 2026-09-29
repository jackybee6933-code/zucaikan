from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .browser_report import render_browser_html
from .odds import fmt_handicap, fmt_probability





def _kickoff_key(item: dict) -> str:
    """Return a comparable time key; missing times sort to the end of their group."""
    match_time = str(item.get("match_time") or "").strip()
    return match_time if match_time else "9999-99-99 99:99"
def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _probs_text(probs: dict, labels: dict[str, str]) -> str:
    return " / ".join(
        f"{labels.get(key, key)} {_pct(probs.get(key, 0.0))}"
        for key in labels
        if key in probs
    )


def history_short(record: dict) -> str:
    if not record or not record.get('matches'):
        return "\u65e0"
    return (
        f"{record['matches']}\u573a {record['wins']}\u80dc{record['draws']}\u5e73{record['losses']}\u8d1f "
        f"\u8fdb{record['goals_for']}\u5931{record['goals_against']}"
    )
def h2h_short(h2h: dict | None) -> str:
    if not h2h or h2h.get("_error") or not h2h.get("total"):
        return "无"
    return (
        f"{h2h['total']}场 {h2h['home_wins']}胜{h2h['draws']}平"
        f"{h2h['away_wins']}负 进{h2h['goals_for']}失{h2h['goals_against']}"
    )


def asian_display(analysis: dict) -> str:
    market = analysis.get("market") or {}
    asian = market.get("asian") or {}
    if not market.get("has_asian"):
        return "无"
    line = str(asian.get("line_text") or "") or (fmt_handicap(float(asian["home_line"])) if asian.get("home_line") is not None else "")
    water = ""
    if asian.get("home_water") or asian.get("away_water"):
        water = f" {asian['home_water']:.2f}/{asian['away_water']:.2f}"
    return f"{line}{water}"


def total_display(analysis: dict) -> str:
    market = analysis.get("market") or {}
    total = market.get("total") or {}
    if not market.get("has_total"):
        return "无"
    water = ""
    if total.get("over_water") or total.get("under_water"):
        water = f" {total['over_water']:.2f}/{total['under_water']:.2f}"
    source = str(total.get("source") or "")
    suffix = f"（{source}）" if source else ""
    return f"{total['line_text']}{water}{suffix}"


def _rec_cell(rec: dict | None, fallback: str) -> str:
    if not rec or not rec.get("available"):
        return "无市场"
    action = rec.get("action") or "观望"
    value = rec.get("recommended_side") or rec.get("final_direction") or fallback
    if action == "观望":
        return f"{value}?"
    return f"{value}（{action}）"


def _market_rec_line(rec: dict | None) -> str:
    if not rec or not rec.get("available"):
        return "无市场"
    action = rec.get("action") or "观望"
    value = rec.get("recommended_side") or ""
    confidence = rec.get("confidence")
    if action == "观望":
        return f"{value}?"
    if confidence is not None:
        return f"{value}[{action} {_pct(float(confidence))}]"
    return f"{value}[{action}]"


def recommendation_summary(analyses: list[dict]) -> list[tuple[int, dict]]:
    rows = [
        (int(item.get("odds_analysis", {}).get("rank") or 0), item)
        for item in analyses
    ]
    rows.sort(key=lambda pair: (-pair[0], int(pair[1].get("match_no") or 0)))
    return rows


def movement_summary(analyses: list[dict]) -> list[dict]:
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


def render_markdown(analyses: list[dict], issue: str, source_url: str) -> str:
    lines: list[str] = []
    lines.append(f"# 胜负彩 {issue} 足球分析报告")
    lines.append("")
    lines.append(f"> 数据来源：{source_url}、竞彩列表、500 odds.xml、liansai 球队页、Titan 历史交锋")
    lines.append(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")
    lines.append("模型说明：独立泊松预期进球模型按近期往绩估算主客进球分布，再以 12%-16% 的独立模型权重与 84%-88% 的市场先验融合；市场概率优先来自 `gl avg`，缺失时用欧赔反推。报告仅供研究，不构成投注建议。")
    lines.append("")
    lines.append("**整期推荐汇总**")
    lines.append("")
    lines.append("| 场 | 赛事 | 主队 vs 客队 | 亚盘让球 | 大小盘 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for _rank, item in recommendation_summary(analyses):
        odds = item.get("odds_analysis", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    item["match_no"],
                    item["league"],
                    f"{item['home_team']} vs {item['away_team']}",
                    _rec_cell(odds.get("asian_handicap"), "主队赢盘"),
                    _rec_cell(odds.get("total_goals"), "大球"),
                ]
            )
            + " |"
        )
    attention_items = movement_summary(analyses)
    if attention_items:
        lines.append("")
        lines.append("**变盘异动提醒**")
        lines.append("")
        lines.append("以下为亚洲盘让球初盘到即时盘的明显变化：")
        lines.append("")
        for item in attention_items:
            movement = item.get("odds_movement") or {}
            reasons = "；".join(movement.get("reasons") or [])
            lines.append(
                f"- {item['match_no']} {item['home_team']} vs {item['away_team']} "
                f"[{movement.get('level', '关注')}] {reasons}"
            )
    lines.append("")
    lines.append("| 场 | 联赛/时间 | 主队 vs 客队 | 亚盘 | 大小盘 | 主队往绩 | 客队往绩 | 交锋 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    notes_by_no: dict[str, str] = {}
    for item in analyses:
        prediction = item["prediction"]
        market = item["market"]
        market_line = (
            "市场概率 " + " / ".join(_pct(market["probability_3way"][key]) for key in ("home", "draw", "away"))
            if any(market["probability_3way"].values())
            else "无市场"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    item["match_no"],
                    f"{item['league']} {item['match_time']}",
                    f"{item['home_team']} vs {item['away_team']}",
                    asian_display(item),
                    total_display(item),
                    history_short(item["history"]["home"]),
                    history_short(item["history"]["away"]),
                    h2h_short(item["history"].get("h2h")),
                ]
            )
            + " |"
        )
        notes = []
        if prediction.get("model_direction") and prediction.get("market_direction"):
            if prediction["model_direction"] != prediction["market_direction"]:
                notes.append(
                    f"模型看 {prediction['model_direction']}，市场看 {prediction['market_direction']}，方向不一致"
                )
        if prediction.get("action") == "观望":
            notes.append("数据/概率门槛不足，建议观望")
        for warning in item.get("warnings", []):
            notes.append(warning)
        if notes:
            notes_by_no[item["match_no"]] = (
                f"{item['home_team']} vs {item['away_team']}：{market_line}；"
                + "；".join(notes)
            )
    if notes_by_no:
        lines.append("")
        lines.append("**场次备注**")
        lines.append("")
        for match_no, note in notes_by_no.items():
            lines.append(f"- {match_no} {note}")
    lines.append("")
    lines.append("**赔率与盘口分析**")
    lines.append("")
    for item in analyses:
        odds = item.get("odds_analysis", {})
        parts: list[str] = []
        europe = odds.get("europe", {})
        if europe.get("available"):
            parts.append(
                "欧赔 "
                + _probs_text(
                    europe.get("market_probability", {}),
                    {"home": "主胜", "draw": "平局", "away": "客胜"},
                )
                + f"，模型看 {europe.get('model_direction', '')}，"
                f"最终 {europe.get('final_direction', '')}({europe.get('action', '')})"
            )
        else:
            parts.append("欧赔/市场：无，模型仅供方向参考")
        asian = odds.get("asian_handicap", {})
        if asian.get("available"):
            parts.append(
                f"亚盘 {asian.get('line_text', '')}：盘口 "
                f"{asian.get('market_side', '')}，模型 {asian.get('model_side', '')}，"
                f"建议 {asian.get('recommended_side', '')} "
                f"{_pct(asian.get('confidence', 0.0))}({asian.get('action', '')})"
            )
        total = odds.get("total_goals", {})
        if total.get("available"):
            parts.append(
                f"大小 {total.get('line_text', '')}：盘口 {total.get('market_side', '')}，"
                f"模型 {total.get('model_side', '')}，建议 {total.get('recommended_side', '')} "
                f"{_pct(total.get('confidence', 0.0))}({total.get('action', '')})"
            )
        if parts:
            lines.append(
                f"- {item['match_no']} {item['home_team']} vs {item['away_team']}："
                + "；".join(parts)
            )
    lines.append("")
    full_count = sum(
        1
        for item in analyses
        if item["history"]["home"].get("matches")
        and item["history"]["away"].get("matches")
        and item["market"].get("has_total")
    )
    lines.append(
        f"覆盖统计：共 {len(analyses)} 场；双方往绩+大小盘齐全 {full_count} 场，其余场次原因见场次备注。"
    )
    h2h_count = sum(
        1
        for item in analyses
        if h2h_short(item["history"].get("h2h")) != "无"
    )
    lines.append(f"历史交锋覆盖：{h2h_count}/{len(analyses)} 场。")
    movement_available = sum(
        1
        for item in analyses
        if (item.get("odds_movement") or {}).get("available")
    )
    movement_attention = len(attention_items)
    lines.append(f"亚盘初盘/即时覆盖：{movement_available}/{len(analyses)} 场；变盘异动提醒 {movement_attention} 场。")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def write_csv(path: Path, analyses: list[dict], issue: str) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "期号",
                "场次",
                "赛事",
                "开赛",
                "主队",
                "客队",
                "亚盘",
                "大小盘",
                "主队近10",
                "客队近10",
                "历史交锋",
                "让球建议",
                "大小建议",
                "变盘异动",
                "变盘说明",
            ]
        )
        for item in analyses:
            asian_rec = "无"
            total_rec = "无"
            if item.get("odds_analysis", {}).get("asian_handicap", {}).get("available"):
                asian = item["odds_analysis"]["asian_handicap"]
                asian_rec = (
                    f"{asian.get('recommended_side', '')} "
                    f"{_pct(asian.get('confidence', 0.0))}({asian.get('action', '')})"
                )
            if item.get("odds_analysis", {}).get("total_goals", {}).get("available"):
                total = item["odds_analysis"]["total_goals"]
                total_rec = (
                    f"{total.get('recommended_side', '')} "
                    f"{_pct(total.get('confidence', 0.0))}({total.get('action', '')})"
                )
            writer.writerow(
                [
                    issue,
                    item["match_no"],
                    item["league"],
                    item["match_time"],
                    item["home_team"],
                    item["away_team"],
                    asian_display(item),
                    total_display(item),
                    history_short(item["history"]["home"]),
                    history_short(item["history"]["away"]),
                    h2h_short(item["history"].get("h2h")),
                    asian_rec,
                    total_rec,
                    _movement_level(item),
                    _movement_reason_text(item),
                ]
            )


def _movement_level(item: dict) -> str:
    movement = item.get("odds_movement") or {}
    return str(movement.get("level") or "无数据")


def _movement_reason_text(item: dict) -> str:
    movement = item.get("odds_movement") or {}
    return "；".join(movement.get("reasons") or [])


def write_report_files(
    report_dir: str | Path,
    analyses: list[dict],
    issue: str,
    source_url: str,
) -> Path:
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_dir / f"issue_{issue}_{stamp}"
    html_path = base.with_suffix(".html")
    write_markdown(html_path, render_browser_html(analyses, issue, source_url, datetime.now()))
    return html_path


def print_console(analyses: list[dict], issue: str) -> None:
    print(f"\n胜负彩 {issue} 推荐摘要（共 {len(analyses)} 场）")
    print("-" * 130)
    header = (
        f"{'场':<4}{'赛事':<8}{'开赛':<12}{'主队':<18}{'客队':<18}"
        f"{'让球建议':<20}{'大小建议':<18}"
    )
    print(header)
    for item in analyses:
        odds = item.get("odds_analysis", {})
        print(
            f"{item['match_no']:<4}{item['league'][:6]:<8}{item['match_time']:<12}"
            f"{item['home_team'][:14]:<18}{item['away_team'][:14]:<18}"
            f"{_market_rec_line(odds.get('asian_handicap'))[:19]:<20}"
            f"{_market_rec_line(odds.get('total_goals'))[:17]:<18}"
        )
    print("-" * 130)
    attention_items = movement_summary(analyses)
    if attention_items:
        print("\n变盘异动提醒（亚盘让球，初盘 -> 即时）：")
        for item in attention_items:
            movement = item.get("odds_movement") or {}
            print(
                f"  {item['match_no']} {item['home_team']} vs {item['away_team']} "
                f"[{movement.get('level', '关注')}] "
                + "；".join(movement.get("reasons") or [])
            )
        print()
    print("'?' 表示观望。亚盘/大小建议基于水位与模型比分覆盖的融合，仅供研究，不构成投注建议。")
