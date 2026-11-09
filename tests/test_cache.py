import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import main
from football_analysis.main import build_parser, clean_cache


class CacheCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name: str, content: str = "x", age_hours: float = 0) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        timestamp = self.now - age_hours * 3600
        os.utime(path, (timestamp, timestamp))
        return path

    def test_keeps_newest_odds_and_removes_disposable_files(self):
        self.touch("odds_pages_26145.json", "old", age_hours=100)
        self.touch("odds_pages_26146.json", "middle", age_hours=50)
        self.touch("odds_pages_26147.json", "new", age_hours=1)
        self.touch("odds_pages_bad.json")
        self.touch("page.raw", "raw")
        self.touch(".odds_partial_26144.json", "{}", age_hours=7)
        self.touch(".odds_partial_26147.json", "{}", age_hours=1)
        self.touch(
            ".ai_scrape_progress",
            json.dumps({"phase": "llm", "total": 2, "done": 1}),
        )
        self.touch("notes.txt", "keep")

        result = clean_cache(self.root, keep=2, now=self.now)

        self.assertEqual(result["deleted_files"], 3)
        self.assertEqual(
            set(result["kept_odds"]),
            {"odds_pages_26146.json", "odds_pages_26147.json"},
        )
        self.assertFalse((self.root / "odds_pages_26145.json").exists())
        self.assertFalse((self.root / "page.raw").exists())
        self.assertFalse((self.root / ".odds_partial_26144.json").exists())
        self.assertTrue((self.root / "odds_pages_bad.json").exists())
        self.assertTrue((self.root / ".odds_partial_26147.json").exists())
        self.assertTrue((self.root / ".ai_scrape_progress").exists())
        self.assertTrue((self.root / "notes.txt").exists())
        self.assertIn(".ai_scrape_progress: 任务进行中", result["skipped"])
        self.assertIn(".odds_partial_26147.json: 未超过 6h", result["skipped"])

    def test_completed_progress_is_removed(self):
        self.touch(
            ".ai_scrape_progress",
            json.dumps({"phase": "llm", "total": 2, "done": 2}),
        )
        result = clean_cache(self.root, keep=0, now=self.now)
        self.assertEqual(result["deleted_files"], 1)
        self.assertFalse((self.root / ".ai_scrape_progress").exists())

    def test_dry_run_does_not_delete(self):
        self.touch("page.raw", "raw")
        result = clean_cache(self.root, keep=0, dry_run=True, now=self.now)
        self.assertEqual(result["deleted_files"], 1)
        self.assertTrue((self.root / "page.raw").exists())

    def test_invalid_keep_is_rejected(self):
        with self.assertRaises(ValueError):
            clean_cache(self.root, keep=-1)

    def test_cache_parser_has_cleanup_options(self):
        args = build_parser().parse_args(
            ["cache", "--cache-dir", "tmp-cache", "--clean", "--keep", "5", "--dry-run"]
        )
        self.assertEqual(args.command, "cache")
        self.assertTrue(args.clean)
        self.assertEqual(args.keep, 5)
        self.assertTrue(args.dry_run)

    def test_automatic_cleanup_only_removes_old_raw_pages(self):
        old = self.touch("a" * 20 + ".raw", "old", age_hours=7 * 24)
        recent = self.touch("b" * 20 + ".raw", age_hours=7 * 24 - 1)
        future = self.touch("c" * 20 + ".raw", age_hours=-1)
        preserved = [
            self.touch("notes.raw", age_hours=200),
            self.touch("odds_pages_26135.json", age_hours=200),
            self.touch("odds_pages_26135.meta.json", age_hours=200),
            self.touch("live_links_26135.json", age_hours=200),
            self.touch(".odds_partial_26135.json", age_hours=200),
            self.touch(".ai_scrape_progress", age_hours=200),
            self.touch(".update_manifest.json", age_hours=200),
        ]

        result = clean_cache(self.root, automatic=True, now=self.now)

        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["deleted_bytes"], 3)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(future.exists())
        self.assertTrue(all(path.exists() for path in preserved))

    def test_automatic_dry_run_preserves_files(self):
        old = self.touch("a" * 20 + ".raw", age_hours=200)
        result = clean_cache(self.root, automatic=True, dry_run=True, now=self.now)
        self.assertEqual(result["deleted_files"], 1)
        self.assertTrue(old.exists())

    def test_automatic_cleanup_skips_locked_files(self):
        locked = self.touch("a" * 20 + ".raw", age_hours=200)
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == locked:
                raise PermissionError("locked")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", unlink):
            result = clean_cache(self.root, automatic=True, now=self.now)
        self.assertEqual(result["deleted_files"], 0)
        self.assertTrue(locked.exists())
        self.assertTrue(result["skipped"])

    def test_auto_cleanup_runs_at_most_once_per_day_across_restarts(self):
        first = self.touch("a" * 20 + ".raw", age_hours=200)
        with mock.patch.object(main.time, "time", return_value=self.now):
            main.auto_clean_cache(self.root)
        self.assertFalse(first.exists())
        second = self.touch("b" * 20 + ".raw", age_hours=200)
        with mock.patch.object(main.time, "time", return_value=self.now + 86399):
            main.auto_clean_cache(self.root)
        self.assertTrue(second.exists())
        with mock.patch.object(main.time, "time", return_value=self.now + 86400):
            main.auto_clean_cache(self.root)
        self.assertFalse(second.exists())

    def test_invalid_auto_cleanup_marker_does_not_block_cleanup(self):
        self.touch(".last_auto_cleanup", "broken")
        old = self.touch("a" * 20 + ".raw", age_hours=200)
        with mock.patch.object(main.time, "time", return_value=self.now):
            main.auto_clean_cache(self.root)
        self.assertFalse(old.exists())

    def test_auto_cleanup_failure_does_not_stop_startup(self):
        with mock.patch.object(main, "clean_cache", side_effect=OSError("denied")):
            main.auto_clean_cache(self.root)
        self.assertFalse((self.root / ".last_auto_cleanup").exists())

    def test_query_startup_runs_auto_cleanup(self):
        args = build_parser().parse_args(["query", "--serve", "--no-open"])
        with (
            mock.patch.object(main, "_default_cache_dir", return_value=self.root),
            mock.patch.object(main, "auto_clean_cache") as cleanup,
            mock.patch.object(main, "serve_query"),
        ):
            self.assertEqual(main._run_query(args), 0)
        cleanup.assert_called_once_with(self.root)

    def test_default_startup_runs_cleanup_before_fetching(self):
        with (
            mock.patch.object(main, "_app_root", return_value=self.root),
            mock.patch.object(main, "auto_clean_cache") as cleanup,
            mock.patch.object(main, "_determine_latest_issue", return_value="26162") as fetch,
            mock.patch.object(main, "_refresh_previous_issue_scores"),
            mock.patch.object(main, "_run_analyze", return_value=3),
        ):
            order = mock.Mock()
            order.attach_mock(cleanup, "cleanup")
            order.attach_mock(fetch, "fetch")
            self.assertEqual(main._run_analyze_then_serve(build_parser()), 3)
        self.assertEqual(order.mock_calls[:2], [
            mock.call.cleanup(self.root / "cache"),
            mock.call.fetch(self.root / "cache"),
        ])

    def test_auto_cleanup_skips_missing_cache_directory(self):
        missing = self.root / "missing"
        main.auto_clean_cache(missing)
        self.assertFalse(missing.exists())

    def test_auto_cleanup_does_not_follow_symlinks(self):
        target = self.root / "outside"
        target.mkdir()
        page = target / ("a" * 20 + ".raw")
        page.write_text("keep", encoding="utf-8")
        os.utime(page, (self.now - 200 * 3600,) * 2)
        link = self.root / ("b" * 20 + ".raw")
        try:
            link.symlink_to(page)
        except OSError:
            self.skipTest("Symlinks require permission on Windows")
        clean_cache(self.root, automatic=True, now=self.now)
        self.assertTrue(page.exists())
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
