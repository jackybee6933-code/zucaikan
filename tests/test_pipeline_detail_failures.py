import unittest
from unittest import mock

from football_analysis import pipeline


class DetailPageFailureTests(unittest.TestCase):
    def test_failed_live_detail_does_not_abort_issue_analysis(self):
        row = {
            "issue": "26158",
            "match_no": "1",
            "fixture_id": "1425920",
            "home_team": "主队",
            "away_team": "客队",
            "league": "联赛",
            "final_score": "",
        }
        fetcher = mock.MagicMock()
        fetcher.cache.root = None

        def fetch(url, **kwargs):
            if "live.500.com/detail.php?fid=1425920" in url:
                raise RuntimeError(
                    "抓取失败 https://live.500.com/detail.php?fid=1425920&r=1: "
                    "567 Server Error: Unknown Status"
                )
            return "<html></html>"

        fetcher.fetch.side_effect = fetch
        with (
            mock.patch.object(pipeline, "collect_sfc_matches", return_value=([row], "26158")),
            mock.patch.object(pipeline, "parse_jczq_html", return_value={}),
            mock.patch.object(pipeline, "parse_odds_xml", return_value={}),
            mock.patch.object(pipeline, "load_odds_pages_raw", return_value={}),
            mock.patch.object(pipeline, "_attach_titan_h2h"),
            mock.patch.object(pipeline, "_attach_odds_movement"),
            mock.patch.object(pipeline, "_fetch_team_records", return_value={}),
            mock.patch.object(
                pipeline,
                "_build_single_analysis",
                side_effect=lambda match, *_args, **_kwargs: {
                    "match_no": match["match_no"],
                    "warnings": match["warnings"],
                },
            ),
        ):
            analyses, issue, _ = pipeline.analyze_issue("26158", fetcher=fetcher)

        self.assertEqual(issue, "26158")
        self.assertEqual(len(analyses), 1)
        self.assertTrue(
            any("详情页抓取失败" in warning for warning in analyses[0]["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
