from __future__ import annotations

from .odds import fmt_handicap


OUTCOME_LABELS = {"home": "主胜", "draw": "平局", "away": "客胜"}
SIDE_LABELS = {"home": "主队赢盘", "away": "客队赢盘"}
TOTAL_LABELS = {"over": "大球", "under": "小球"}
ACTION_RANK = {"主推": 3, "轻仓": 2, "观望": 1}


def _two_way_market_probability(home_water: float, away_water: float) -> dict[str, float] | None:
    if home_water <= 0 or away_water <= 0:
        return None
    home_dec = 1.0 + home_water
    away_dec = 1.0 + away_water
    home_implied = 1.0 / home_dec
    away_implied = 1.0 / away_dec
    total = home_implied + away_implied
    if total <= 0:
        return None
    return {"home": home_implied / total, "away": away_implied / total}


def _water_market_side(home_water: float, away_water: float, line: float) -> str:
    if home_water > 0 and away_water > 0:
        return "home" if home_water <= away_water else "away"
    return "home" if line <= 0 else "away"


def _two_way_ev(
    win_prob: float,
    push_prob: float,
    water: float,
) -> float | None:
    if water <= 0:
        return None
    loss_prob = max(0.0, 1.0 - win_prob - push_prob)
    return win_prob * water - loss_prob


def _two_way_decision(
    *,
    market_side: str,
    model_side: str,
    home_market_prob: float,
    home_model_prob: float,
) -> tuple[str, float, str, float]:
    blended_home = 0.85 * home_market_prob + 0.15 * home_model_prob
    final_side = "home" if blended_home >= 0.5 else "away"
    final_prob = max(blended_home, 1.0 - blended_home)
    margin = abs(blended_home - 0.5) * 2.0
    same = market_side == model_side
    if not same:
        action = "观望"
    elif final_prob >= 0.65 and margin >= 0.28:
        action = "主推"
    elif final_prob >= 0.55 and margin >= 0.10:
        action = "轻仓"
    else:
        action = "观望"
    return final_side, final_prob, action, margin


def _analyze_europe(market: dict, prediction: dict) -> dict:
    europe = market.get("europe") or {}
    has_odds = any(float(europe.get(key) or 0) > 1.01 for key in ("home", "draw", "away"))
    market_probs = prediction.get("market_probabilities") or {}
    model_probs = prediction.get("model_probabilities") or {}
    final_probs = prediction.get("final_probabilities") or {}
    if not has_odds:
        model_direction = max(model_probs, key=model_probs.get) if model_probs else ""
        return {
            "available": False,
            "market_probability": {},
            "model_probability": model_probs,
            "final_probability": final_probs,
            "market_direction": "",
            "model_direction": OUTCOME_LABELS.get(model_direction, ""),
            "final_direction": prediction.get("final_direction", ""),
            "action": "观望",
            "agreement": False,
        }
    market_direction = max(market_probs, key=market_probs.get) if market_probs else ""
    model_direction = max(model_probs, key=model_probs.get) if model_probs else ""
    return {
        "available": True,
        "market_probability": market_probs,
        "model_probability": model_probs,
        "final_probability": final_probs,
        "market_direction": OUTCOME_LABELS.get(market_direction, ""),
        "model_direction": OUTCOME_LABELS.get(model_direction, ""),
        "final_direction": prediction.get("final_direction", ""),
        "action": prediction.get("action", "观望"),
        "agreement": bool(market_direction) and market_direction == model_direction,
    }


def _analyze_asian(market: dict, prediction: dict) -> dict:
    if not market.get("has_asian"):
        return {
            "available": False,
            "line_text": "",
            "home_line": 0.0,
            "action": "观望",
        }
    asian = market["asian"]
    ah = prediction.get("asian_handicap") or {}
    home_line = float(asian.get("home_line") or 0.0)
    home_water = float(asian.get("home_water") or 0.0)
    away_water = float(asian.get("away_water") or 0.0)
    home_cover = float(ah.get("home_cover") or 0.0)
    away_cover = float(ah.get("away_cover") or 0.0)
    push = float(ah.get("push") or 0.0)
    cover_total = home_cover + away_cover
    model_home = home_cover / cover_total if cover_total > 0 else 0.5

    market_side = _water_market_side(home_water, away_water, home_line)
    market_probs = _two_way_market_probability(home_water, away_water)
    if market_probs is None:
        market_probs = {"home": 0.5, "away": 0.5}
    model_side = "home" if model_home >= 0.5 else "away"
    final_side, final_prob, action, margin = _two_way_decision(
        market_side=market_side,
        model_side=model_side,
        home_market_prob=market_probs.get("home", 0.5),
        home_model_prob=model_home,
    )
    final_side_prob = (
        final_prob if final_side == "home" else 1.0 - final_prob
    )
    ev_home = _two_way_ev(home_cover, push, home_water)
    ev_away = _two_way_ev(away_cover, push, away_water)

    return {
        "available": True,
        "line_text": asian.get("line_text", "") or fmt_handicap(home_line),
        "home_line": home_line,
        "home_water": home_water,
        "away_water": away_water,
        "market_probability": market_probs,
        "model_probability": {
            "home_cover": home_cover,
            "away_cover": away_cover,
            "push": push,
            "home_cover_rate": model_home,
        },
        "blended_probability": {"home": final_side_prob, "away": 1.0 - final_side_prob},
        "market_side": SIDE_LABELS.get(market_side, ""),
        "model_side": SIDE_LABELS.get(model_side, ""),
        "recommended_side": SIDE_LABELS.get(final_side, ""),
        "action": action,
        "confidence": final_prob,
        "margin": margin,
        "ev_home": ev_home,
        "ev_away": ev_away,
    }


def _analyze_total(market: dict, prediction: dict) -> dict:
    if not market.get("has_total"):
        return {
            "available": False,
            "line_text": "",
            "line": 0.0,
            "action": "观望",
        }
    total = market["total"]
    ou = prediction.get("total_goals") or {}
    line = float(total.get("line") or 0.0)
    over_water = float(total.get("over_water") or 0.0)
    under_water = float(total.get("under_water") or 0.0)
    over = float(ou.get("over") or 0.0)
    under = float(ou.get("under") or 0.0)
    push = float(ou.get("push") or 0.0)
    sum_over_under = over + under
    model_over = over / sum_over_under if sum_over_under > 0 else 0.5

    if over_water <= 0 and under_water <= 0:
        market_side = "over" if model_over >= 0.5 else "under"
        market_probs = {"over": 0.5, "under": 0.5}
    else:
        market_side = "over" if over_water <= under_water else "under"
        water_probs = _two_way_market_probability(over_water, under_water)
        market_probs = (
            {"over": water_probs["home"], "under": water_probs["away"]}
            if water_probs
            else {"over": 0.5, "under": 0.5}
        )
    model_side = "over" if model_over >= 0.5 else "under"
    final_side, final_prob, action, margin = _two_way_decision(
        market_side=market_side,
        model_side=model_side,
        home_market_prob=market_probs.get("over", 0.5),
        home_model_prob=model_over,
    )
    final_side_key = "over" if final_side == "home" else "under"
    ev_over = _two_way_ev(over, push, over_water)
    ev_under = _two_way_ev(under, push, under_water)

    return {
        "available": True,
        "line_text": str(total.get("line_text") or f"{line:g}"),
        "line": line,
        "over_water": over_water,
        "under_water": under_water,
        "market_probability": market_probs,
        "model_probability": {
            "over": over,
            "under": under,
            "push": push,
            "over_rate": model_over,
        },
        "market_side": TOTAL_LABELS.get(market_side, ""),
        "model_side": TOTAL_LABELS.get(model_side, ""),
        "recommended_side": TOTAL_LABELS.get(final_side_key, ""),
        "action": action,
        "confidence": final_prob,
        "margin": margin,
        "ev_over": ev_over,
        "ev_under": ev_under,
    }


def analyze_odds(market: dict, prediction: dict) -> dict:
    result = {
        "europe": _analyze_europe(market, prediction),
        "asian_handicap": _analyze_asian(market, prediction),
        "total_goals": _analyze_total(market, prediction),
    }
    best_rank = 0
    any_available = False
    for market_key in ("europe", "asian_handicap", "total_goals"):
        rec = result[market_key]
        if rec.get("available"):
            any_available = True
        rank = ACTION_RANK.get(str(rec.get("action") or ""), 0)
        if rank:
            best_rank = max(best_rank, rank)
    result["rank"] = max(best_rank, 1) if any_available else 0
    return result
