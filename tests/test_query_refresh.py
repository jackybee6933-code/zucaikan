import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import query_app
from football_analysis.odds_pages import PAGES_STALE_HOURS
from football_analysis.store import save_issue_movements


def _item() -> dict:
    return {
        "match_no": "1",
        "league": "欧冠",
        "match_time": "10-07 02:45",
        "home_team": "H",
        "away_team": "A",
        "odds_analysis": {
            "asian_handicap": {"available": True, "recommended_side": "主队赢盘", "action": "轻仓"}
        },
        "odds_movement": {"available": False, "attention": False, "level": "稳定"},
    }


def _artifact(cache_dir: Path, issue: str, age_hours: float) -> Path:
    path = cache_dir / f"odds_pages_{issue}.json"
    path.write_text(json.dumps({"9|yazhi": {"status": "empty", "rows": []}}), encoding="utf-8")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


class DedupMessageTests(unittest.TestCase):
    def test_none_when_issue_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(db, [_item()], "26120")
            self.assertIsNone(query_app._dedup_message(db, "99999"))

    def test_skips_recent_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(db, [_item()], "26120")
            msg = query_app._dedup_message(db, "26120")
            self.assertIsNotNone(msg)
            self.assertIn("26120", msg)
            self.assertIn("不重复入库", msg)


class RunRefreshOddsTests(unittest.TestCase):
    def test_fresh_artifact_skips_without_cdp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS / 2)
            with mock.patch.object(query_app, "_ensure_cdp") as ensure, \
                 mock.patch("football_analysis.main._app_root", return_value=root):
                text = query_app.run_refresh_odds("26135", db_path=root / "x.sqlite")
            self.assertIn("跳过重抓", text)
            ensure.assert_not_called()

    def test_stale_without_cdp_returns_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS + 3)
            with mock.patch("football_analysis.main._app_root", return_value=root), \
                 mock.patch.object(query_app, "_ensure_cdp", return_value=query_app.CDP_HINT):
                text = query_app.run_refresh_odds("26135")
            self.assertEqual(text, query_app.CDP_HINT)

    def test_frozen_exe_proceeds_to_scrape(self):
        # 换成 stdlib CDP 客户端后 exe 也能抓：frozen 不再短路，照常走 CDP + 抓取。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS + 3)
            with mock.patch("football_analysis.main._app_root", return_value=root), \
                 mock.patch.object(query_app, "_ensure_cdp", return_value=None) as ensure, \
                 mock.patch.object(
                     query_app, "_run_odds_scraper", side_effect=RuntimeError("reached-scraper")
                 ):
                with mock.patch("sys.frozen", True, create=True):
                    with self.assertRaises(RuntimeError) as ctx:
                        query_app.run_refresh_odds("26135")
                self.assertEqual(str(ctx.exception), "reached-scraper")
                ensure.assert_called_once()
