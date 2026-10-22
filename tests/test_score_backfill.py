import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import main
from football_analysis.store import (
    load_issue_scores,
    query_movements,
    save_issue_movements,
)


def _item(match_no: str, score: str) -> dict:
    return {
        "match_no": match_no,
        "league": "欧冠",
        "match_time": "10-21 20:00",
        "home_team": f"主队{match_no}",
        "away_team": f"客队{match_no}",
        "final_score": score,
        "market": {},
        "odds_analysis": {},
        "odds_movement": {},
    }


class IssueScoreTests(unittest.TestCase):
    def test_all_zero_cached_run_does_not_hide_known_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(
                db,
                [_item("1", "4 - 3"), _item("2", "1 - 1")],
                "26146",
            )
            save_issue_movements(
                db,
                [_item("1", "0 - 0"), _item("2", "0 - 0")],
                "26146",
            )

            scores = load_issue_scores(db, "26146")

            self.assertEqual(scores, {"1": "4 - 3", "2": "1 - 1"})

    def test_real_zero_zero_is_kept_in_mixed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(
                db,
                [_item("1", "0 - 0"), _item("2", "1 - 1")],
                "26146",
            )

            scores = load_issue_scores(db, "26146")

            self.assertEqual(scores, {"1": "0 - 0", "2": "1 - 1"})


class PreviousIssueBackfillTests(unittest.TestCase):
    def test_cached_zero_does_not_replace_known_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(
                db,
                [_item("1", "4 - 3"), _item("2", "1 - 1")],
                "26146",
            )
            save_issue_movements(
                db,
                [_item("1", "0 - 0"), _item("2", "0 - 0")],
                "26146",
            )
            cached = [_item("1", "0 - 0"), _item("2", "0 - 0")]
            issues = [{"issue": "26147"}, {"issue": "26146"}]

            with mock.patch.object(main, "list_issues", return_value=issues), mock.patch.object(
                main,
                "analyze_issue",
                return_value=(cached, "26146", ""),
            ):
                main._backfill_previous_issue(
                    Path(tmp) / "cache",
                    db,
                    object(),
                    1,
                )

            rows = query_movements(db, issue="26146", latest_only=True)
            scores = {row["match_no"]: row["final_score"] for row in rows}
            self.assertEqual(scores, {"1": "4 - 3", "2": "1 - 1"})
