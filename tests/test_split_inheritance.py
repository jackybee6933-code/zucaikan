import json
import tempfile
import unittest
from pathlib import Path

from football_analysis.store import query_movements, save_issue_movements


def _item(match_no: str, split: list | None = None) -> dict:
    movement: dict = {
        "available": True,
        "attention": False,
        "level": "稳定",
        "reasons": [],
        "asian": {"available": True},
    }
    if split is not None:
        movement["split"] = split
    return {
        "match_no": match_no,
        "league": "英超",
        "match_time": "10-10 03:00",
        "home_team": f"H{match_no}",
        "away_team": f"A{match_no}",
        "odds_movement": movement,
    }


SPLIT_BLOCK = [{"market": "亚盘", "picked": "293", "rows": [{"cid": "293", "name": "威廉", "initial": "0.90", "current": "0.85"}]}]


class SplitInheritanceTests(unittest.TestCase):
    def test_new_run_inherits_split_from_previous_run(self):
        """普通抓取不读单场页产物，新 run 缺 split 时从上一 run 继承。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            # 第一次入库：带 split（模拟「刷新盘口分歧」后的 run）
            save_issue_movements(db, [_item("1", split=SPLIT_BLOCK), _item("2")], "26140")
            # 第二次入库：完全没有 split（模拟查询页「抓取并入库」）
            save_issue_movements(db, [_item("1"), _item("2")], "26140")

            rows = query_movements(db, issue="26140", latest_only=True)
            by_no = {r["match_no"]: r for r in rows}
            self.assertEqual(by_no["1"]["split"], SPLIT_BLOCK)
            # 上一 run 本来就没 split 的场次，继承后仍然没有
            self.assertEqual(by_no["2"]["split"], [])

    def test_new_run_split_not_clobbered_by_stale_inheritance(self):
        """新 run 自己有 split 时，不用上一 run 的旧值覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(db, [_item("1", split=SPLIT_BLOCK)], "26140")
            fresh = [{"market": "大小球", "picked": "5", "rows": []}]
            save_issue_movements(db, [_item("1", split=fresh)], "26140")

            rows = query_movements(db, issue="26140", latest_only=True)
            self.assertEqual(rows[0]["split"], fresh)

    def test_first_run_without_split_stays_empty(self):
        """期次首次入库、没有历史 run 时不报错也不凭空造 split。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(db, [_item("1")], "26140")
            rows = query_movements(db, issue="26140", latest_only=True)
            self.assertEqual(rows[0]["split"], [])


if __name__ == "__main__":
    unittest.main()
