import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from football_analysis.bets import (
    BetError,
    bet_trend,
    export_bets_csv,
    bet_options,
    create_bet,
    delete_bet,
    list_bets,
    parse_kickoff,
    summarize_bets,
    update_bet,
)
from football_analysis.query_report import render_query_html
from football_analysis.store import (
    query_movements,
    save_issue_movements,
    update_match_scores,
)

ISSUE = "26145"
NOW = datetime(2026, 10, 18, 12, 0)


def _item(
    *,
    match_time: str = "10-18 23:30",
    asian_line: float = 0.75,
    total_line: float = 2.5,
    final_score: str = "",
) -> dict:
    return {
        "match_no": "1",
        "league": "英超",
        "match_time": match_time,
        "home_team": "主队",
        "away_team": "客队",
        "final_score": final_score,
        "market": {
            "has_asian": True,
            "has_total": True,
            "asian": {
                "home_line": asian_line,
                "line_text": "受半球/一球",
                "home_water": 1.04,
                "away_water": 0.8,
                "source": "澳门",
            },
            "total": {
                "line": total_line,
                "line_text": f"{total_line:g}",
                "over_water": 0.9,
                "under_water": 0.9,
                "source": "澳门",
            },
        },
        "odds_analysis": {
            "asian_handicap": {
                "available": True,
                "recommended_side": "客队赢盘",
                "action": "轻仓",
                "confidence": 0.60,
                "margin": 0.12,
            },
            "total_goals": {
                "available": True,
                "recommended_side": "小球",
                "action": "观望",
                "confidence": 0.52,
                "margin": 0.03,
            },
        },
        "odds_movement": {"available": False, "attention": False, "level": "稳定"},
    }


def _seed(tmp: str, item: dict | None = None) -> Path:
    db = Path(tmp) / "bets.sqlite"
    save_issue_movements(
        db, [item or _item()], ISSUE, "https://example.test", captured_at=NOW
    )
    return db


class BetOptionsTests(unittest.TestCase):
    def test_options_expose_two_markets_and_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            options = bet_options(db, ISSUE, "1", now=NOW)
        self.assertEqual(options["kickoff_at"], "2026-10-18T23:30")
        self.assertFalse(options["locked"])
        self.assertEqual(set(options["markets"]), {"asian", "total"})
        self.assertEqual(
            options["markets"]["asian"]["recommendation"]["side"], "away"
        )
        self.assertEqual(
            options["markets"]["total"]["recommendation"]["default"], ""
        )


class BetLifecycleTests(unittest.TestCase):
    def test_create_update_and_delete_before_kickoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            created = create_bet(
                db, ISSUE, "1", "asian", "away", 10, now=NOW
            )
            self.assertEqual(created["stake"], 10.0)
            self.assertEqual(created["selection"], "away")
            self.assertEqual(created["follows_recommendation"], 1)
            self.assertEqual(created["status_label"], "待开赛")
            self.assertTrue(created["can_edit"])

            updated = update_bet(
                db, created["id"], selection="home", stake=20, now=NOW
            )
            self.assertEqual(updated["selection"], "home")
            self.assertEqual(updated["stake"], 20.0)
            self.assertEqual(updated["water"], 1.04)
            self.assertEqual(updated["follows_recommendation"], 0)

            deleted = delete_bet(db, created["id"], now=NOW)
            self.assertEqual(deleted["deleted"], created["id"])
            self.assertEqual(list_bets(db, issue=ISSUE, now=NOW), [])

    def test_one_bet_per_market_and_two_markets_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            create_bet(db, ISSUE, "1", "asian", "away", 10, now=NOW)
            with self.assertRaises(BetError) as raised:
                create_bet(db, ISSUE, "1", "asian", "home", 10, now=NOW)
            self.assertEqual(raised.exception.status, 409)

            create_bet(db, ISSUE, "1", "total", "over", 10, now=NOW)
            self.assertEqual(len(list_bets(db, issue=ISSUE, now=NOW)), 2)

    def test_rejects_invalid_stake_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            with self.assertRaises(BetError):
                create_bet(db, ISSUE, "1", "asian", "away", 0, now=NOW)
            with self.assertRaises(BetError):
                create_bet(db, ISSUE, "1", "asian", "draw", 10, now=NOW)

    def test_operations_locked_after_kickoff_or_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            created = create_bet(db, ISSUE, "1", "asian", "away", 10, now=NOW)
            late = datetime(2026, 10, 19, 0, 0)
            with self.assertRaises(BetError) as raised:
                update_bet(db, created["id"], stake=20, now=late)
            self.assertEqual(raised.exception.status, 409)
            with self.assertRaises(BetError):
                delete_bet(db, created["id"], now=late)

            update_match_scores(db, ISSUE, {"1": "2-1"})
            with self.assertRaises(BetError):
                update_bet(db, created["id"], stake=20, now=NOW)
            with self.assertRaises(BetError):
                delete_bet(db, created["id"], now=NOW)


class BetSettlementTests(unittest.TestCase):
    def test_half_win_and_half_loss_are_settled_from_final_score(self):
        item = _item(asian_line=-0.75, total_line=3.25)
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, item)
            asian = create_bet(db, ISSUE, "1", "asian", "home", 10, now=NOW)
            total = create_bet(db, ISSUE, "1", "total", "over", 10, now=NOW)
            update_match_scores(db, ISSUE, {"1": "2-1"})
            bets = {bet["id"]: bet for bet in list_bets(db, issue=ISSUE, now=NOW)}
        self.assertEqual(bets[asian["id"]]["result_label"], "赢半")
        self.assertAlmostEqual(bets[asian["id"]]["profit"], 5.2)
        self.assertEqual(bets[total["id"]]["result_label"], "输半")
        self.assertAlmostEqual(bets[total["id"]]["profit"], -5.0)
        self.assertEqual(bets[asian["id"]]["status"], "settled")


class BetStatsTests(unittest.TestCase):
    def test_summary_trend_and_csv(self):
        item = _item(asian_line=-0.75, total_line=2.25)
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, item)
            create_bet(db, ISSUE, "1", "asian", "away", 10, now=NOW)
            create_bet(db, ISSUE, "1", "total", "over", 10, now=NOW)
            update_match_scores(db, ISSUE, {"1": "2-1"})
            bets = list_bets(db, issue=ISSUE, now=NOW)
            stats = summarize_bets(bets)
            trend = bet_trend(db, now=NOW)
            csv_text = export_bets_csv(db, issue=ISSUE, now=NOW)
        self.assertEqual(stats["bets"], 2)
        self.assertEqual(stats["settled"], 2)
        self.assertEqual(stats["stake"], 20.0)
        self.assertAlmostEqual(stats["profit"], 4.0)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["decided"], 2)
        self.assertAlmostEqual(stats["hit_rate"], 50.0)
        self.assertEqual(stats["follows"]["follow"]["bets"], 1)
        self.assertEqual(stats["follows"]["none"]["bets"], 1)
        self.assertEqual(trend[0]["issue"], ISSUE)
        self.assertIn("期号,场次", csv_text)
        self.assertIn("跟随", csv_text)
        self.assertIn("无推荐", csv_text)


class KickoffTests(unittest.TestCase):
    def test_short_match_time_uses_issue_year(self):
        self.assertEqual(
            parse_kickoff("26145", "10-18 23:30"),
            datetime(2026, 10, 18, 23, 30),
        )

    def test_full_match_time_keeps_its_year(self):
        self.assertEqual(
            parse_kickoff("26145", "2025-01-02 08:05"),
            datetime(2025, 1, 2, 8, 5),
        )


class BetRenderTests(unittest.TestCase):
    def test_static_page_is_read_only_and_serve_page_has_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            create_bet(db, ISSUE, "1", "asian", "away", 10, now=NOW)
            rows = query_movements(db, issue=ISSUE)
            bets = list_bets(db, issue=ISSUE, now=NOW)
            common = {
                "rows": rows,
                "issues": [{"issue": ISSUE}],
                "issue": ISSUE,
                "bets": bets,
            }
            static_html = render_query_html(**common)
            serve_html = render_query_html(**common, port="8765")
            trend_html = render_query_html(
                **common,
                port="8765",
                bet_trend=[{
                    "issue": ISSUE, "bets": 1, "settled": 0, "stake": 10.0,
                    "profit": 0.0, "roi": 0.0, "hit_rate": 0.0,
                }],
            )
        self.assertIn("本期投注", static_html)
        self.assertNotIn('id="bet-dialog"', static_html)
        self.assertIn('id="bet-dialog"', serve_html)
        self.assertIn("bet-open", serve_html)
        self.assertIn("bet-more", serve_html)
        self.assertIn('id="bets-section"', serve_html)
        self.assertIn("data-bet-tip", serve_html)
        self.assertIn("10 积分", serve_html)
        self.assertIn(".bets-table tbody tr:nth-child(even) td", serve_html)
        self.assertIn("bet-flash", serve_html)
        self.assertIn("bet-chip", serve_html)
        self.assertIn("/bets.csv?issue=26145", serve_html)
        self.assertIn('id="bet-row-1"', serve_html)
        self.assertIn("跨期投注趋势", trend_html)

    def test_started_match_button_says_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp, _item(final_score="2-1"))
            html = render_query_html(
                rows=query_movements(db, issue=ISSUE),
                issues=[{"issue": ISSUE}],
                issue=ISSUE,
                bets=[],
                port="8765",
            )
        self.assertIn("disabled", html)
        self.assertIn("投注</button>", html)
        self.assertNotIn('disabled title="已结算"', html)


if __name__ == "__main__":
    unittest.main()
