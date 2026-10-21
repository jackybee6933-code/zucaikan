import unittest

from football_analysis.settlement import (
    profit_units,
    settle_asian,
    settle_total,
    split_line,
    summarize,
)


class SplitLineTests(unittest.TestCase):
    def test_quarter_lines_split_into_adjacent_half_lines(self):
        self.assertEqual(split_line(-0.25), (-0.5, 0.0))
        self.assertEqual(split_line(0.75), (0.5, 1.0))
        self.assertEqual(split_line(2.25), (2.0, 2.5))

    def test_half_and_integer_lines_stay_single(self):
        self.assertEqual(split_line(-0.5), (-0.5,))
        self.assertEqual(split_line(0.0), (0.0,))
        self.assertEqual(split_line(2.0), (2.0,))


class AsianSettlementTests(unittest.TestCase):
    def test_home_quarter_line_draw_is_half_loss(self):
        self.assertAlmostEqual(settle_asian(1, 1, -0.25, "home"), -0.5)

    def test_home_quarter_line_win_is_full_win(self):
        self.assertAlmostEqual(settle_asian(2, 1, -0.25, "home"), 1.0)

    def test_home_three_quarter_win_is_half_win(self):
        self.assertAlmostEqual(settle_asian(2, 1, -0.75, "home"), 0.5)

    def test_home_three_quarter_draw_is_full_loss(self):
        self.assertAlmostEqual(settle_asian(1, 1, -0.75, "home"), -1.0)

    def test_away_pick_mirrors_home_pick(self):
        self.assertAlmostEqual(settle_asian(1, 1, -0.25, "away"), 0.5)
        self.assertAlmostEqual(settle_asian(2, 1, -0.75, "away"), -0.5)

    def test_integer_line_can_push(self):
        self.assertAlmostEqual(settle_asian(1, 0, -1.0, "home"), 0.0)

    def test_invalid_side_returns_none(self):
        self.assertIsNone(settle_asian(1, 0, 0.0, ""))


class TotalSettlementTests(unittest.TestCase):
    def test_quarter_line_over_and_under_are_half_results(self):
        self.assertAlmostEqual(settle_total(2, 2.25, True), -0.5)
        self.assertAlmostEqual(settle_total(2, 2.25, False), 0.5)

    def test_three_quarter_line_over_and_under_are_half_results(self):
        self.assertAlmostEqual(settle_total(3, 2.75, True), 0.5)
        self.assertAlmostEqual(settle_total(3, 2.75, False), -0.5)

    def test_half_line_is_binary(self):
        self.assertAlmostEqual(settle_total(2, 2.5, True), -1.0)
        self.assertAlmostEqual(settle_total(2, 2.5, False), 1.0)

    def test_integer_line_can_push(self):
        self.assertAlmostEqual(settle_total(2, 2.0, True), 0.0)


class ProfitTests(unittest.TestCase):
    def test_profit_uses_half_stake_for_half_results(self):
        self.assertAlmostEqual(profit_units(1.0, 0.9), 0.9)
        self.assertAlmostEqual(profit_units(0.5, 0.9), 0.45)
        self.assertAlmostEqual(profit_units(0.0, 0.9), 0.0)
        self.assertAlmostEqual(profit_units(-0.5, 0.9), -0.5)
        self.assertAlmostEqual(profit_units(-1.0, 0.9), -1.0)


class SummarizeTests(unittest.TestCase):
    def test_counts_five_outcomes_and_excludes_pushes_from_total(self):
        stats = summarize([1.0, 0.5, 0.0, -0.5, -1.0])
        self.assertEqual(stats["bets"], 5)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["half_wins"], 1)
        self.assertEqual(stats["pushes"], 1)
        self.assertEqual(stats["half_losses"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["hits"], 2)
        self.assertEqual(stats["total"], 4)
        self.assertAlmostEqual(stats["units"], 0.0)


if __name__ == "__main__":
    unittest.main()
