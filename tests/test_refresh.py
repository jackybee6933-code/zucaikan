import json
import os
import subprocess
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from football_analysis import main
from football_analysis.odds_pages import PAGES_STALE_HOURS


def _args(cache_dir: str, **over) -> Namespace:
    base = dict(
        issue="26135", cache_dir=cache_dir, delay=8.0,
        cdp="http://127.0.0.1:9222", force=False, db_path=None,
    )
    base.update(over)
    return Namespace(**base)


def _artifact(cache_dir: str, age_hours: float) -> Path:
    path = Path(cache_dir) / "odds_pages_26135.json"
    path.write_text(json.dumps({"9|yazhi": {"status": "empty", "rows": []}}), encoding="utf-8")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def _ctx_ok():
    """模拟 CDP 可达。"""
    return mock.MagicMock(__enter__=mock.MagicMock(), __exit__=mock.MagicMock(return_value=False))


def _fake_scrape(cache_dir: str):
    """模拟抓取脚本：返回 0 并真正写出产物。"""
    _artifact(cache_dir, 0.0)
    return 0


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = str(Path(self.tmp.name) / "cache")
        Path(self.cache).mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def test_fresh_artifact_skips_without_touching_browser(self):
        _artifact(self.cache, PAGES_STALE_HOURS / 2)
        with mock.patch.object(subprocess, "call") as call:
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 0)
        call.assert_not_called()

    def test_force_regrabs_even_when_fresh(self):
        _artifact(self.cache, 0.1)
        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch.object(subprocess, "call",
                               side_effect=lambda *a, **k: _fake_scrape(self.cache)) as call:
            rc = main._run_refresh(_args(self.cache, force=True))
        self.assertEqual(rc, 0)
        call.assert_called_once()

    def test_stale_without_cdp_explains_and_stops(self):
        _artifact(self.cache, PAGES_STALE_HOURS + 3)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 1)

    def test_missing_artifact_triggers_scrape(self):
        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch.object(subprocess, "call",
                                side_effect=lambda *a, **k: _fake_scrape(self.cache)) as call:
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 0)
        call.assert_called_once()

    def test_scraper_failure_propagates(self):
        _artifact(self.cache, PAGES_STALE_HOURS + 3)
        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch.object(subprocess, "call", return_value=3):
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 3)

    def test_passes_issue_cache_and_cdp_through(self):
        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch.object(subprocess, "call",
                               side_effect=lambda *a, **k: _fake_scrape(self.cache)) as call:
            main._run_refresh(_args(self.cache, issue="26140", delay=12.0))
        cmd = call.call_args.args[0]
        self.assertIn("--issue", cmd)
        self.assertEqual(cmd[cmd.index("--issue") + 1], "26140")
        self.assertEqual(cmd[cmd.index("--delay") + 1], "12.0")
        self.assertEqual(cmd[cmd.index("--cdp") + 1], "http://127.0.0.1:9222")
        self.assertEqual(cmd[cmd.index("--cache-dir") + 1], self.cache)

    def test_no_artifact_after_success_is_an_error(self):
        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch.object(subprocess, "call", return_value=0):
            # 抓取脚本“成功”但没落盘
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 1)

    def test_empty_db_without_issue_is_an_error(self):
        with mock.patch.object(main, "list_issues", return_value=[]):
            rc = main._run_refresh(_args(self.cache, issue=""))
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
