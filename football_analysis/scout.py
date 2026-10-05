from __future__ import annotations

import json
import os

import requests

from .llm import endpoint_config, extract_content


SCOUT_SYSTEM_PROMPT = """你是一名足球情报分析师。任务：对指定比赛联网检索伤停、停赛、战意、赛程密集度等“软信息”，并判断这些信息支持还是反对当前市场方向。

硬性要求：
- 只报告本次检索到的信息。检索不到任何相关报道时必须返回 {"found": false}，绝不可凭训练记忆编造球员姓名、伤情、战意或赛程。
- 球队名与联赛名以输入为准，不要张冠李戴到同名球队。
- 不要给出投注建议，不要预测比分。
- 只输出 JSON 本身，不要代码块、不要前后解释。"""

SCOUT_JSON_TEMPLATE = """{
  "found": true,
  "facts": ["检索到的要点，每条一句话"],
  "lean": "支持市场方向 / 与市场方向相反 / 无明确指向",
  "confidence": "high / medium / low",
  "summary": "2 到 3 句话总结"
}"""

LEAN_LABELS = ("支持市场方向", "与市场方向相反", "无明确指向")
CONFIDENCE_LABELS = ("high", "medium", "low")


def build_scout_prompt(analysis: dict) -> str:
    """Prompt for soft-info retrieval on a match the model has no opinion on. Pure."""
    prediction = analysis.get("prediction") or {}
    market_dir = str(prediction.get("market_direction") or "").strip()
    lines = [
        f"联赛：{analysis.get('league') or '未知'}",
        f"比赛：{analysis.get('home_team') or '?'} vs {analysis.get('away_team') or '?'}"
        f"（{analysis.get('match_time') or '时间未知'}）",
        f"当前市场方向：{market_dir or '无'}",
        "模型状态：数据不足或概率门禁未通过，本期报告对该场只标记为“观望”，不做推荐。",
        "",
        "请联网检索后只输出如下 JSON：",
        SCOUT_JSON_TEMPLATE,
    ]
    if not market_dir:
        lines.append("（输入未给出市场方向时，lean 固定为“无明确指向”。）")
    return "\n".join(lines)


def _parse_scout_text(text: str) -> dict:
    """Normalize an LLM JSON reply into a scout block. Empty dict when nothing usable."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").lstrip()
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
    payload: object = None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if 0 <= start < end:
            try:
                payload = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return {}
    if payload.get("found") is False:
        return {}
    facts = [str(f).strip() for f in payload.get("facts") or [] if str(f).strip()]
    summary = str(payload.get("summary") or "").strip()
    lean = str(payload.get("lean") or "").strip()
    if lean not in LEAN_LABELS:
        lean = "无明确指向"
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCE_LABELS:
        confidence = "low"
    if not facts and not summary:
        return {}
    return {
        "facts": facts,
        "lean": lean,
        "confidence": confidence,
        "summary": summary,
    }


class MatchScout:
    """Calls an OpenAI-compatible endpoint with web search for soft info on one match."""

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 45.0,
        search: bool | None = None,
    ) -> None:
        self.base_url, self.api_key, self.model = endpoint_config(
            base_url=base_url, api_key=api_key, model=model
        )
        self.timeout = timeout
        if search is None:
            search = os.environ.get("LLM_SEARCH", "1").strip().lower() not in ("0", "false", "no", "")
        self.search = search

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def scout(self, analysis: dict) -> dict:
        if not self.enabled:
            return {}
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
                {"role": "user", "content": build_scout_prompt(analysis)},
            ],
            "temperature": 0.3,
        }
        if self.search:
            body["enable_search"] = True
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = extract_content(response.json())
        except Exception as exc:  # noqa: BLE001
            print(f"  软信息检索请求失败，已跳过：{exc}")
            return {}
        return _parse_scout_text(text)


def scout_matches(
    analyses: list[dict],
    scout: MatchScout | None = None,
) -> int:
    """Retrieve soft info for matches the model has no opinion on. Returns populated count."""
    scout = scout or MatchScout()
    if not scout.enabled:
        print("  未启用软信息检索：需要环境变量 LLM_API_KEY（与大模型解读共用）")
        return 0
    targets = [
        item
        for item in analyses
        if (item.get("prediction") or {}).get("action") == "观望"
        and item.get("home_team")
        and item.get("away_team")
    ]
    if not targets:
        print("  本期没有“观望”场次，无需软信息检索")
        return 0
    count = 0
    for item in targets:
        block = scout.scout(item)
        if block:
            item["scout"] = block
            count += 1
    print(f"  软信息检索：观望场次 {len(targets)} 场，拿到信息 {count} 场")
    return count
