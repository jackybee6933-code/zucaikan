"""Pin the fact that the 主推 tier is unreachable with water-level markets.

If this test fails, someone changed the blend weight or the 主推 threshold.
Before lowering the threshold, read the comment in _two_way_decision: the
bands above 轻仓 hold only 7 and 5 matches (71% and 40% — noise). Lowering it
would put the project's highest-confidence label on a coin flip. Only relax it
together with fresh hit-rate evidence.
"""
import unittest

from football_analysis.odds_analysis import (
    _two_way_decision,
    _two_way_market_probability,
    analyze_odds,
)

# 实测（24 期 / 328 场）confidence 上限 0.589、margin 上限 0.178。
# 在真实水位区间内留余量后仍应远低于主推门槛 0.65 / 0.28。
OBSERVED_CONF_CEILING = 0.60
OBSERVED_MARGIN_CEILING = 0.19

# 扩展水位区间：包含不会实际出现的极端值，用例若刷制主推会立刻失败
WIDE_WATERS = [0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.20, 1.30, 1.50]

# 真实中能出现的水位区间，用于量化上限
REAL_WATERS = [0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15]
WATER_RANGE = WIDE_WATERS


class MainTierUnreachableTests(unittest.TestCase):
    def test_main_tier_never_fires_for_water_level_markets(self):
        seen = []
        for hw in WATER_RANGE:
            for aw in WATER_RANGE:
                probs = _two_way_market_probability(hw, aw)
                if probs is None:
                    continue
                for model in (0.30, 0.40, 0.50, 0.60, 0.70):
                    side, prob, action, margin = _two_way_decision(
                        market_side="home",
                        model_side="home",
                        home_market_prob=probs["home"],
                        home_model_prob=model,
                    )
                    seen.append((prob, margin))
                    self.assertNotEqual(
                        action,
                        "主推",
                        f"水位 {hw}/{aw} + 模型 {model} 触发了主推；"
                        "若确因模型变强而有意放宽，请同时更新命中率基线与该函数注释",
                    )
        self.assertTrue(seen)

    def test_observed_ceiling_is_far_below_main_threshold(self):
        worst_prob = 0.0
        worst_margin = 0.0
        for hw in REAL_WATERS:
            for aw in REAL_WATERS:
                probs = _two_way_market_probability(hw, aw)
                if probs is None:
                    continue
                for model in (0.35, 0.45, 0.55, 0.65):
                    _, prob, _, margin = _two_way_decision(
                        market_side="home", model_side="home",
                        home_market_prob=probs["home"], home_model_prob=model,
                    )
                    worst_prob = max(worst_prob, prob)
                    worst_margin = max(worst_margin, margin)
        self.assertLess(worst_prob, OBSERVED_CONF_CEILING,
                        f"confidence 上限升到 {worst_prob:.3f}，接近主推门槛 0.65")
        self.assertLess(worst_margin, OBSERVED_MARGIN_CEILING,
                        f"margin 上限升到 {worst_margin:.3f}，接近主推门槛 0.28")

    def test_analyze_odds_never_returns_main_for_asian(self):
        for hw in (0.70, 0.85, 0.95, 1.05, 1.20):
            for aw in (0.70, 0.85, 0.95, 1.05, 1.20):
                market = {
                    "has_asian": True,
                    "has_total": False,
                    "asian": {"home_line": -0.5, "line_text": "半球",
                              "home_water": hw, "away_water": aw},
                    "total": {},
                }
                prediction = {
                    "action": "观望",
                    "asian_handicap": {
                        "home_cover": 0.52, "away_cover": 0.46, "push": 0.02,
                    },
                }
                out = analyze_odds(market, prediction)
                self.assertNotEqual(out["asian_handicap"]["action"], "主推")

    def test_light_tier_is_actually_reachable(self):
        """反向确认：轻仓档仍能触发，否则说明门槛整体失效。"""
        hits = 0
        for hw in WATER_RANGE:
            for aw in WATER_RANGE:
                probs = _two_way_market_probability(hw, aw)
                if probs is None:
                    continue
                _, _, action, _ = _two_way_decision(
                    market_side="home", model_side="home",
                    home_market_prob=probs["home"], home_model_prob=0.55,
                )
                hits += action == "轻仓"
        self.assertGreater(hits, 0, "轻仓档也不可达了，门槛需整体重估")
