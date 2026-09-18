import math
import unittest

from football_analysis.model import (
    ModelInput,
    blend_with_market,
    build_model_input,
    predict_match,
    score_distribution,
)
from football_analysis.odds import (
    handicap_line_to_float,
    market_implied_from_odds,
    normalize_probabilities,
    total_line_to_float,
)


class OddsTests(unittest.TestCase):
    def test_handicap_terms(self):
        self.assertAlmostEqual(handicap_line_to_float("平手"), 0.0)
        self.assertAlmostEqual(handicap_line_to_float("半球/一球"), -0.75)
        self.assertAlmostEqual(handicap_line_to_float("受半球/一球"), 0.75)
        self.assertAlmostEqual(handicap_line_to_float("一球/球半"), -1.25)
        self.assertAlmostEqual(handicap_line_to_float("三球/三球半"), -3.25)

    def test_total_terms(self):
        self.assertAlmostEqual(total_line_to_float("2.5/3"), 2.75)
        self.assertAlmostEqual(total_line_to_float("两球半"), 2.5)
        self.assertAlmostEqual(total_line_to_float("3"), 3.0)

    def test_market_implied_sums_to_one(self):
        probs = market_implied_from_odds(2.0, 3.5, 4.0)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=8)

    def test_normalization_clamps_tiny_values(self):
        normalized = normalize_probabilities(0.0, 0.0, 1.0)
        self.assertAlmostEqual(sum(normalized), 1.0, places=8)
        self.assertGreater(min(normalized), 0.0)


class ModelTests(unittest.TestCase):
    def test_market_blend_is_conservative(self):
        data = ModelInput(
            market_home=0.55,
            market_draw=0.25,
            market_away=0.20,
            has_market=True,
        )
        blended = blend_with_market(
            {"home": 0.2, "draw": 0.25, "away": 0.55},
            data,
        )
        self.assertGreater(blended["home"], 0.45)
        self.assertLess(blended["away"], 0.35)

    def test_score_distribution_sums_to_one(self):
        distribution = score_distribution(1.5, 1.0)
        total = sum(distribution["distribution"].values())
        self.assertAlmostEqual(total, 1.0, places=6)
        self.assertAlmostEqual(
            sum(distribution["probabilities"].values()),
            1.0,
            places=6,
        )

    def test_predict_without_market_uses_poisson(self):
        data = build_model_input(europe_home=2.4, europe_draw=3.3, europe_away=2.8)
        prediction = predict_match(data)
        self.assertAlmostEqual(sum(prediction["final_probabilities"].values()), 1.0, places=6)
        self.assertIn("action", prediction)
        self.assertIn("asian_handicap", prediction)

    def test_expected_lambdas_are_bounded(self):
        data = ModelInput(has_market=True)
        for _ in range(50):
            prediction = predict_match(data)
            self.assertGreaterEqual(prediction["expected_goals_home"], 0.3)
            self.assertLessEqual(prediction["expected_goals_home"], 3.3)
            self.assertGreaterEqual(prediction["expected_goals_away"], 0.2)
            self.assertLessEqual(prediction["expected_goals_away"], 3.2)


if __name__ == "__main__":
    unittest.main()
