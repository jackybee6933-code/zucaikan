import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from football_analysis.query_report import _data_freshness


class QueryFreshnessTests(unittest.TestCase):
    def test_missing_artifact_is_reported(self):
        result = _data_freshness(
            [],
            [{"captured_at": "2026-10-19T12:00:00"}],
            issue="26147",
            cache_dir=tempfile.mkdtemp(),
        )
        self.assertEqual(result["class"], "freshness-warn")
        self.assertIn("抓取时间：2026-10-19 12:00:00", result["captured"])
        self.assertIn("盘口产物：未找到", result["pages"])
        self.assertIn("数据产物：未找到", result["data"])
        self.assertNotIn("sources", result)

    def test_stale_artifact_distinguishes_empty_and_failed_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            path = cache / "odds_pages_26147.json"
            path.write_text(
                json.dumps(
                    {
                        "1|yazhi": {"status": "empty", "rows": []},
                        "1|daxiao": {"status": "timeout", "rows": []},
                    }
                ),
                encoding="utf-8",
            )
            old = time.time() - 7 * 3600
            os.utime(path, (old, old))
            result = _data_freshness(
                [{"captured_at": "2026-10-19T01:00:00"}],
                [],
                issue="26147",
                cache_dir=cache,
            )
        self.assertEqual(result["class"], "freshness-warn")
        self.assertIn("超过 1h", result["pages"])
        self.assertIn("抓取失败 1 页", result["pages"])
        self.assertIn("上游未开盘 1 页", result["pages"])
        self.assertIn("数据产物：未找到", result["data"])

    def test_no_issue_does_not_claim_one_artifact(self):
        result = _data_freshness(
            [{"captured_at": "2026-10-19T12:00:00"}],
            [],
            issue="",
            cache_dir=Path("unused"),
        )
        self.assertEqual(result["class"], "freshness-muted")
        self.assertIn("选择期号后查看", result["pages"])
        self.assertIn("选择期号后查看", result["data"])

    def test_odds_and_data_have_independent_ages(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            path = cache / "odds_pages_26147.json"
            path.write_text(
                json.dumps(
                    {
                        "1|yazhi": {"status": "ok", "rows": [["293"]]},
                        "1|daxiao": {"status": "ok", "rows": [["293"]]},
                        "1|shuju": {"status": "ok", "text": "资料"},
                    }
                ),
                encoding="utf-8",
            )
            now = time.time()
            (cache / "odds_pages_26147.meta.json").write_text(
                json.dumps({"yazhi": now - 7 * 3600, "daxiao": now - 7 * 3600, "shuju": now}),
                encoding="utf-8",
            )
            result = _data_freshness(
                [], [], issue="26147", cache_dir=cache
            )
        self.assertIn("超过 1h", result["pages"])
        self.assertIn("数据产物：刚刚", result["data"])
        self.assertEqual(result["pages_class"], "freshness-stale")
        self.assertEqual(result["data_class"], "freshness-ok")

    def test_ai_merge_does_not_make_legacy_odds_look_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            path = cache / "odds_pages_26147.json"
            path.write_text(
                json.dumps(
                    {
                        "1|yazhi": {"status": "ok", "rows": [["293"]]},
                        "1|daxiao": {"status": "ok", "rows": [["293"]]},
                        "1|shuju": {"status": "ok", "text": "资料"},
                    }
                ),
                encoding="utf-8",
            )
            result = _data_freshness(
                [], [], issue="26147", cache_dir=cache
            )
        self.assertIn("盘口产物：未记录", result["pages"])
        self.assertNotIn("盘口产物：刚刚", result["pages"])
        self.assertIn("数据产物：刚刚", result["data"])


if __name__ == "__main__":
    unittest.main()
