import tempfile
import unittest
from unittest import mock
from datetime import datetime

from football_analysis import pipeline
from football_analysis.http import Fetcher, PageCache
from football_analysis.sfc import (
    ISSUE_URL,
    LIVE_ISSUE_URL,
    TRADE_URL,
    extract_issue,
    extract_match_score,
    parse_live_sfc_html,
)


def sfc_html(issue: str) -> str:
    return f"""
    <select>
      <option value="{issue}" selected="selected">{issue}</option>
    </select>
    <table id="vsTable">
      <tr class="bet-tb-tr"
          data-bjpl="1.80,3.40,4.00"
          data-pjgl="50,25,25"
          data-asian="0.90,半球/一球,0.96">
        <td class="td-no">1</td>
        <td class="td-evt">英超</td>
        <td class="td-team"><a class="team-l">Home</a> VS <a class="team-r">Away</a></td>
        <td class="td-data"><a href="shuju-123456.shtml">分析</a></td>
        <td class="td-endtime">09-10 00:45</td>
      </tr>
    </table>
    """


class FakeResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        return None


class RecordingFetcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.cache = PageCache(None)

    def fetch(
        self,
        url: str,
        *,
        kind: str = "html",
        cache_key: str | None = None,
        use_cache: bool = True,
    ) -> str:
        self.calls.append(
            {
                "url": url,
                "kind": kind,
                "cache_key": cache_key,
                "use_cache": use_cache,
            }
        )
        return "<matches/>" if "odds.xml" in url else "<html></html>"


class SfcIssueTests(unittest.TestCase):
    def test_extract_issue_uses_selected_option(self):
        self.assertEqual(extract_issue(sfc_html("26121")), "26121")

    def test_extract_issue_falls_back_to_expect_url(self):
        html = '<a href="https://trade.500.com/sfc/?expect=26120">26120</a>'
        self.assertEqual(extract_issue(html), "26120")

    def test_parse_live_sfc_html_uses_fixture_and_team_fields(self):
        html = """
        <title>【26166期足彩比分直播】</title>
        <table id="table_match">
          <tr fid="1428647" id="a1428647">
            <td>1</td><td class="ssbox_01" bgcolor="#990099">德甲</td>
            <td>第10轮</td><td>11-21 03:30</td><td>未</td>
            <td class="p_lr01"><span>[01]</span><a>拜仁</a></td>
            <td><a data-ao="两球半/三球" data-pb="两球半/三球">两球半/三球</a></td>
            <td class="p_lr01"><a>科隆</a><span>[15]</span></td>
            <td>-</td><td><a href="//odds.500.com/fenxi/shuju-1428647.shtml">析</a></td>
            <td><span>1.08</span><span>11.44</span><span>19.81</span></td>
          </tr>
        </table>
        """
        rows = parse_live_sfc_html(
            html,
            issue="26166",
            source_url=LIVE_ISSUE_URL.format(issue="26166"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_id"], "1428647")
        self.assertEqual(rows[0]["home_team"], "拜仁")
        self.assertEqual(rows[0]["away_team"], "科隆")
        self.assertEqual(rows[0]["league_color"], "#990099")
        self.assertEqual(rows[0]["europe_home"], 1.08)
        self.assertEqual(rows[0]["asian_line_text"], "两球半/三球")

    def test_collect_falls_back_to_live_issue_page(self):
        from football_analysis import pipeline

        live_html = """
        <table id="table_match">
          <tr fid="1428647"><td>1</td><td class="ssbox_01">德甲</td>
          <td>第10轮</td><td>11-21 03:30</td><td>未</td>
          <td class="p_lr01"><a>拜仁</a></td><td><a data-ao="两球半">两球半</a></td>
          <td class="p_lr01"><a>科隆</a></td><td>-</td><td><a href="/fenxi/shuju-1428647.shtml">析</a></td>
          <td><span>1.08</span><span>11.44</span><span>19.81</span></td></tr>
        </table>
        """

        class FallbackFetcher:
            def __init__(self):
                self.calls = []
                self.cache = PageCache(None)

            def fetch(self, url, **kwargs):
                self.calls.append(url)
                if url == ISSUE_URL.format(issue="26166"):
                    return '<table id="vsTable"></table>'
                return live_html

        fetcher = FallbackFetcher()
        rows, actual_issue = pipeline.collect_sfc_matches("26166", fetcher)
        self.assertEqual(actual_issue, "26166")
        self.assertEqual(rows[0]["fixture_id"], "1428647")
        self.assertEqual(fetcher.calls, [
            ISSUE_URL.format(issue="26166"),
            LIVE_ISSUE_URL.format(issue="26166"),
        ])


class AutoLatestTests(unittest.TestCase):
    def test_auto_latest_ignores_stale_sfc_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = PageCache(tmp)
            cache.save(f"sfc:{TRADE_URL}", sfc_html("26120").encode("utf-8"))
            fetcher = Fetcher(tmp)
            response = FakeResponse(sfc_html("26121").encode("utf-8"))
            with mock.patch("football_analysis.http.requests.get", return_value=response) as get:
                rows, issue = pipeline.collect_sfc_matches("", fetcher)
            self.assertEqual(issue, "26121")
            self.assertEqual(len(rows), 1)
            get.assert_called_once()

    def test_explicit_issue_still_reads_sfc_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = "26120"
            url = ISSUE_URL.format(issue=issue)
            cache = PageCache(tmp)
            cache.save(f"sfc:{url}", sfc_html(issue).encode("utf-8"))
            fetcher = Fetcher(tmp)
            with mock.patch("football_analysis.http.requests.get") as get:
                rows, actual_issue = pipeline.collect_sfc_matches(issue, fetcher)
            self.assertEqual(actual_issue, issue)
            self.assertEqual(len(rows), 1)
            get.assert_not_called()

    def _run_analyze(self, issue: str) -> RecordingFetcher:
        fetcher = RecordingFetcher()
        row = {"fixture_id": "123456", "match_no": "01"}
        with (
            mock.patch.object(
                pipeline,
                "collect_sfc_matches",
                return_value=([row], issue or "26121"),
            ),
             mock.patch.object(pipeline, "fetch_cantonese_matches", return_value={}),
            mock.patch.object(pipeline, "_attach_titan_h2h"),
            mock.patch.object(pipeline, "_attach_odds_movement"),
            mock.patch.object(pipeline, "_fetch_team_records", return_value={}),
            mock.patch.object(
                pipeline,
                "_build_single_analysis",
                return_value={"match_no": row["match_no"]},
            ),
        ):
            pipeline.analyze_issue(issue, fetcher=fetcher, max_workers=1)
        return fetcher

    def test_auto_mode_never_uses_cached_current_sources(self):
        fetcher = self._run_analyze("")
        current_calls = [
            call
            for call in fetcher.calls
            if call["cache_key"] in ("jczq:current", "odds_xml:current")
        ]
        self.assertEqual(len(current_calls), 2)
        self.assertTrue(all(not call["use_cache"] for call in current_calls))

    def test_explicit_issue_can_reuse_current_source_cache(self):
        fetcher = self._run_analyze("26120")
        current_calls = [
            call
            for call in fetcher.calls
            if call["cache_key"] in ("jczq:current", "odds_xml:current")
        ]
        self.assertEqual(len(current_calls), 2)
        self.assertTrue(all(call["use_cache"] for call in current_calls))




def _detail_page(score: str, kickoff: str) -> str:
    return (
        '<html><head><title>A VS B</title></head><body>'
        f'<span class="score">{score}</span>'
        f'<div class="h"><a>2026-2027 欧国联第4轮 {kickoff}</a></div>'
        "</body></html>"
    )


class ExtractMatchScoreTests(unittest.TestCase):
    """未开赛页面的比分牌默认是 0 - 0，不能当成终场比分。"""

    def _extract(self, html: str, now: datetime) -> str:
        with mock.patch("football_analysis.sfc.datetime") as dt:
            dt.now.return_value = now
            dt.strptime.side_effect = datetime.strptime
            return extract_match_score(html)

    def test_unstartedMatchScoreIsDiscarded(self):
        page = _detail_page("0 - 0", "2026-10-07 02:45")
        self.assertEqual(self._extract(page, datetime(2026, 10, 6, 22, 0)), "")

    def test_finishedMatchScoreIsKept(self):
        page = _detail_page("2 - 1", "2026-10-06 02:45")
        self.assertEqual(self._extract(page, datetime(2026, 10, 6, 22, 0)), "2 - 1")

    def test_zeroZeroAfterKickoffIsKept(self):
        page = _detail_page("0 - 0", "2026-10-06 02:45")
        self.assertEqual(self._extract(page, datetime(2026, 10, 6, 22, 0)), "0 - 0")

    def test_missingKickoffTimeFallsBackToScore(self):
        self.assertEqual(extract_match_score('<span class="score">1 - 0</span>'), "1 - 0")

    def test_malformedScoreIsEmpty(self):
        page = _detail_page("VS", "2026-10-06 02:45")
        self.assertEqual(self._extract(page, datetime(2026, 10, 6, 22, 0)), "")
if __name__ == "__main__":
    unittest.main()
