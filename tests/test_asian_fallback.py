import unittest

from football_analysis.pipeline import _build_single_analysis
from football_analysis.report import asian_display


def _row(movement: dict | None = None) -> dict:
    row = {"match_no": "4", "league": "欧洲杯", "home_name": "A", "away_name": "B"}
    if movement is not None:
        row["odds_movement"] = movement
    return row


def _movement_asian(line_display="主让一球", line=-1.0, home=0.93, away=0.95) -> dict:
    return {
        "asian": {
            "available": True,
            "current": {
                "line_text": "1",
                "line": line,
                "line_display": line_display,
                "home_water": home,
                "away_water": away,
            },
        }
    }


class AsianFallbackTests(unittest.TestCase):
    def test_falls_back_to_prematch_movement_when_odds_xml_missing(self):
        analysis = _build_single_analysis(
            _row(_movement_asian()), {}, home_record={}, away_record={}
        )
        asian = analysis["market"]["asian"]
        self.assertTrue(analysis["market"]["has_asian"])
        self.assertEqual(asian["source"], "赛前指数")
        self.assertEqual(asian["line_text"], "主让一球")
        self.assertEqual(asian["home_line"], -1.0)
        self.assertEqual(asian["home_water"], 0.93)
        self.assertEqual(asian["away_water"], 0.95)
        self.assertTrue(analysis["odds_analysis"]["asian_handicap"]["available"])

    def test_prefers_odds_xml_over_prematch_movement(self):
        odds = {
            "asian_home_line": -0.5,
            "asian_line_text": "让半球",
            "asian_home_water": 0.88,
            "asian_away_water": 1.00,
            "asian_company": "am",
        }
        analysis = _build_single_analysis(
            _row(_movement_asian()), odds, home_record={}, away_record={}
        )
        asian = analysis["market"]["asian"]
        self.assertEqual(asian["line_text"], "让半球")
        self.assertEqual(asian["source"], "澳门")

    def test_row_asian_wins_over_prematch_movement(self):
        row = _row(_movement_asian())
        row["asian_line_text"] = "受让半球"
        row["asian_home_line"] = 0.5
        analysis = _build_single_analysis(
            row, {}, home_record={}, away_record={}
        )
        asian = analysis["market"]["asian"]
        self.assertEqual(asian["line_text"], "受让半球")
        self.assertEqual(asian["source"], "")

    def test_unavailable_when_both_sources_missing(self):
        analysis = _build_single_analysis(
            _row(), {}, home_record={}, away_record={}
        )
        self.assertFalse(analysis["market"]["has_asian"])
        self.assertFalse(analysis["odds_analysis"]["asian_handicap"]["available"])

    def test_ignores_movement_asian_without_line(self):
        movement = {"asian": {"available": True, "current": {}}}
        analysis = _build_single_analysis(
            _row(movement), {}, home_record={}, away_record={}
        )
        self.assertFalse(analysis["market"]["has_asian"])

    def test_report_shows_fallback_source(self):
        analysis = _build_single_analysis(
            _row(_movement_asian()), {}, home_record={}, away_record={}
        )
        self.assertEqual(asian_display(analysis), "主让一球 0.93/0.95（赛前指数）")


if __name__ == "__main__":
    unittest.main()
