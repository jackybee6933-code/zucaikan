import tempfile
import unittest
from pathlib import Path

from football_analysis.main import review_issue
from football_analysis.query_app import format_review, run_review
from football_analysis.store import save_issue_movements


def _item(
    match_no: str,
    final_score: str,
    *,
    asian_line: float = -1.0,
    asian_side: str = "主队赢盘",
    asian_action: str = "轻仓",
    total_line: float = 2.5,
    total_side: str = "大球",
    total_action: str = "轻仓",
) -> dict:
    return {
        "match_no": match_no,
        "league": "欧冠",
        "match_time": "10-07 02:45",
        "home_team": f"H{match_no}",
        "away_team": f"A{match_no}",
        "final_score": final_score,
        "market": {"asian": {"home_line": asian_line}, "total": {"line": total_line}},
        "odds_analysis": {
            "asian_handicap": {
                "available": bool(asian_action),
                "recommended_side": asian_side,
                "action": asian_action,
            },
            "total_goals": {
                "available": bool(total_action),
                "recommended_side": total_side,
                "action": total_action,
            },
        },
        "odds_movement": {"available": False, "attention": False, "level": "稳定"},
    }


# 主让一球：3-1 赢盘 / 2-1 走水 / 1-2 输盘 / 0-0 则客队赢盘赢
# 大小球 2.5：4 球中大 / 3 球不中小 / 0 球中小
ITEMS = [
    _item("1", "3-1"),                                                   # 亚中 + 大中
    _item("2", "2-1", total_side="小球"),                                # 亚走水 + 小不中
    _item("3", "1-2", total_action="观望"),                               # 亚失 + 大不计
    _item("4", "0-0", asian_side="客队赢盘", total_side="小球"),          # 亚中 + ��中
    _item("5", "0-0", asian_action="观望"),                               # 亚不计 + 大失
    _item("6", "2-1", total_action="观望"),                               # 亚走水 + 大不计
    _item("7", ""),                                                      # 未完赛
]

# 亚盘分母 = 1,2,3,4,6 = 5；大小球分母 = 1,2,4,5 = 4。
# 两者刻意不同，用来验证「观望」是按市场分别排除的，而不是一刀切。
EXPECTED = {"issue": "26135", "match_count": 7, "scored": 6, "run_id": 1,
            "asian_hits": 2, "asian_total": 5, "total_hits": 2, "total_total": 4}
TEXT = "26135 已完成 6/7 场（run #1） · 亚盘 2/5 (40%) · 大小球 2/4 (50%)"


def _seed(tmp: str, issue: str = "26135") -> Path:
    db = Path(tmp) / "test.sqlite"
    save_issue_movements(db, ITEMS, issue, "https://example.test")
    return db


class ReviewIssueTests(unittest.TestCase):
    def test_counts_only_recommended_and_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = review_issue(_seed(tmp), "26135", refresh=False)
            for key, want in EXPECTED.items():
                self.assertEqual(got[key], want, key)

    def test_push_is_counted_but_not_a_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = review_issue(_seed(tmp), "26135", refresh=False)
            self.assertEqual(got["asian_total"], 5)
            self.assertEqual(got["asian_hits"], 2)

    def test_watch_action_excluded_per_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = review_issue(_seed(tmp), "26135", refresh=False)
            self.assertEqual(got["asian_total"], 5)
            self.assertEqual(got["total_total"], 4)
            self.assertNotEqual(got["asian_total"], got["total_total"])

    def test_unscored_match_excluded_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = review_issue(_seed(tmp), "26135", refresh=False)
            self.assertEqual(got["match_count"], 7)
            self.assertEqual(got["scored"], 6)

    def test_unknown_issue_is_empty_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = review_issue(_seed(tmp), "99999", refresh=False)
            self.assertEqual(got["match_count"], 0)
            self.assertEqual(got["asian_total"], 0)

    def test_refresh_false_leaves_no_refresh_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                review_issue(_seed(tmp), "26135", refresh=False)["refreshed"], {}
            )

    def test_empty_refresh_score_does_not_clobber_real_score(self):
        """复盘抓到空比分（未开赛/失败）时不得冲掉已有真比分。"""
        from unittest import mock
        from football_analysis import main

        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            # 场次 1 已有真比分 3-1；模拟复盘只拿到空串
            with mock.patch.object(main, "_refresh_scores", return_value={"issue": "26135", "filled": 0, "match_count": 7, "updated": 0, "errors": []}):
                review_issue(db, "26135", refresh=True, cache_dir=None)
            from football_analysis.store import query_movements
            rows = query_movements(db, latest_only=True)
            first = next(r for r in rows if r["match_no"] == "1")
            self.assertEqual(first["final_score"], "3-1")


class FormatReviewTests(unittest.TestCase):
    def test_formats_hits_and_percentages(self):
        self.assertEqual(format_review(EXPECTED), TEXT)

    def test_missing_recommendations_say_无推荐(self):
        text = format_review(
            {"issue": "26135", "scored": 0, "match_count": 14,
             "asian_hits": 0, "asian_total": 0, "total_hits": 0, "total_total": 0}
        )
        self.assertEqual(text, "26135 已完成 0/14 场 · 亚盘 无推荐 · 大小球 无推荐")

    def test_percent_rounds_to_whole_number(self):
        text = format_review(
            {"issue": "1", "scored": 3, "match_count": 3,
             "asian_hits": 1, "asian_total": 3, "total_hits": 2, "total_total": 3}
        )
        self.assertIn("亚盘 1/3 (33%)", text)
        self.assertIn("大小球 2/3 (67%)", text)

    def test_appends_refresh_errors(self):
        text = format_review(
            {"issue": "26135", "scored": 0, "match_count": 14,
             "asian_hits": 0, "asian_total": 0, "total_hits": 0, "total_total": 0,
             "refreshed": {"error": "网络失败", "errors": ["场次 1"]}}
        )
        self.assertIn("比分刷新失败：网络失败；场次 1", text)


class RunReviewTests(unittest.TestCase):
    """查询页 /review 用的入口：返回可直接展示的文案。"""

    def test_returns_formatted_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = run_review(_seed(tmp), "26135", refresh=False)
        self.assertEqual(text, TEXT)

    def test_uses_latest_run_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _seed(tmp)
            save_issue_movements(
                db,
                [_item("1", "1-1", asian_action="观望", total_action="观望")],
                "26135",
                "",
            )
            text = run_review(db, "26135", refresh=False)
        self.assertIn("已完成 1/1 场", text)


if __name__ == "__main__":
    unittest.main()
