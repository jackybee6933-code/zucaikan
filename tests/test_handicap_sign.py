import tempfile
import unittest
from pathlib import Path

from football_analysis.main import review_issue
from football_analysis.store import save_issue_movements

ISSUE = "26135"


class HandicapSignTests(unittest.TestCase):
    """home_line 符号约定：负=主让、正=主受让，统一为 goal_diff + line。

    受让盘平局算打出（主受让半球时赢或平都打出），旧写法在 line>0 时反号，
    把平局误判成未打出，导致受让盘的命中率被系统性低估。
    """

    @staticmethod
    def _item(line: float, score: str, side: str) -> dict:
        return {
            "match_no": "1", "league": "欧冠", "match_time": "",
            "home_team": "H", "away_team": "A", "final_score": score,
            "market": {"asian": {"home_line": line}, "total": {"line": 2.5}},
            "odds_analysis": {
                "asian_handicap": {"available": True, "recommended_side": side, "action": "轻仓"},
                "total_goals": {"available": True, "recommended_side": "大球", "action": "轻仓"},
            },
            "odds_movement": {"available": False, "attention": False, "level": "稳定"},
        }

    def test_home_gives_one_cover_only_by_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [self._item(-1.0, "2-0", "主队赢盘")], ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (1, 1))

    def test_home_gives_one_push_is_excluded_from_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [self._item(-1.0, "1-0", "主队赢盘")], ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (0, 0))
        self.assertEqual(r["asian_bets"], 1)
        self.assertEqual(r["asian_pushes"], 1)

    def test_home_receives_half_and_draws_still_covers(self):
        """受让半球打平：主队赢或平都打出。旧代码在这里判错。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [self._item(0.5, "1-1", "主队赢盘")], ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (1, 1))

    def test_home_receives_half_and_loses_does_not_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [self._item(0.5, "0-1", "主队赢盘")], ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (0, 1))

    def test_away_side_is_mirror_image(self):
        """客队赢盘 + 主受让半球 + 平局：主队打出，客队没打出。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [self._item(0.5, "1-1", "客队赢盘")], ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (0, 1))

    def test_receiving_side_raises_hit_rate(self):
        """受让盘整体命中率应高于让让盘，修复前恰好相反。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            items = [self._item(0.5, "1-1", "主队赢盘") for _ in range(3)]
            items += [self._item(-1.0, "1-0", "主队赢盘")]   # 让一球走水：不入分母
            save_issue_movements(db, items, ISSUE, "")
            r = review_issue(db, ISSUE, refresh=False)
        self.assertEqual((r["asian_hits"], r["asian_total"]), (3, 3))
        self.assertEqual(r["asian_bets"], 4)
        self.assertEqual(r["asian_pushes"], 1)
