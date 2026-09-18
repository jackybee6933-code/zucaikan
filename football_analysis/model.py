from __future__ import annotations

import math
from dataclasses import dataclass

from .odds import market_implied_from_odds, normalize_probabilities
from .team import TeamRecord


OUTCOME_KEYS = ("home", "draw", "away")
OUTCOME_LABELS = {"home": "主胜", "draw": "平局", "away": "客胜"}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def poisson_probability(lmbda: float, goals: int) -> float:
    return math.exp(-lmbda) * (lmbda**goals) / math.factorial(goals)


def _league_base_ppg(record: TeamRecord | None) -> float:
    if record and record.matches:
        return (record.wins * 3 + record.draws) / record.matches
    return 1.30


def _league_base_gf(record: TeamRecord | None) -> float:
    if record and record.matches:
        return record.goals_for / record.matches
    return 1.35


def _league_base_ga(record: TeamRecord | None) -> float:
    if record and record.matches:
        return record.goals_against / record.matches
    return 1.10


def _home_only_ppg(record: TeamRecord | None) -> float:
    if record and record.home_matches >= 3:
        return (record.home_wins * 3 + record.home_draws) / record.home_matches
    return 0.0


def _away_only_ppg(record: TeamRecord | None) -> float:
    if record and record.away_matches >= 3:
        return (record.away_wins * 3 + record.away_draws) / record.away_matches
    return 0.0


@dataclass
class ModelInput:
    home_record: TeamRecord | None = None
    away_record: TeamRecord | None = None
    h2h_total: int = 0
    h2h_home_wins: int = 0
    h2h_draws: int = 0
    h2h_away_wins: int = 0
    market_home: float = 1 / 3
    market_draw: float = 1 / 3
    market_away: float = 1 / 3
    has_market: bool = False
    europe_home: float = 0.0
    europe_draw: float = 0.0
    europe_away: float = 0.0
    has_europe: bool = False
    asian_home_line: float = 0.0
    asian_home_water: float = 0.0
    asian_away_water: float = 0.0
    has_asian: bool = False
    total_line: float = 2.5
    has_total: bool = False
    over_water: float = 0.0
    under_water: float = 0.0

    @property
    def history_coverage(self) -> float:
        present = 0
        for record in (self.home_record, self.away_record):
            if record and record.matches:
                present += 1
        return present / 2

    @property
    def h2h_available(self) -> bool:
        return self.h2h_total >= 3

    @property
    def h2h_points_per_game(self) -> float:
        if self.h2h_total <= 0:
            return 0.0
        return (self.h2h_home_wins * 3 + self.h2h_draws) / self.h2h_total

    @property
    def quality_score(self) -> float:
        score = self.history_coverage * 2.0
        if self.has_market:
            score += 1.0
        if self.has_europe:
            score += 0.5
        if self.has_asian:
            score += 0.75
        if self.has_total:
            score += 0.75
        return clamp(score / 5.0, 0.0, 1.0)


def _expected_lambdas(data: ModelInput) -> tuple[float, float]:
    home_record = data.home_record
    away_record = data.away_record
    home_form_ppg = _league_base_ppg(home_record)
    away_form_ppg = _league_base_ppg(away_record)
    form_gap = clamp(home_form_ppg - away_form_ppg, -1.5, 1.5)

    home_gf_pg = _league_base_gf(home_record)
    away_gf_pg = _league_base_gf(away_record)
    home_ga_pg = _league_base_ga(home_record)
    away_ga_pg = _league_base_ga(away_record)

    home_attack = home_gf_pg
    away_defense = away_ga_pg
    away_attack = away_gf_pg
    home_defense = home_ga_pg

    # Use home-only / away-only form when the recent window contains enough
    # split samples; otherwise fall back to total form to avoid tiny samples.
    home_split_ppg = _home_only_ppg(home_record)
    away_split_ppg = _away_only_ppg(away_record)
    split_gap = (
        (home_split_ppg - away_split_ppg) * 0.25
        if home_split_ppg and away_split_ppg
        else 0.0
    )
    h2h_gap = 0.0
    if data.h2h_available:
        # H2H samples are small and span years/competitions, so it only nudges
        # the independent model instead of acting like a standalone prior.
        h2h_gap = clamp((data.h2h_points_per_game - 1.30) * 0.35, -0.30, 0.30)
    goal_diff_gap = clamp(
        (home_gf_pg - home_ga_pg) - (away_gf_pg - away_ga_pg),
        -1.5,
        1.5,
    )

    home_lambda = clamp(
        1.34
        + (home_attack - 1.45) * 0.34
        + (away_defense - 1.10) * 0.24
        + form_gap * 0.16
        + goal_diff_gap * 0.10
        + split_gap * 0.14
        + h2h_gap * 0.10,
        0.35,
        3.20,
    )
    away_lambda = clamp(
        1.10
        + (away_attack - 1.30) * 0.34
        + (home_defense - 1.05) * 0.24
        - form_gap * 0.14
        - goal_diff_gap * 0.08
        - split_gap * 0.12
        - h2h_gap * 0.10,
        0.30,
        3.00,
    )
    return home_lambda, away_lambda


def score_distribution(
    home_lambda: float,
    away_lambda: float,
    *,
    max_goals: int = 8,
) -> dict:
    top_scores: list[tuple[tuple[int, int], float]] = []
    distribution: dict[tuple[int, int], float] = {}
    for hg in range(max_goals + 1):
        ph = poisson_probability(home_lambda, hg)
        for ag in range(max_goals + 1):
            prob = ph * poisson_probability(away_lambda, ag)
            # Small low-score correlation: shared match states push a little
            # probability into 1-1 / 0-0 and away from one-sided results.
            if hg == ag:
                prob *= 1.02
            elif abs(hg - ag) <= 1:
                prob *= 0.995
            prob = max(prob, 0.0)
            distribution[(hg, ag)] = prob
            top_scores.append(((hg, ag), prob))
    raw_total = sum(distribution.values())
    if raw_total > 0:
        for key in distribution:
            distribution[key] /= raw_total
    home_prob = draw_prob = away_prob = 0.0
    for (hg, ag), prob in distribution.items():
        if hg > ag:
            home_prob += prob
        elif hg == ag:
            draw_prob += prob
        else:
            away_prob += prob
    home_p, draw_p, away_p = normalize_probabilities(home_prob, draw_prob, away_prob)
    top_scores.sort(key=lambda item: item[1], reverse=True)
    return {
        "probabilities": {"home": home_p, "draw": draw_p, "away": away_p},
        "lambda_home": home_lambda,
        "lambda_away": away_lambda,
        "distribution": distribution,
        "top_scores": top_scores[:8],
    }


def _ou_probs_from_scores(distribution: dict, total_line: float) -> dict[str, float]:
    over = under = push = 0.0
    for (hg, ag), prob in distribution.items():
        total = hg + ag
        if total > total_line:
            over += prob
        elif total < total_line:
            under += prob
        else:
            push += prob
    total = over + under + push
    if total <= 0:
        return {"over": 1 / 3, "under": 1 / 3, "push": 0.0}
    return {
        "over": over / total,
        "under": under / total,
        "push": push / total,
    }


def _ah_probs_from_scores(distribution: dict, home_line: float) -> dict[str, float]:
    home_cover = away_cover = push = 0.0
    for (hg, ag), prob in distribution.items():
        adjusted = hg - ag + home_line
        if adjusted > 0:
            home_cover += prob
        elif adjusted < 0:
            away_cover += prob
        else:
            push += prob
    total = home_cover + away_cover + push
    if total <= 0:
        return {"home_cover": 1 / 3, "away_cover": 1 / 3, "push": 0.0}
    return {
        "home_cover": home_cover / total,
        "away_cover": away_cover / total,
        "push": push / total,
    }


def blend_with_market(
    model_probs: dict[str, float],
    data: ModelInput,
) -> dict[str, float]:
    if not data.has_market:
        return dict(model_probs)
    market = {
        "home": data.market_home,
        "draw": data.market_draw,
        "away": data.market_away,
    }
    history_score = data.history_coverage
    # The reference project's settled backtests show market prices beat the
    # independent model on average, so the market stays a strong anchor.
    # Independent-model weight only rises when both recent-form windows exist.
    # MVP history is cross-league raw form and can be noisy, so the market
    # remains an 84%-88% prior rather than a 50/50 mix.
    model_weight = 0.12 + 0.04 * history_score
    blended = normalize_probabilities(
        model_probs["home"] * model_weight + market["home"] * (1 - model_weight),
        model_probs["draw"] * model_weight + market["draw"] * (1 - model_weight),
        model_probs["away"] * model_weight + market["away"] * (1 - model_weight),
    )
    return {
        "home": blended[0],
        "draw": blended[1],
        "away": blended[2],
    }


def _direction(probs: dict[str, float]) -> str:
    best = max(probs, key=probs.get)
    return best


def _expected_value(prob: float, odds: float) -> float:
    return prob * odds - 1 if odds > 1.0 else 0.0


def _action(
    final: dict[str, float],
    data: ModelInput,
    model_dir: str,
    market_dir: str,
    odds_map: dict[str, float],
) -> dict:
    best_dir = _direction(final)
    best_prob = final[best_dir]
    market_odds = odds_map.get(best_dir, 0.0)
    has_odds = market_odds > 1.01
    ev = _expected_value(best_prob, market_odds)
    margin = sorted(final.values(), reverse=True)[0] - sorted(final.values(), reverse=True)[1]
    same_as_market = model_dir == market_dir
    if data.quality_score < 0.30 or best_prob < 0.32:
        action = "观望"
    elif not has_odds and not data.has_market:
        # 没有欧赔/市场概率时，模型只输出参考方向，不建议参与。
        action = "观望"
    elif same_as_market and best_prob >= 0.52 and margin >= 0.12 and data.quality_score >= 0.55:
        action = "主推"
    elif same_as_market and best_prob >= 0.38 and margin >= 0.04:
        action = "轻仓"
    else:
        action = "观望"
    return {
        "action": action,
        "ev": ev,
        "margin": margin,
        "market_direction": OUTCOME_LABELS.get(market_dir, ""),
        "model_direction": OUTCOME_LABELS.get(model_dir, ""),
        "same_as_market": same_as_market,
    }


def predict_match(data: ModelInput) -> dict:
    home_lambda, away_lambda = _expected_lambdas(data)
    score = score_distribution(home_lambda, away_lambda)
    model_probs = score["probabilities"]
    model_dir = _direction(model_probs)

    if data.has_market:
        market_probs = {
            "home": data.market_home,
            "draw": data.market_draw,
            "away": data.market_away,
        }
        market_dir = _direction(market_probs)
        final = blend_with_market(model_probs, data)
    elif data.has_europe:
        market_probs = market_implied_from_odds(
            data.europe_home, data.europe_draw, data.europe_away
        )
        market_dir = _direction(market_probs)
        final = dict(model_probs)
    else:
        market_probs = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
        market_dir = model_dir
        final = dict(model_probs)

    final_dir = _direction(final)
    odds_map = {
        "home": data.europe_home if data.europe_home > 1 else 0,
        "draw": data.europe_draw if data.europe_draw > 1 else 0,
        "away": data.europe_away if data.europe_away > 1 else 0,
    }
    action = _action(final, data, model_dir, market_dir, odds_map)
    ah = _ah_probs_from_scores(score["distribution"], data.asian_home_line)
    ou = _ou_probs_from_scores(score["distribution"], data.total_line)
    model_ou = _ou_probs_from_scores(score["distribution"], 2.5)
    return {
        "expected_goals_home": home_lambda,
        "expected_goals_away": away_lambda,
        "model_probabilities": {
            "home": model_probs["home"],
            "draw": model_probs["draw"],
            "away": model_probs["away"],
        },
        "market_probabilities": {
            "home": market_probs["home"],
            "draw": market_probs["draw"],
            "away": market_probs["away"],
        },
        "final_probabilities": {
            "home": final["home"],
            "draw": final["draw"],
            "away": final["away"],
        },
        "final_direction": OUTCOME_LABELS.get(final_dir, ""),
        "top_scores": [
            {
                "score": f"{hg}-{ag}",
                "probability": prob,
            }
            for (hg, ag), prob in score["top_scores"][:3]
        ],
        "asian_handicap": ah,
        "total_goals": ou,
        "model_over25": model_ou["over"],
        "action": action["action"],
        "action_detail": action,
        "quality_score": data.quality_score,
    }


def build_model_input(
    *,
    home_record: TeamRecord | None = None,
    away_record: TeamRecord | None = None,
    h2h_total: int = 0,
    h2h_home_wins: int = 0,
    h2h_draws: int = 0,
    h2h_away_wins: int = 0,
    market_home: float = 0.0,
    market_draw: float = 0.0,
    market_away: float = 0.0,
    europe_home: float = 0.0,
    europe_draw: float = 0.0,
    europe_away: float = 0.0,
    asian_home_line: float = 0.0,
    asian_home_water: float = 0.0,
    asian_away_water: float = 0.0,
    has_asian: bool = False,
    total_line: float = 2.5,
    has_total: bool = False,
    over_water: float = 0.0,
    under_water: float = 0.0,
) -> ModelInput:
    has_market = any(x > 0 for x in (market_home, market_draw, market_away))
    has_europe = any(x > 1.01 for x in (europe_home, europe_draw, europe_away))
    if not has_market and has_europe:
        implied = market_implied_from_odds(europe_home, europe_draw, europe_away)
        market_home, market_draw, market_away = (
            implied["home"],
            implied["draw"],
            implied["away"],
        )
        has_market = True
    return ModelInput(
        home_record=home_record,
        away_record=away_record,
        h2h_total=h2h_total,
        h2h_home_wins=h2h_home_wins,
        h2h_draws=h2h_draws,
        h2h_away_wins=h2h_away_wins,
        market_home=market_home,
        market_draw=market_draw,
        market_away=market_away,
        has_market=has_market,
        europe_home=europe_home,
        europe_draw=europe_draw,
        europe_away=europe_away,
        has_europe=has_europe,
        asian_home_line=asian_home_line,
        asian_home_water=asian_home_water,
        asian_away_water=asian_away_water,
        has_asian=has_asian,
        total_line=total_line,
        has_total=has_total,
        over_water=over_water,
        under_water=under_water,
    )
