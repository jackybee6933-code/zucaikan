import json
import os
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
    """模拟抓取脚本：返回 0 并顺手写出产物。"""
    _artifact(cache_dir, 0.0)
    return 0


def _patch_loader(calls, rc):
    """把 importlib 加载抓取脚本替换成直接返回伪造模块。"""
    class _Mod:
        def main(self, argv):
            calls.append(argv)
            return rc

    def _fake_spec(name, path):
        calls.append(path)
        return mock.MagicMock(
            loader=mock.MagicMock(exec_module=lambda mod: None),
        )

    return _fake_spec, _Mod()


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = str(Path(self.tmp.name) / "cache")
        Path(self.cache).mkdir(parents=True)
        # 抓取脚本必须存在，_run_refresh 才会走到加载那一步
        tools = Path(self.tmp.name) / "tools"
        tools.mkdir()
        (tools / "scrape_odds_pages.py").write_text("# stub\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _run_with_scrape(self, args, rc=0, fake=None):
        """跑 _run_refresh，把抓取脚本替换成 fake（默认写产物返回 rc）。"""
        calls = []
        if fake is None:
            fake = lambda argv: _fake_scrape(self.cache)

        class _Mod:
            def main(self, argv):
                calls.append(argv)
                return fake(argv)

        def _fake_spec(name, path):
            return mock.MagicMock(loader=mock.MagicMock(exec_module=lambda mod: None))

        with mock.patch("urllib.request.urlopen", _ctx_ok()), \
             mock.patch("importlib.util.spec_from_file_location", side_effect=_fake_spec), \
             mock.patch("importlib.util.module_from_spec", return_value=_Mod()):
            rc_out = main._run_refresh(args)
        return rc_out, calls

    def test_fresh_artifact_skips_without_touching_browser(self):
        _artifact(self.cache, PAGES_STALE_HOURS / 2)
        with mock.patch("importlib.util.spec_from_file_location") as spec:
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 0)
        spec.assert_not_called()

    def test_force_regrabs_even_when_fresh(self):
        _artifact(self.cache, 0.1)
        rc, calls = self._run_with_scrape(_args(self.cache, force=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_stale_without_cdp_explains_and_stops(self):
        _artifact(self.cache, PAGES_STALE_HOURS + 3)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            rc = main._run_refresh(_args(self.cache))
        self.assertEqual(rc, 1)

    def test_missing_artifact_triggers_scrape(self):
        rc, calls = self._run_with_scrape(_args(self.cache))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_scraper_failure_propagates(self):
        _artifact(self.cache, PAGES_STALE_HOURS + 3)
        rc, _ = self._run_with_scrape(_args(self.cache), rc=3, fake=lambda argv: 3)
        self.assertEqual(rc, 3)

    def test_passes_issue_cache_and_cdp_through(self):
        rc, calls = self._run_with_scrape(_args(self.cache, issue="26140", delay=12.0))
        argv = calls[0]
        self.assertIn("--issue", argv)
        self.assertEqual(argv[argv.index("--issue") + 1], "26140")
        self.assertEqual(argv[argv.index("--delay") + 1], "12.0")
        self.assertEqual(argv[argv.index("--cdp") + 1], "http://127.0.0.1:9222")
        self.assertEqual(argv[argv.index("--cache-dir") + 1], self.cache)

    def test_no_artifact_after_success_is_an_error(self):
        rc, _ = self._run_with_scrape(_args(self.cache), fake=lambda argv: 0)
        self.assertEqual(rc, 1)

    def test_default_cache_dir_follows_app_root(self):
        """不传 --cache-dir 时要落到 app_root，否则手动跑 exe refresh 会找不到产物。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            (root / "cache").mkdir(parents=True)
            _artifact(str(root / "cache"), 0.5)
            with mock.patch.object(main, "_app_root", return_value=root), \
                 mock.patch("importlib.util.spec_from_file_location") as spec:
                rc = main._run_refresh(_args(cache_dir=""))
            self.assertEqual(rc, 0)
            spec.assert_not_called()   # 新鲜产物不该去抓

    def test_empty_db_without_issue_is_an_error(self):
        with mock.patch.object(main, "list_issues", return_value=[]):
            rc = main._run_refresh(_args(self.cache, issue=""))
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
