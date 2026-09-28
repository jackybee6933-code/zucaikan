import unittest

from football_analysis.pipeline import _build_single_analysis
from football_analysis.report import total_display


def _row(movement: dict | None = None) -> dict:
    row = {"match_no": "3", "league": "英超", "home_name": "A", "away_name": "B"}
    if movement is not None:
        row["odds_movement"] = movement
    return row


def _movement_total(line_text="2.5", line=2.5, over=0.90, under=0.95) -> dict:
    return {
        "total": {
            "available": True,
            "current": {
                "line_text": line_text,
                "line": line,
                "over_water": over,
                "under_water": under,
            },
        }
    }


class TotalFallbackTests(unittest.TestCase):
    def test_falls_back_to_prematch_movement_when_odds_xml_missing(self):
        analysis = _build_single_analysis(
            _row(_movement_total()), {}, home_record={}, away_record={}
        )
        total = analysis["market"]["total"]
        self.assertTrue(analysis["market"]["has_total"])
        self.assertEqual(total["source"], "赛前指数")
        self.assertEqual(total["line_text"], "2.5")
        self.assertEqual(total["over_water"], 0.90)
        self.assertEqual(total["under_water"], 0.95)
        self.assertTrue(analysis["odds_analysis"]["total_goals"]["available"])

    def test_prefers_odds_xml_over_prematch_movement(self):
        odds = {
            "total_line": 3.0,
            "total_line_text": "3",
            "over_water": 0.85,
            "under_water": 1.00,
            "total_company": "am",
        }
        analysis = _build_single_analysis(
            _row(_movement_total()), odds, home_record={}, away_record={}
        )
        total = analysis["market"]["total"]
        self.assertEqual(total["line"], 3.0)
        self.assertEqual(total["source"], "澳门")

    def test_unavailable_when_both_sources_missing(self):
        analysis = _build_single_analysis(
            _row(), {}, home_record={}, away_record={}
        )
        self.assertFalse(analysis["market"]["has_total"])
        self.assertFalse(analysis["odds_analysis"]["total_goals"]["available"])

    def test_ignores_movement_total_without_line(self):
        movement = {"total": {"available": True, "current": {}}}
        analysis = _build_single_analysis(
            _row(movement), {}, home_record={}, away_record={}
        )
        self.assertFalse(analysis["market"]["has_total"])

    def test_report_shows_fallback_source(self):
        analysis = _build_single_analysis(
            _row(_movement_total()), {}, home_record={}, away_record={}
        )
        self.assertEqual(total_display(analysis), "2.5 0.90/0.95（赛前指数）")


if __name__ == "__main__":
    unittest.main()
