import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import main, pipeline
from football_analysis.pipeline import parse_live_issue_scores


def _score_page() -> str:
    return """
    <table id="table_match" class="bf_tablelist01">
      <tr class="revamp-match-row" id="a1473947">
        <td class="revamp-number">1</td>
        <td><a href="/team/1608/">浦项制铁</a></td>
        <td class="revamp-score"><a class="clt1">3</a><a>让球</a><a class="clt3">3</a></td>
        <td><a href="/team/752/">大阪钢巴</a></td>
        <td class="revamp-status">完</td>
      </tr>
      <tr class="revamp-match-row" id="a1473948">
        <td class="revamp-number">2</td>
        <td><a href="/team/1/">主队</a></td>
        <td class="revamp-score"><a class="clt1">0</a><a>让球</a><a class="clt3">0</a></td>
        <td><a href="/team/2/">客队</a></td>
        <td class="revamp-status">未</td>
      </tr>
      <tr class="revamp-match-row" id="a1473949">
        <td class="revamp-number">3</td>
        <td><a href="/team/3/">主队乙</a></td>
        <td class="revamp-score"><a class="clt1">2</a><a>让球</a><a class="clt3">1</a></td>
        <td class="revamp-half">1 - 0</td>
        <td><a href="/team/4/">客队乙</a></td>
        <td class="revamp-status">完场</td>
      </tr>
    </table>
    """


class LiveIssueScoreParserTests(unittest.TestCase):
    def test_extracts_only_completed_full_time_scores_by_fixture_id(self):
        self.assertEqual(
            parse_live_issue_scores(_score_page()),
            {"1473947": "3 - 3", "1473949": "2 - 1"},
        )

    def test_missing_table_returns_no_scores(self):
        self.assertEqual(parse_live_issue_scores("<html></html>"), {})


class RefreshLiveIssueScoreTests(unittest.TestCase):
    def test_fetch_waits_for_all_expected_rows(self):
        from football_analysis import cdp, cdp_profile

        class FakePage:
            def __init__(self):
                self.calls = 0

            def goto(self, *args, **kwargs):
                pass

            def evaluate(self, _script):
                self.calls += 1
                return ("", 0) if self.calls == 1 else ("<table></table>", 14)

            def close(self):
                pass

        page = FakePage()
        with mock.patch.object(cdp, "CdpPage", return_value=page), \
             mock.patch.object(cdp_profile, "ensure_cdp_chrome", return_value=None), \
             mock.patch.object(cdp_profile, "shutdown_cdp_chrome") as shutdown:
            html = main._fetch_live_issue_score_html("26158", 14)

        self.assertEqual(html, "<table></table>")
        self.assertEqual(page.calls, 2)
        shutdown.assert_not_called()

    def test_refresh_uses_issue_page_and_updates_only_matching_completed_rows(self):
        matches = [
            {"match_no": "1", "fixture_id": "1473947"},
            {"match_no": "2", "fixture_id": "1473948"},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(pipeline, "collect_sfc_matches", return_value=(matches, "26158")), \
             mock.patch.object(main, "_fetch_live_issue_score_html", return_value=_score_page()), \
             mock.patch.object(main, "update_match_scores", return_value=1) as update:
            result = main._refresh_scores(Path(tmp) / "db.sqlite", "26158", Path(tmp) / "cache")

        self.assertEqual(result["filled"], 1)
        self.assertEqual(result["updated"], 1)
        update.assert_called_once_with(Path(tmp) / "db.sqlite", "26158", {"1": "3 - 3"})
