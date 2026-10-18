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
from football_analysis.odds_pages import has_odds_rows


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


def _artifact(
    cache_dir: Path,
    issue: str,
    age_hours: float,
    *,
    with_rows: bool = True,
    all_kinds: bool = False,
) -> Path:
    """默认写一份带可信 yazhi 公司行的产物；残缺产物（AI 分析合并后只有
    shuju 键、或 yazhi 为 empty 零公司行）传 with_rows=False 模拟。
    all_kinds=True 时额外补上 daxiao/shuju，凑齐 3 种页种。"""
    payload = {
        "9|yazhi": {
            "status": "ok" if with_rows else "empty",
            "rows": [[293, "0.87", "平手", "0.95", "0.90", "平手", "0.90"]] if with_rows else [],
        }
    }
    if all_kinds:
        payload["9|daxiao"] = {"status": "ok", "rows": [[293, "0.90", "2.5", "0.90", "0.90", "2.5", "0.90"]]}
        payload["9|shuju"] = {"status": "ok", "text": "资料"}
    path = cache_dir / f"odds_pages_{issue}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
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
    def test_fresh_only_shuju_missing_skips_without_scraping(self):
        """yazhi/daxiao 都有公司行、只缺 shuju：直接跳过，shuju 留给「AI 分析」。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS / 2, all_kinds=True).write_text(
                json.dumps(
                    {
                        "9|yazhi": {
                            "status": "ok",
                            "rows": [[293, "0.87", "平手", "0.95", "0.90", "平手", "0.90"]],
                        },
                        "9|daxiao": {
                            "status": "ok",
                            "rows": [[293, "0.90", "2.5", "0.90", "0.90", "2.5", "0.90"]],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("football_analysis.main._app_root", return_value=root), \
                 mock.patch.object(query_app, "_ensure_cdp", return_value=None), \
                 mock.patch.object(query_app, "_run_odds_scraper", return_value=None) as scraper, \
                 mock.patch("football_analysis.cdp_profile.shutdown_cdp_chrome"):
                text = query_app.run_refresh_odds("26135", db_path=root / "x.sqlite")
            self.assertIn("跳过重抓", text)
            scraper.assert_not_called()

    def test_fresh_full_artifact_skips_without_cdp(self):
        """3 种页种齐全的新鲜产物：盘口和 shuju 都在，直接跳过。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS / 2, all_kinds=True)
            with mock.patch.object(query_app, "_ensure_cdp") as ensure, \
                 mock.patch("football_analysis.main._app_root", return_value=root):
                text = query_app.run_refresh_odds("26135", db_path=root / "x.sqlite")
            self.assertIn("跳过重抓", text)
            ensure.assert_not_called()

    def test_fresh_but_incomplete_artifact_forces_rescrape(self):
        """残缺产物（AI 分析合并后只有 shuju 键、yazhi 零公司行）mtime 很新，
        也不能跳过：内容检查不通过就得重新抓全量，否则盘口永远补不上。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS / 2, with_rows=False)
            with mock.patch("football_analysis.main._app_root", return_value=root), \
                 mock.patch.object(query_app, "_ensure_cdp", return_value=None), \
                 mock.patch.object(
                     query_app, "_run_odds_scraper", side_effect=RuntimeError("reached-scraper")
                 ), \
                 mock.patch("football_analysis.cdp_profile.shutdown_cdp_chrome"):
                with self.assertRaises(RuntimeError) as ctx:
                    query_app.run_refresh_odds("26135")
                self.assertEqual(str(ctx.exception), "reached-scraper")

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
                 mock.patch.object(query_app, "_ensure_cdp", return_value=None), \
                 mock.patch.object(
                     query_app, "_run_odds_scraper", side_effect=RuntimeError("reached-scraper")
                 ), \
                 mock.patch("football_analysis.cdp_profile.shutdown_cdp_chrome"):
                with mock.patch("sys.frozen", True, create=True):
                    with self.assertRaises(RuntimeError) as ctx:
                        query_app.run_refresh_odds("26135")
                self.assertEqual(str(ctx.exception), "reached-scraper")

class RefreshOddsOnlyMissingKindsTests(unittest.TestCase):
    """「刷新盘口分歧」只在产物缺页种时才真正开浏览器抓，且只补缺的：
    全量产物 → 跳过；缺 shuju → 只补抓 shuju；缺 yazhi → 只补抓 yazhi。"""

    def _write_artifact(self, cache: Path, payload: dict, age_hours: float) -> None:
        path = cache / "odds_pages_26143.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        stamp = time.time() - age_hours * 3600
        os.utime(path, (stamp, stamp))

    def _run(self, payload: dict, age_hours: float):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            self._write_artifact(cache, payload, age_hours)
            with mock.patch("football_analysis.main._app_root", return_value=root), \
                 mock.patch.object(query_app, "_ensure_cdp", return_value=None) as ensure, \
                 mock.patch.object(query_app, "_run_odds_scraper") as scraper, \
                 mock.patch("football_analysis.cdp_profile.shutdown_cdp_chrome"):
                query_app.run_refresh_odds("26143", db_path=root / "x.sqlite")
            return ensure, scraper

    def test_fully_covers_all_kinds_skips_scrape(self):
        full = {
            "9|yazhi": {"status": "ok", "rows": [[293, "0.90", "平手", "0.95", "0.90", "平手", "0.90"]]},
            "9|daxiao": {"status": "ok", "rows": [[293, "0.90", "2.5", "0.90", "0.90", "2.5", "0.90"]]},
            "9|shuju": {"status": "ok", "text": "资料"},
        }
        ensure, scraper = self._run(full, PAGES_STALE_HOURS / 2)
        # 全量产物：既没开浏览器，也没抓
        ensure.assert_not_called()
        scraper.assert_not_called()

    def test_only_shuju_missing_skips_scrape(self):
        payload = {
            "9|yazhi": {"status": "ok", "rows": [[293, "0.90", "平手", "0.95", "0.90", "平手", "0.90"]]},
            "9|daxiao": {"status": "ok", "rows": [[293, "0.90", "2.5", "0.90", "0.90", "2.5", "0.90"]]},
        }
        ensure, scraper = self._run(payload, PAGES_STALE_HOURS / 2)
        ensure.assert_not_called()
        scraper.assert_not_called()

    def test_failed_yazhi_scrapes_just_yazhi(self):
        payload = {
            "9|yazhi": {"status": "timeout", "rows": []},
            "9|daxiao": {"status": "ok", "rows": [[293, "0.90", "2.5", "0.90", "0.90", "2.5", "0.90"]]},
            "9|shuju": {"status": "ok", "text": "资料"},
        }
        ensure, scraper = self._run(payload, PAGES_STALE_HOURS / 2)
        ensure.assert_called()
        scraper.assert_called_once()
        self.assertEqual(scraper.call_args.kwargs.get("kinds"), ["yazhi"])

    def test_empty_upstream_is_not_missing_skips_scrape(self):
        """yazhi 是 empty（上游确实没开盘）：重抓也拿不到，跳过。"""
        payload = {
            "9|yazhi": {"status": "empty", "rows": []},
            "9|daxiao": {"status": "empty", "rows": []},
            "9|shuju": {"status": "ok", "text": "资料"},
        }
        ensure, scraper = self._run(payload, PAGES_STALE_HOURS / 2)
        scraper.assert_not_called()


class StaleProgressSnapshotTests(unittest.TestCase):
    """进度快照只按 issue 匹配：上一轮任务留下的 14/14 不能被本轮读成 100%。"""

    def test_refresh_clears_previous_snapshot_before_deciding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            _artifact(cache, "26135", PAGES_STALE_HOURS / 2, all_kinds=True)
            stale = cache / ".ai_scrape_progress"
            stale.write_text(
                json.dumps({"issue": "26135", "phase": "llm", "total": 14, "done": 14}),
                encoding="utf-8",
            )
            with mock.patch("football_analysis.main._app_root", return_value=root):
                text = query_app.run_refresh_odds("26135")
            self.assertIn("跳过重抓", text)
            self.assertFalse(stale.exists())


class HasOddsRowsTests(unittest.TestCase):
    """has_odds_rows 只看产物内容（有无可信 yazhi 公司行），不看 mtime。"""

    def _write(self, tmp: Path, payload: object) -> Path:
        cache = tmp / "cache"
        cache.mkdir()
        path = cache / "odds_pages_26135.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return cache

    def test_full_artifact_with_rows_is_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._write(
                Path(tmp),
                {
                    "9|yazhi": {"status": "ok", "rows": [[293, "0.87", "平手", "0.95", "0.90", "平手", "0.90"]]},
                    "9|shuju": {"status": "ok", "text": "数据页"},
                },
            )
            self.assertTrue(has_odds_rows(cache, "26135"))

    def test_shuju_only_artifact_is_false(self):
        """AI 分析补抓合并后只剩 |shuju 键的残缺产物：不算有盘口。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._write(Path(tmp), {"9|shuju": {"status": "ok", "text": "数据页"}})
            self.assertFalse(has_odds_rows(cache, "26135"))

    def test_empty_yazhi_is_false(self):
        """yazhi 页是 empty（上游确实没开盘）：有可信抓取结果但零盘口，
        也算 False（重抓只会再拿一次同样结果，由 _ensure_cdp 流程正常兜底）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._write(Path(tmp), {"9|yazhi": {"status": "empty", "rows": []}})
            self.assertFalse(has_odds_rows(cache, "26135"))

    def test_failed_yazhi_is_false(self):
        """yazhi 页是 timeout/error：不算有盘口，必须重抓。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._write(
                Path(tmp), {"9|yazhi": {"status": "timeout", "rows": []}}
            )
            self.assertFalse(has_odds_rows(cache, "26135"))

    def test_missing_file_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            self.assertFalse(has_odds_rows(cache, "26135"))

    def test_none_cache_root_is_false(self):
        self.assertFalse(has_odds_rows(None, "26135"))
