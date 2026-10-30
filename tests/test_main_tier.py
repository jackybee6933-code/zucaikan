"""验证市场概率与模型概率的 50/50 融合及推荐档位。"""
import unittest

from football_analysis.odds_analysis import (
    _two_way_decision,
    _two_way_market_probability,
    analyze_odds,
)


class MainTierTests(unittest.TestCase):
    def test_blend_uses_equal_market_and_model_weights(self):
        side, prob, action, margin = _two_way_decision(
            market_side="home",
            model_side="home",
            home_market_prob=0.60,
            home_model_prob=0.80,
        )
        self.assertEqual(side, "home")
        self.assertAlmostEqual(prob, 0.70)
        self.assertEqual(action, "主推")
        self.assertAlmostEqual(margin, 0.40)

    def test_analyze_odds_can_return_main_for_asian(self):
        market = {
            "has_asian": True,
            "has_total": False,
            "asian": {
                "home_line": -0.5,
                "line_text": "半球",
                "home_water": 0.60,
                "away_water": 1.50,
            },
            "total": {},
        }
        prediction = {
            "action": "观望",
            "asian_handicap": {
                "home_cover": 0.70,
                "away_cover": 0.20,
                "push": 0.10,
            },
        }
        result = analyze_odds(market, prediction)
        self.assertEqual(result["asian_handicap"]["action"], "主推")

    def test_light_tier_is_still_reachable(self):
        _, prob, action, margin = _two_way_decision(
            market_side="home",
            model_side="home",
            home_market_prob=0.60,
            home_model_prob=0.55,
        )
        self.assertEqual(action, "轻仓")
        self.assertAlmostEqual(prob, 0.575)
        self.assertAlmostEqual(margin, 0.15)
