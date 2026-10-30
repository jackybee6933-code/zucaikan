import unittest

from football_analysis.odds_analysis import analyze_odds
from football_analysis.report import _rec_cell, recommendation_summary


class OddsAnalysisTests(unittest.TestCase):
    def test_asian_side_uses_lower_water_and_model_blend(self):
        market = {
            "has_asian": True,
            "asian": {
                "home_line": -0.75,
                "line_text": "半球/一球",
                "home_water": 0.80,
                "away_water": 1.00,
            },
            "has_total": False,
            "total": {},
        }
        prediction = {
            "market_probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
            "model_probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
            "final_probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2},
            "asian_handicap": {
                "home_cover": 0.70,
                "away_cover": 0.20,
                "push": 0.10,
            },
            "action": "观望",
        }
        result = analyze_odds(market, prediction)
        asian = result["asian_handicap"]
        self.assertTrue(asian["available"])
        self.assertEqual(asian["market_side"], "主队赢盘")
        self.assertEqual(asian["model_side"], "主队赢盘")
        self.assertEqual(asian["recommended_side"], "主队赢盘")
        self.assertEqual(asian["action"], "主推")
        self.assertGreater(asian["ev_home"], 0.0)

    def test_total_side_uses_lower_water(self):
        market = {
            "has_asian": False,
            "asian": {},
            "has_total": True,
            "total": {
                "line": 2.75,
                "line_text": "2.5/3",
                "over_water": 1.00,
                "under_water": 0.80,
            },
        }
        prediction = {
            "market_probabilities": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            "model_probabilities": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            "final_probabilities": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            "total_goals": {"over": 0.30, "under": 0.70, "push": 0.0},
            "action": "观望",
        }
        result = analyze_odds(market, prediction)
        total = result["total_goals"]
        self.assertTrue(total["available"])
        self.assertEqual(total["market_side"], "小球")
        self.assertEqual(total["model_side"], "小球")
        self.assertEqual(total["recommended_side"], "小球")
        self.assertEqual(total["action"], "轻仓")

    def test_europe_missing_is_not_treated_as_market(self):
        market = {
            "europe": {"home": 0.0, "draw": 0.0, "away": 0.0},
            "asian": {},
            "total": {},
            "has_asian": False,
            "has_total": False,
        }
        prediction = {
            "market_probabilities": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            "model_probabilities": {"home": 0.46, "draw": 0.27, "away": 0.27},
            "final_probabilities": {"home": 0.46, "draw": 0.27, "away": 0.27},
            "final_direction": "主胜",
            "action": "观望",
        }
        result = analyze_odds(market, prediction)
        europe = result["europe"]
        self.assertFalse(europe["available"])
        self.assertEqual(europe["market_probability"], {})
        self.assertEqual(europe["market_direction"], "")
        self.assertEqual(_rec_cell(europe, "主胜"), "无市场")
        self.assertEqual(result["rank"], 0)

    def test_europe_missing_but_asian_available_keeps_watch_rank(self):
        market = {
            "europe": {"home": 0.0, "draw": 0.0, "away": 0.0},
            "asian": {
                "home_line": 0.25,
                "line_text": "平手/半球",
                "home_water": 0.90,
                "away_water": 0.95,
            },
            "total": {},
            "has_asian": True,
            "has_total": False,
        }
        prediction = {
            "market_probabilities": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            "model_probabilities": {"home": 0.46, "draw": 0.27, "away": 0.27},
            "final_probabilities": {"home": 0.46, "draw": 0.27, "away": 0.27},
            "asian_handicap": {"home_cover": 0.60, "away_cover": 0.25, "push": 0.15},
            "final_direction": "主胜",
            "action": "观望",
        }
        result = analyze_odds(market, prediction)
        europe = result["europe"]
        self.assertFalse(europe["available"])
        self.assertEqual(_rec_cell(europe, "主胜"), "无市场")
        self.assertEqual(result["rank"], 2)

    def test_rank_orders_summary(self):
        market = {
            "europe": {"home": 2.0, "draw": 3.5, "away": 4.0},
            "asian": {
                "home_line": -0.5,
                "line_text": "半球",
                "home_water": 0.90,
                "away_water": 0.95,
            },
            "total": {
                "line": 2.5,
                "line_text": "2.5",
                "over_water": 0.95,
                "under_water": 0.90,
            },
            "has_asian": True,
            "has_total": True,
        }

        def item(match_no: str, action: str) -> dict:
            prediction = {
                "market_probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "model_probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "final_probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "asian_handicap": {"home_cover": 0.60, "away_cover": 0.30, "push": 0.10},
                "total_goals": {"over": 0.40, "under": 0.60, "push": 0.0},
                "final_direction": "主胜",
                "action": action,
            }
            return {
                "match_no": match_no,
                "league": "欧冠",
                "home_team": "主",
                "away_team": "客",
                "odds_analysis": analyze_odds(market, prediction),
            }

        rows = recommendation_summary([item("02", "观望"), item("01", "主推")])
        self.assertEqual([row[1]["match_no"] for row in rows], ["01", "02"])
        self.assertEqual(rows[0][0], 3)
        self.assertEqual(rows[1][0], 2)



from football_analysis.browser_report import _render_summary_rows

class SummaryMovementDisplayTests(unittest.TestCase):
    def test_summary_movement_uses_dash_for_stable_and_missing(self):
        rows = _render_summary_rows([
            {
                "match_no": "01",
                "league": "欧冠",
                "match_time": "09-10 20:00",
                "home_team": "A",
                "away_team": "B",
                "odds_analysis": {},
                "odds_movement": {"level": "稳定"},
            },
            {
                "match_no": "02",
                "league": "英超",
                "match_time": "09-10 21:00",
                "home_team": "C",
                "away_team": "D",
                "odds_analysis": {},
            },
        ])
        movement_cells = [
            line.strip() for line in rows.splitlines()
            if line.strip().startswith("<td><span class=\"badge badge-stable\">")
        ]
        self.assertEqual(movement_cells, ["<td><span class=\"badge badge-stable\">-</span></td>"] * 2)
        self.assertNotIn("无数据", rows)


class SummaryMarketDisplayTests(unittest.TestCase):
    def test_summary_shows_asian_rec_and_market_total(self):
        rows = _render_summary_rows([
            {
                "match_no": "01",
                "league": "欧冠",
                "match_time": "09-10 20:00",
                "home_team": "A",
                "away_team": "B",
                "odds_analysis": {
                    "asian_handicap": {"available": True, "recommended_side": "主队赢盘", "action": "主推"},
                },
                "market": {
                    "has_total": True,
                    "total": {
                        "line_text": "3/3.5",
                        "over_water": 0.9,
                        "under_water": 0.97,
                        "source": "澳门",
                    },
                },
            },
        ])
        self.assertIn("主队赢盘（主推）", rows)
        self.assertIn("3/3.5 0.90/0.97", rows)
        self.assertNotIn("3/3.5 0.90/0.97（澳门）", rows)
        self.assertNotIn("无市场", rows)

if __name__ == "__main__":
    unittest.main()
