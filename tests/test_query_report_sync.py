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
        self.assertEqual(src.count('p.phase === "verification_required"'), 2)
        self.assertEqual(src.count('p.phase === "verification_resolved"'), 2)
        self.assertIn("请在 Chrome 中完成人工验证", src)

    def test_single_render_query_html(self):
        src = Path(query_report.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("def render_query_html("), 1)

    def test_fetch_issue_is_restored_after_successful_action_reload(self):
        html = query_report._FETCH_BLOCK
        self.assertIn('const fetchIssueStorageKey = "fetch-issue-input"', html)
        self.assertIn("fetchIssueInput.value = savedIssue", html)
        self.assertIn("sessionStorage.removeItem(fetchIssueStorageKey)", html)
        self.assertEqual(html.count("reloadQueryWithIssue(issue)"), 3)

    def test_ai_analysis_progress_resumes_after_query_navigation(self):
        html = query_report._FETCH_BLOCK
        self.assertIn('fetch("/ai-analyze-status").then((r) => r.json())', html)
        self.assertIn("if (!s.running) return;", html)
        self.assertIn("_pollAiAnalyze(\"\")", html)
        self.assertIn("if (refreshIssue && s.result && !s.error)", html)


if __name__ == "__main__":
    unittest.main()
