import tempfile
import unittest
from pathlib import Path

from football_analysis.ev_ledger import (
    _cover_result,
    build_ledger_rows,
    ledger_trend,
    reconcile_ledger,
    save_ev_ledger,
    top_picks,
)
from football_analysis.store import save_issue_movements


def _item(
    match_no: str,
    ev_home: float | None,
    ev_away: float | None,
    *,
    line: float = -1.0,
    line_text: str = "受让半球",
    final_score: str = "",
    available: bool = True,
) -> dict:
    return {
        "match_no": match_no,
        "league": "西甲",
        "home_team": f"H{match_no}",
        "away_team": f"A{match_no}",
        "final_score": final_score,
        "market": {"asian": {"home_line": line, "line_text": line_text}},
        "odds_analysis": {
            "asian_handicap": {
                "available": available,
                "ev_home": ev_home,
                "ev_away": ev_away,
            }
        },
    }


def _summary(
    *,
    rows: int,
    settled: int,
    wins: int,
    half_wins: int,
    push: int,
    half_losses: int,
    losses: int,
    units: float,
) -> dict:
    return {
        "rows": rows,
        "settled": settled,
        "wins": wins,
        "half_wins": half_wins,
        "push": push,
        "half_losses": half_losses,
        "losses": losses,
        "hits": wins + half_wins,
        "units": units,
    }


class BuildLedgerRowsTests(unittest.TestCase):
    def test_sorts_byAbsoluteEvDiffDescending(self):
        rows = build_ledger_rows(
            [
                _item("1", 0.10, -0.30),   # |diff| 0.40
                _item("2", 0.05, 0.50),    # |diff| 0.45
                _item("3", 0.00, 0.05),    # |diff| 0.05
            ]
        )
        self.assertEqual([r["match_no"] for r in rows], ["2", "1", "3"])

    def test_skipsMissingEvAndUnavailableAsian(self):
        rows = build_ledger_rows(
            [
                _item("1", 0.2, -0.1),
                _item("2", None, -0.1),
                _item("3", 0.2, None),
                _item("4", 0.2, -0.1, available=False),
            ]
        )
        self.assertEqual([r["match_no"] for r in rows], ["1"])

    def test_pickedSideFollowsLargerEv(self):
        rows = build_ledger_rows(
            [
                _item("1", 0.2, -0.1),
                _item("2", -0.1, 0.2),
                _item("3", 0.1, 0.1),
            ]
        )
        self.assertEqual({r["match_no"]: r["picked_side"] for r in rows},
                         {"1": "home", "2": "away", "3": ""})

    def test_copiesMarketLineAndScore(self):
        rows = build_ledger_rows([_item("1", 0.2, -0.1)])
        self.assertEqual(rows[0]["asian_line"], -1.0)
        self.assertEqual(rows[0]["asian_line_text"], "受让半球")


class CoverResultTests(unittest.TestCase):
    """符号约定与 main._issue_hit_rates 一致：home_handicap = (主进球-客进球) + line。"""

    def test_homePickCoversWhenGoalDiffPlusLinePositive(self):
        self.assertAlmostEqual(_cover_result(3, 1, -1.0, "home"), 1.0)   # 2-1=1 +(-1)=1 赢
        self.assertAlmostEqual(_cover_result(2, 1, -1.0, "home"), 0.0)   # 平局走水
        self.assertAlmostEqual(_cover_result(1, 2, -1.0, "home"), -1.0)

    def test_awayPickUsesNegatedHandicap(self):
        self.assertAlmostEqual(_cover_result(3, 1, -1.0, "away"), -1.0)
        self.assertAlmostEqual(_cover_result(0, 0, -1.0, "away"), 1.0)   # 主让一球 0-0，客队赢

    def test_quarter_lines_return_half_results(self):
        self.assertAlmostEqual(_cover_result(2, 1, -0.75, "home"), 0.5)
        self.assertAlmostEqual(_cover_result(1, 1, -0.25, "home"), -0.5)
        self.assertAlmostEqual(_cover_result(2, 1, -0.75, "away"), -0.5)

    def test_noPickIsNone(self):
        self.assertIsNone(_cover_result(3, 1, -1.0, ""))


class SaveAndReconcileTests(unittest.TestCase):
    def test_returnsZeroWithoutAnalyses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(save_ev_ledger(Path(tmp) / "t.sqlite", [], "26135", 1), 0)

    def test_storesFinalScoreFromAnalyses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_ev_ledger(db, [_item("1", 0.2, -0.1, final_score="3-1")], "26135", 7)
            self.assertEqual(top_picks(db, "26135")[0]["final_score"], "3-1")

    def test_sameRunIdIsNotWrittenTwice(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            items = [_item("1", 0.2, -0.1), _item("2", 0.1, 0.1)]
            self.assertEqual(save_ev_ledger(db, items, "26135", 7), 2)
            self.assertEqual(save_ev_ledger(db, items, "26135", 7), 2)

    def test_reconcileWithExplicitScores(self):
        items = [
            _item("1", 0.5, -0.2),   # 主队选边，3-1 赢
            _item("2", -0.5, 0.2),   # 客队选边，3-1 输
            _item("3", 0.4, -0.1),   # 主队选边，2-1 走水
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_ev_ledger(db, items, "26135", 1)
            summary = reconcile_ledger(
                db, "26135", {"1": "3-1", "2": "3-1", "3": "2-1"}
            )
            self.assertEqual(
                summary,
                _summary(rows=3, settled=3, wins=1, half_wins=0, push=1,
                         half_losses=0, losses=1, units=0.0),
            )
            self.assertEqual(top_picks(db, "26135", limit=1)[0]["final_score"], "3-1")

    def test_reconcileReadsScoresFromMovements(self):
        """复盘把比分写进 match_movements，对账时自动取同一 run 的比分。"""
        scored = [
            {**_item("1", 0.5, -0.2), "final_score": "3-1"},
            {**_item("2", -0.5, 0.2), "final_score": "3-1"},
            {**_item("3", 0.4, -0.1), "final_score": "2-1"},
        ]
        fresh = [_item("1", 0.5, -0.2), _item("2", -0.5, 0.2), _item("3", 0.4, -0.1)]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            saved = save_issue_movements(db, scored, "26135", "")
            self.assertEqual(
                save_ev_ledger(db, fresh, "26135", saved["run_id"], saved["captured_at"]),
                3,
            )
            summary = reconcile_ledger(db, "26135")
        self.assertEqual(
            summary,
            _summary(rows=3, settled=3, wins=1, half_wins=0, push=1,
                     half_losses=0, losses=1, units=0.0),
        )

    def test_reconcileUnknownIssueIsEmpty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                reconcile_ledger(Path(tmp) / "t.sqlite", "99999"),
                _summary(rows=0, settled=0, wins=0, half_wins=0, push=0,
                         half_losses=0, losses=0, units=0.0),
            )

    def test_reconcile_counts_half_wins_and_half_losses(self):
        items = [
            _item("1", 0.5, -0.2, line=-0.75),
            _item("2", 0.5, -0.2, line=-0.25),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_ev_ledger(db, items, "26135", 1)
            summary = reconcile_ledger(db, "26135", {"1": "2-1", "2": "1-1"})
        self.assertEqual(
            summary,
            _summary(rows=2, settled=2, wins=0, half_wins=1, push=0,
                     half_losses=1, losses=0, units=0.0),
        )


class LedgerTrendTests(unittest.TestCase):
    def test_ordersByIssueAndAggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_ev_ledger(db, [_item("1", 0.5, -0.2), _item("2", -0.5, 0.2)], "26135", 1)
            save_ev_ledger(db, [_item("1", 0.5, -0.2)], "26136", 2)
            reconcile_ledger(db, "26135", {"1": "3-1", "2": "3-1"})
            reconcile_ledger(db, "26136", {"1": "0-0"})
            trend = ledger_trend(db)
        self.assertEqual([row["issue"] for row in trend], ["26135", "26136"])
        self.assertEqual(
            trend[0],
            {
                **_summary(rows=2, settled=2, wins=1, half_wins=0, push=0,
                           half_losses=0, losses=1, units=0.0),
                "issue": "26135",
            },
        )
        self.assertEqual(
            trend[1],
            {
                **_summary(rows=1, settled=1, wins=0, half_wins=0, push=0,
                           half_losses=0, losses=1, units=-1.0),
                "issue": "26136",
            },
        )


if __name__ == "__main__":
    unittest.main()
