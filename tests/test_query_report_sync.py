"""query_report.py 里 _FETCH_BLOCK 和 render_query_html 各只有一份（2026-10-17 去重）。
改前端时只需改这一处，无需多副本同步。"""
import unittest
from pathlib import Path

import football_analysis.query_report as query_report


class FetchBlockSyncTests(unittest.TestCase):
    def test_single_fetch_block(self):
        src = Path(query_report.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("_FETCH_BLOCK ="), 1)
        self.assertEqual(src.count('id="refresh-odds-progress-bar"'), 1)
        self.assertEqual(src.count('getElementById("refresh-odds-progress-bar")'), 1)
        self.assertEqual(src.count("function _renderRefreshProgress(s)"), 1)

    def test_single_render_query_html(self):
        src = Path(query_report.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("def render_query_html("), 1)


if __name__ == "__main__":
    unittest.main()
