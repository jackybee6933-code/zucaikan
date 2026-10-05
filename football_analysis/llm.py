from __future__ import annotations

import os

import requests

from .model import OUTCOME_LABELS
from .report import history_short, h2h_short

_H2H_ROW_LIMIT = 6


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def endpoint_config(*, base_url: str = "", api_key: str = "", model: str = "") -> tuple[str, str, str]:
    """Shared LLM endpoint resolution from args or LLM_* env vars."""
    return (
        (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        api_key or os.environ.get("LLM_API_KEY") or "",
        model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
    )

SYSTEM_PROMPT = """你是一名足球盘口数据分析师，负责把亚盘/大小盘从初盘到即时盘的结构化变化翻译成简短中文解读。

要求：
- 只使用输入中给出的数据；不得编造赔率、水位、比分、伤停或任何未提供的信息。
- 不要给出投注建议，也不要预测比分；建议由报告的动作门禁模块另行给出。
- 数据缺失时直接说明该部分不可用，不要猜测。
- 输出 2 到 3 句话，不要列表、不要标题、不要复述全部数字。"""


def _pct(value: float) -> str:
    return f"{float(value) * 100:.0f}%"


def _asian_line_text(side: dict | None) -> str:
    if not side:
        return "无数据"
    line = str(side.get("line_display") or side.get("line_text") or "").strip()
    home = float(side.get("home_water") or 0)
    away = float(side.get("away_water") or 0)
    water = f" 水位 {home:.2f}/{away:.2f}" if home or away else ""
    return f"{line}{water}" or "无数据"


def _total_line_text(side: dict | None) -> str:
    if not side:
        return "无数据"
    line = str(side.get("line_text") or "").strip()
    over = float(side.get("over_water") or 0)
    under = float(side.get("under_water") or 0)
    water = f" 水 {over:.2f}/{under:.2f}" if over or under else ""
    return f"{line}{water}" or "无数据"


def build_prompt(analysis: dict) -> str:
    """Turn one pipeline analysis dict into an LLM prompt. Pure, no I/O."""
    movement = analysis.get("odds_movement") or {}
    asian = movement.get("asian") or {}
    total = movement.get("total") or {}
    prediction = analysis.get("prediction") or {}
    asian_rec = (analysis.get("odds_analysis") or {}).get("asian_handicap") or {}
    history = analysis.get("history") or {}

    lines = [
        f"联赛：{analysis.get('league') or '未知'}",
        f"比赛：{analysis.get('home_team') or '?'} vs {analysis.get('away_team') or '?'}"
        f"（{analysis.get('match_time') or '时间未知'}）",
        f"异动判定：{movement.get('level') or '无'}，类型 {movement.get('kind') or '无'}",
    ]
    reasons = [str(r) for r in (movement.get("reasons") or []) if str(r).strip()]
    if reasons:
        lines.append("系统判定依据：" + "；".join(reasons))
    if asian.get("available"):
        lines.append(f"亚盘初盘：{_asian_line_text(asian.get('initial'))}")
        lines.append(f"亚盘即时盘：{_asian_line_text(asian.get('current'))}")
        if asian.get("lean"):
            lines.append(f"盘口异动倾向：{asian['lean']}赢盘")
    else:
        lines.append("亚盘初盘/即时盘：无数据")
    if total.get("available"):
        lines.append(f"大小盘初盘：{_total_line_text(total.get('initial'))}")
        lines.append(f"大小盘即时盘：{_total_line_text(total.get('current'))}")

    probs = prediction.get("final_probabilities") or {}
    if probs:
        lines.append(
            "最终概率："
            + " / ".join(
                f"{OUTCOME_LABELS.get(key, key)} {_pct(probs.get(key, 0.0))}"
                for key in ("home", "draw", "away")
            )
        )
    if prediction.get("model_direction") and prediction.get("market_direction"):
        lines.append(
            f"模型方向：{prediction['model_direction']}；市场方向：{prediction['market_direction']}"
        )
    if asian_rec.get("available"):
        lines.append(
            f"亚盘建议：{asian_rec.get('recommended_side') or '-'}"
            f"（{asian_rec.get('action') or '观望'}，置信度 {_pct(asian_rec.get('confidence') or 0)}）"
        )
    lines.append(f"主队近况：{history_short(history.get('home'))}")
    lines.append(f"客队近况：{history_short(history.get('away'))}")
    lines.append(f"历史交锋：{h2h_short(history.get('h2h'))}")
    detail = _history_detail(history)
    if detail:
        for line in detail:
            lines.append(line)
    for warning in analysis.get("warnings") or []:
        lines.append(f"数据告警：{warning}")

    lines.append(
        "\n\u8bf7\u7528 3 \u5230 4 \u53e5\u8bdd\u7efc\u5408\u89e3\u8bfb\u672c\u573a\uff1a"
        "\u5148\u7528 1-2 \u53e5\u603b\u7ed3\u4e3b\u5ba2\u961f\u8fd1\u51b5\u4e0e\u5386\u53f2\u4ea4\u950b\u7684\u65b9\u5411\u504f\u597d\uff08\u4ec5\u4f9d\u636e\u5df2\u63d0\u4f9b\u7684\u8d5b\u79ef\u3001\u8fdb\u5931\u7403\u4e0e\u4ea4\u950b\u8bb0\u5f55\uff0c"
        "\u6570\u636e\u7f3a\u5931\u65f6\u76f4\u8bf4\u4e0d\u53ef\u7528\uff09\uff1b\u518d\u89e3\u8bfb\u76d8\u53e3\u5f02\u52a8\uff08\u5e02\u573a\u901a\u8fc7\u76d8\u53e3\u6216\u6c34\u4f4d\u53d8\u5316\u8868\u8fbe\u4e86\u4ec0\u4e48\uff0c"
        "\u4e0e\u6a21\u578b\u65b9\u5411\u548c\u4e9a\u76d8\u5efa\u8bae\u662f\u5426\u4e00\u81f4\uff09\u3002\u53ea\u89e3\u8bfb\uff0c\u4e0d\u5efa\u8bae\u3002"
    )
    return "\n".join(lines)


def _history_detail(history: dict) -> list[str]:
    """Structured form + head-to-head detail for a synthesis prompt. Empty if nothing usable."""
    out: list[str] = []
    for label, key in (("主队", "home"), ("客队", "away")):
        record = history.get(key) or {}
        rows = record.get("rows") or []
        if not rows:
            continue
        parts = []
        for row in rows[:10]:
            score = f"{row.get('goal_home', 0)}-{row.get('goal_away', 0)}" if "goal_home" in row else str(row.get("score", "?"))
            league = str(row.get("league") or "").strip() or "联赛未知"
            parts.append(f"{row.get('date', '')} {row.get('home', '')} {score} {row.get('away', '')} ({league})")
        out.append(f"{label}近{min(len(rows),10)}场: " + " / ".join(parts))
    h2h = history.get("h2h") or {}
    if h2h.get("_error"):
        out.append(f"历史交锋：{h2h['_error']}")
    else:
        rows = h2h.get("rows") or []
        if not h2h.get("total"):
            out.append("历史交锋：无记录")
        else:
            lines2 = []
            for row in rows[:_H2H_ROW_LIMIT]:
                lines2.append(f"{row.get('date', '')} {row.get('home', '')} {row.get('score', '?')} {row.get('away', '')} ({row.get('league') or '联赛未知'})")
            out.append("历史交锋(近期优先): " + " / ".join(lines2))
    return out


def extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


class MovementNarrator:
    """Calls an OpenAI-compatible chat completions endpoint to narrate one match."""

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.base_url, self.api_key, self.model = endpoint_config(
            base_url=base_url, api_key=api_key, model=model
        )
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def narrate(self, analysis: dict) -> str:
        if not self.enabled:
            return ""
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_prompt(analysis)},
                    ],
                    "temperature": 0.4,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = extract_content(response.json())
        except Exception as exc:  # noqa: BLE001
            print(f"  大模型解读请求失败，已跳过：{exc}")
            return ""
        return text.strip()


def narrate_movements(
    analyses: list[dict],
    narrator: MovementNarrator | None = None,
) -> int:
    """Attach an LLM paragraph to each movement-alert match. Returns narrated count."""
    narrator = narrator or MovementNarrator()
    if not narrator.enabled:
        print(
            "  未启用大模型解读：需要环境变量 LLM_API_KEY"
            "（可用 LLM_BASE_URL / LLM_MODEL 指定服务商与模型）"
        )
        return 0
    items = [
        item
        for item in analyses
        if (item.get("odds_movement") or {}).get("attention")
    ]
    if not items:
        print("  本期没有达到关注线的盘口异动，无需大模型解读")
        return 0
    count = 0
    for item in items:
        text = narrator.narrate(item)
        if text:
            item["narration"] = text
            count += 1
    print(f"  大模型解读：{count}/{len(items)} 场变盘异动已生成")
    return count
