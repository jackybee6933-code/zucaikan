"""P1 tests: staleness detection for the odds_pages artifact."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from football_analysis.odds_pages import (
    PAGES_STALE_HOURS,
    pages_age_hours,
    pages_kind_age_hours,
)

ISSUE = "26135"
FILENAME = f"odds_pages_{ISSUE}.json"


class PagesAgeTests(unittest.TestCase):
    def _cache_with_age(self, age_seconds: float) -> tempfile.TemporaryDirectory:
        """建一个含产物、mtime 设为 age_seconds 前的临时 cache 目录。"""
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / FILENAME
        path.write_text("{}", encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return tmp

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pages_age_hours(tmp, ISSUE))

    def test_no_cache_root_returns_none(self):
        self.assertIsNone(pages_age_hours(None, ISSUE))
        self.assertIsNone(pages_age_hours("", ISSUE))

    def test_fresh_file_is_near_zero(self):
        with self._cache_with_age(60) as tmp:
            age = pages_age_hours(tmp, ISSUE)
            self.assertIsNotNone(age)
            self.assertLess(age, 1.0)

    def test_old_file_reports_age_beyond_threshold(self):
        with self._cache_with_age(PAGES_STALE_HOURS * 3600 * 3) as tmp:
            self.assertGreater(pages_age_hours(tmp, ISSUE), PAGES_STALE_HOURS)

    def test_future_mtime_is_clamped_to_zero(self):
        with self._cache_with_age(-3600) as tmp:
            self.assertEqual(pages_age_hours(tmp, ISSUE), 0.0)

    def test_threshold_is_sane(self):
        self.assertGreater(PAGES_STALE_HOURS, 0)
        self.assertLessEqual(PAGES_STALE_HOURS, 24)

    def test_kind_age_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / f"odds_pages_{ISSUE}.meta.json"
            meta.write_text(
                '{"yazhi": 1000000, "shuju": 999900}', encoding="utf-8"
            )
            with mock.patch("football_analysis.odds_pages.time.time", return_value=1003600):
                yazhi = pages_kind_age_hours(tmp, ISSUE, "yazhi")
                shuju = pages_kind_age_hours(tmp, ISSUE, "shuju")
            self.assertAlmostEqual(yazhi, 1.0)
            self.assertAlmostEqual(shuju, 1.0277777778)
            self.assertIsNone(pages_kind_age_hours(tmp, ISSUE, "daxiao"))
