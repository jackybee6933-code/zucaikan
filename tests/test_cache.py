import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
