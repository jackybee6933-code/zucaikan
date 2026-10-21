from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from .model import OUTCOME_LABELS
from .report import history_short, h2h_short
from .llm_settings import load_settings

_H2H_ROW_LIMIT = 6


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def endpoint_config(
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    settings_path: str | Path | None = None,
    use_saved_key: bool = True,
) -> tuple[str, str, str]:
    """Shared LLM endpoint resolution from args, local settings, or env vars."""
    saved = load_settings(settings_path)["primary"]
    return (
        (base_url or saved["base_url"] or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        api_key
        or (saved["api_key"] if use_saved_key else "")
        or (os.environ.get("LLM_API_KEY") if use_saved_key else "")
        or "",
        model or saved["model"] or os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
    )


def fallback_config(
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    settings_path: str | Path | None = None,
    use_saved_key: bool = True,
) -> tuple[str, str, str]:
    """Backup LLM endpoint from args, local settings, or LLM_FALLBACK_* env vars."""
    saved = load_settings(settings_path)["fallback"]
    return (
        (base_url or saved["base_url"] or os.environ.get("LLM_FALLBACK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        api_key
        or (saved["api_key"] if use_saved_key else "")
        or (os.environ.get("LLM_FALLBACK_API_KEY") if use_saved_key else "")
        or "",
        model or saved["model"] or os.environ.get("LLM_FALLBACK_MODEL") or DEFAULT_MODEL,
    )


def test_endpoint(
    name: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    settings_path: str | Path | None = None,
    use_saved_key: bool = True,
    timeout: float = 15.0,
) -> dict[str, object]:
    """Send a minimal request to verify one configured OpenAI-compatible endpoint."""
    if name == "primary":
        resolved = endpoint_config(
            base_url=base_url,
            api_key=api_key,
            model=model,
            settings_path=settings_path,
            use_saved_key=use_saved_key,
        )
    elif name == "fallback":
        resolved = fallback_config(
            base_url=base_url,
            api_key=api_key,
            model=model,
            settings_path=settings_path,
            use_saved_key=use_saved_key,
        )
    else:
        return {"ok": False, "message": "接入点名称无效"}

    resolved_url, resolved_key, resolved_model = resolved
    if not resolved_key:
        return {"ok": False, "message": "未配置 API Key"}
    if not resolved_model:
        return {"ok": False, "message": "未配置模型名"}
    if not resolved_url.startswith(("http://", "https://")):
        return {"ok": False, "message": "URL 必须以 http:// 或 https:// 开头"}

    try:
        response = requests.post(
            f"{resolved_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {resolved_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": resolved_model,
                "messages": [{"role": "user", "content": "只回复 OK"}],
                "max_tokens": 1,
                "temperature": 0,
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return {"ok": False, "message": "连接超时"}
    except requests.RequestException as exc:
        return {"ok": False, "message": f"连接失败：{exc}"[:220]}

    if 200 <= response.status_code < 300:
        return {"ok": True, "message": f"连接成功（{resolved_model}）"}
    messages = {
        401: "API Key 无效或已过期",
        403: "没有访问权限",
        404: "URL 或模型不存在",
        429: "请求过于频繁或额度不足",
    }
    message = messages.get(response.status_code, f"服务返回 HTTP {response.status_code}")
    return {"ok": False, "message": message}

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


# --- 球队资料分析：读 500 数据分析页的资料，给亚盘让球倾向（只展示，不进任何门禁） ---

TEAM_READ_TENDENCIES = ("主队赢盘", "客队赢盘", "观望")
OVER_READ_TENDENCIES = ("大球", "小球", "观望")

TEAM_READ_SYSTEM_PROMPT = """你是一名足球盘口分析师，负责读完一场比赛的球队资料后，给出这场球的亚盘让球倾向。

要求：
- 只使用输入里给出的资料（近期战绩、主客场表现、赢盘率、大小球率、历史交锋、积分排名、盘口与概率）。
- 只能输出一个 JSON 对象，不要任何额外文字、不要代码块围栏，格式：
  {"tendency": "主队赢盘", "confidence": 0.0, "reason": "一句话，40 字以内", "over_tendency": "大球", "over_confidence": 0.0, "over_reason": "一句话，40 字以内"}
- tendency 只能取三个值之一：主队赢盘、客队赢盘、观望。
- 资料不足、双方势均力敌或无法判断时，tendency 必须是「观望」。
- over_tendency 判断总进球数更可能落在大小盘线上方还是下方，只能取三个值之一：大球、小球、观望。
- 大小盘数据缺失、双方大小球率都没有、或无法判断时，over_tendency 必须是「观望」。
- 不要编造伤停、转会、内幕等资料里没有的信息，也不要预测比分。"""


def _team_form_line(label: str, record: dict | None) -> str:
    """One team's form line for the prompt: 近10场 + 主客拆分 + 赢盘率 + 大小球率."""
    record = record or {}
    if not record.get("matches"):
        return f"{label}：无近期战绩数据"
    parts = [
        f"近{record['matches']}场 {record['wins']}胜{record['draws']}平{record['losses']}负"
        f" 进{record['goals_for']}失{record['goals_against']}"
    ]
    if record.get("home_matches"):
        parts.append(
            f"主场{record['home_matches']}场{record['home_wins']}胜"
            f"{record['home_draws']}平{record['home_losses']}负"
        )
    if record.get("away_matches"):
        parts.append(
            f"客场{record['away_matches']}场{record['away_wins']}胜"
            f"{record['away_draws']}平{record['away_losses']}负"
        )
    if record.get("handicap_win_rate") is not None:
        parts.append(
            f"赢盘率{_pct(record['handicap_win_rate'])}"
            f"（{record.get('handicap_win', 0)}赢{record.get('handicap_push', 0)}走）"
        )
    if record.get("over_rate") is not None:
        parts.append(f"大球率{_pct(record['over_rate'])}")
    return f"{label}：" + "，".join(parts)


def build_team_read_prompt(analysis: dict, page_text: str = "") -> str:
    """One match's team data -> LLM prompt. Pure, no I/O."""
    movement = analysis.get("odds_movement") or {}
    asian = movement.get("asian") or {}
    total = movement.get("total") or {}
    history = analysis.get("history") or {}
    prediction = analysis.get("prediction") or {}

    lines = [
        f"联赛：{analysis.get('league') or '未知'}",
        f"比赛：{analysis.get('home_team') or '?'}（主） vs "
        f"{analysis.get('away_team') or '?'}（客）（{analysis.get('match_time') or '时间未知'}）",
    ]
    if asian.get("available"):
        lines.append(f"亚盘初盘：{_asian_line_text(asian.get('initial'))}")
        lines.append(f"亚盘即时盘：{_asian_line_text(asian.get('current'))}")
        if asian.get("lean"):
            lines.append(f"盘口异动倾向：{asian['lean']}赢盘")
    else:
        lines.append("亚盘：无数据")
    if total.get("available"):
        lines.append(f"大小盘初盘：{_total_line_text(total.get('initial'))}")
        lines.append(f"大小盘即时盘：{_total_line_text(total.get('current'))}")

    probs = prediction.get("final_probabilities") or {}
    if probs:
        lines.append(
            "模型概率："
            + " / ".join(
                f"{OUTCOME_LABELS.get(key, key)} {_pct(probs.get(key, 0.0))}"
                for key in ("home", "draw", "away")
            )
        )
    lines.append(_team_form_line("主队近况", history.get("home")))
    lines.append(_team_form_line("客队近况", history.get("away")))
    lines.extend(_history_detail(history))
    if page_text:
        lines.append("500 数据分析页资料（原样摘录，含积分排名/交战历史/近期战绩/未来赛事/平均数据）：")
        lines.append(page_text[:6000])
    else:
        lines.append("500 数据分析页资料：未抓到")
    lines.append(
        "\n请判断两件事：(1) 主队还是客队更可能赢下亚盘让球（主队赢盘 / 客队赢盘 / 观望）；"
        "(2) 总进球数更可能落在大小盘线上方还是下方（大球 / 小球 / 观望）。"
        "只用上面给出的资料，资料不足就给观望。只输出 JSON，不要解释。"
    )
    return "\n".join(lines)


def parse_team_read(text: str) -> dict | None:
    """严格解析模型返回；不合法一律返回 None（调用方跳过，不写脏数据）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except ValueError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    tendency = str(data.get("tendency") or "").strip()
    if tendency not in TEAM_READ_TENDENCIES:
        return None
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:80]
    over_tendency = str(data.get("over_tendency") or "").strip()
    if over_tendency not in OVER_READ_TENDENCIES:
        over_tendency = "观望"
    try:
        over_confidence = float(data.get("over_confidence") or 0.0)
    except (TypeError, ValueError):
        over_confidence = 0.0
    over_confidence = min(1.0, max(0.0, over_confidence))
    over_reason = re.sub(r"\s+", " ", str(data.get("over_reason") or "")).strip()[:80]
    return {
        "tendency": tendency,
        "confidence": confidence,
        "reason": reason,
        "over_tendency": over_tendency,
        "over_confidence": over_confidence,
        "over_reason": over_reason,
    }


class TeamReader:
    """Reads one match's team data and asks the LLM for an Asian-handicap lean."""

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        fallback: tuple[str, str, str] | None = None,
        timeout: float = 45.0,
        retry_wait: float = 5.0,
        attempts: int = 4,
    ) -> None:
        self.base_url, self.api_key, self.model = endpoint_config(
            base_url=base_url, api_key=api_key, model=model
        )
        # 主接入点限流/挂掉时自动切备用接入点（LLM_FALLBACK_*）；没配就只有一个。
        if fallback is None:
            fallback = fallback_config()
        self.endpoints = [(self.base_url, self.api_key, self.model)]
        if fallback and fallback[1]:
            self.endpoints.append(tuple(fallback))
        self.timeout = timeout
        # 接入点（实测 agnes-ai）有每分钟调用上限，14 场连着打会 429；退避重试即可跑完。
        self.retry_wait = retry_wait
        self.attempts = max(1, attempts)

    @property
    def enabled(self) -> bool:
        return any(api_key for _, api_key, _ in self.endpoints)

    def read(self, analysis: dict, page_text: str = "") -> dict | None:
        if not self.enabled:
            return None
        body = {
            "messages": [
                {"role": "system", "content": TEAM_READ_SYSTEM_PROMPT},
                {"role": "user", "content": build_team_read_prompt(analysis, page_text)},
            ],
            "temperature": 0.2,
        }
        for index, (base_url, api_key, model) in enumerate(self.endpoints):
            if index:
                print(f"  主接入点没给出结果，改用备用接入点 {model}")
            result = self._read_endpoint(base_url, api_key, model, body)
            if result is not None:
                return result
        return None

    def _read_endpoint(self, base_url: str, api_key: str, model: str, body: dict) -> dict | None:
        payload = dict(body, model=model)
        for attempt in range(1, self.attempts + 1):
            try:
                response = requests.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  球队资料分析请求失败，已跳过：{exc}")
                return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.attempts:
                    time.sleep(self.retry_wait * attempt)
                    continue
                print(f"  球队资料分析被限流（HTTP {response.status_code}，{model}）")
                return None
            try:
                response.raise_for_status()
                return parse_team_read(extract_content(response.json()))
            except Exception as exc:  # noqa: BLE001
                print(f"  球队资料分析请求失败，已跳过：{exc}")
                return None
        return None


def enrich_team_reads(
    analyses: list[dict],
    reader: TeamReader | None = None,
    page_texts: list[str] | None = None,
    pace: float = 4.0,
    progress_path: Path | None = None,
    issue: str = "",
) -> int:
    """Attach a model lean to each match. Returns the enriched count."""
    reader = reader or TeamReader()
    if not reader.enabled:
        print(
            "  未启用球队资料分析：需要环境变量 LLM_API_KEY"
            "（可用 LLM_BASE_URL / LLM_MODEL 指定服务商与模型）"
        )
        return 0

    def _report(phase: str, done: int, total: int) -> None:
        if progress_path is None:
            return
        try:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text(
                json.dumps({"issue": issue, "phase": phase, "total": total, "done": done, "failed": 0},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    count = 0
    for index, item in enumerate(analyses):
        _report("llm", index, len(analyses))
        if index:
            # 接入点通常是按分钟限流的，14 场连着打必被 429；这里主动留出间隔。
            time.sleep(max(0.0, pace))
        page_text = ""
        if page_texts and index < len(page_texts):
            page_text = str(page_texts[index] or "")
        result = reader.read(item, page_text)
        if result:
            item["team_read"] = result
            count += 1
    _report("llm", len(analyses), len(analyses))
    print(f"  球队资料分析：{count}/{len(analyses)} 场已生成倾向")
    return count
