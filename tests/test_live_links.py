import unittest
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from football_analysis.live_links import (
    HOME_URL,
    _collect_crzb_links,
    _collect_links,
    _fetch,
    _fetch_crzb_json,
    load_cached_live_links,
    parse_league_links,
    parse_match_links,
    live_links,
)
from football_analysis.query_report import render_query_html


class LiveLinkParserTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "issue": "26135",
            "match_no": "1",
            "home_team": "拜仁慕尼黑",
            "away_team": "美因茨",
        }]

    def test_force_refresh_bypasses_cached_links(self):
        rows = [{"match_no": "1", "home_team": "home", "away_team": "away"}]
        fresh = {"1": "https://example.test/new"}
        with (
            patch("football_analysis.live_links._cache_links", {"26135": {"1": "https://example.test/old"}}),
            patch("football_analysis.live_links._cache_expires", float("inf")),
            patch("football_analysis.live_links._collect_links", return_value=fresh) as collect,
        ):
            self.assertEqual(live_links(rows, "26135", force=True), {
                ("26135", "1"): "https://example.test/new"
            })
        collect.assert_called_once_with(rows, "26135")

    def test_fresh_disk_cache_is_reused(self):
        now = 1_800_000_000
        with tempfile.TemporaryDirectory() as cache_dir:
            path = Path(cache_dir) / "live_links_26135.json"
            path.write_text(json.dumps({
                "issue": "26135",
                "fetched_at": now - 3599,
                "links": {"1": "https://example.test/live"},
            }), encoding="utf-8")
            with (
                patch("football_analysis.live_links.time.time", return_value=now),
                patch("football_analysis.live_links._collect_links") as collect,
            ):
                self.assertEqual(
                    live_links(self.rows, "26135", cache_dir=cache_dir),
                    {("26135", "1"): "https://example.test/live"},
                )
            collect.assert_not_called()

    def test_expired_cache_is_refreshed_and_persisted(self):
        now = 1_800_000_000
        fresh = {"1": "https://example.test/new"}
        with tempfile.TemporaryDirectory() as cache_dir:
            path = Path(cache_dir) / "live_links_26135.json"
            path.write_text(json.dumps({
                "issue": "26135",
                "fetched_at": now - 3600,
                "links": {"1": "https://example.test/old"},
            }), encoding="utf-8")
            with (
                patch("football_analysis.live_links.time.time", return_value=now),
                patch(
                    "football_analysis.live_links._collect_links",
                    return_value=fresh,
                ) as collect,
            ):
                result = live_links(self.rows, "26135", cache_dir=cache_dir)
                cached, expired, exists = load_cached_live_links(
                    self.rows, "26135", cache_dir
                )

            self.assertEqual(result, {("26135", "1"): fresh["1"]})
            self.assertEqual(cached, result)
            self.assertFalse(expired)
            self.assertTrue(exists)
            collect.assert_called_once_with(self.rows, "26135")

    def test_homepage_fixture_title_maps_to_match_detail(self):
        page = """
        <li>
          <a href="/zuqiu/500105779.html" title="拜仁慕尼黑 vs 美因茨">
            拜仁慕尼黑
          </a>
          <a href="/zuqiu/500105779.html" title="拜仁慕尼黑 vs 美因茨">
            美因茨
          </a>
          <a href="/zuqiu/500105779.html" title="拜仁慕尼黑 vs 美因茨">
            即将开始
          </a>
        </li>
        """
        self.assertEqual(
            parse_match_links(
                page, "http://huakangsy.cn/zuqiu/", self.rows
            ),
            {
                ("拜仁慕尼黑", "美因茨"):
                    "http://huakangsy.cn/play/500105779_1.html"
            },
        )

    def test_unrelated_game_does_not_match(self):
        page = """
        <li>
          <a href="/zuqiu/42.html" title="皇马 vs 巴萨">皇马</a>
        </li>
        """
        self.assertEqual(
            parse_match_links(
                page, "http://huakangsy.cn/zuqiu/", self.rows
            ),
            {},
        )

    def test_26162_abbreviated_team_names_match_full_live_titles(self):
        cases = (
            ("曼联", "维拉", "曼彻斯特联 vs 阿斯顿维拉", "3003946"),
            ("国米", "科莫", "国际米兰 vs 科莫", "2993859"),
            ("马竞技", "巴萨", "马德里竞技 vs 巴塞罗那", "3013752"),
        )
        for home, away, title, fixture_id in cases:
            with self.subTest(title=title):
                page = (
                    f'<a href="/zuqiu/{fixture_id}.html" '
                    f'title="{title}">{title}</a>'
                )
                self.assertEqual(
                    parse_match_links(
                        page,
                        HOME_URL,
                        [{"home_team": home, "away_team": away}],
                    ),
                    {
                        (home, away):
                            f"http://huakangsy.cn/play/{fixture_id}_1.html"
                    },
                )

    def test_cantonese_villarreal_and_getafe_names_match_mandarin_title(self):
        for title in ("比利亚雷亚尔 vs 赫塔菲", "比利亚雷亚尔 vs 赫塔费"):
            with self.subTest(title=title):
                page = (
                    '<a href="/zuqiu/3013761.html" '
                    f'title="{title}">{title}</a>'
                )
                self.assertEqual(
                    parse_match_links(
                        page,
                        HOME_URL,
                        [{"home_team": "維拉利爾", "away_team": "加泰"}],
                    ),
                    {
                        ("維拉利爾", "加泰"):
                            "http://huakangsy.cn/play/3013761_1.html"
                    },
                )

    def test_stuttgart_and_bremen_aliases_match_full_live_title(self):
        page = (
            '<a href="/zuqiu/3018866.html" '
            'title="斯图加特 vs 云达不莱梅">斯图加特 vs 云达不莱梅</a>'
        )
        for home in ("斯图加", "史特加"):
            with self.subTest(home=home):
                self.assertEqual(
                    parse_match_links(
                        page,
                        HOME_URL,
                        [{"home_team": home, "away_team": "不来梅"}],
                    ),
                    {
                        (home, "不来梅"):
                            "http://huakangsy.cn/play/3018866_1.html"
                    },
                )

    def test_aliases_do_not_match_different_teams(self):
        page = (
            '<a href="/zuqiu/3003946.html" '
            'title="曼彻斯特联 vs 阿斯顿维拉">曼彻斯特联 vs 阿斯顿维拉</a>'
        )
        self.assertEqual(
            parse_match_links(
                page,
                HOME_URL,
                [{"home_team": "曼城", "away_team": "维拉"}],
            ),
            {},
        )

    def test_collects_match_from_popular_league_page_using_issue_match_number(self):
        rows = [{
            "issue": "26161",
            "league": "德甲",
            "match_no": "5",
            "home_team": "粤语主队",
            "away_team": "粤语客队",
        }]
        homepage = '<a href="/zuqiu/dejia/">德甲</a>'
        league = (
            '<a href="/zuqiu/3018863.html" '
            'title="美因茨 vs 拜仁慕尼黑">美因茨</a>'
        )

        def fetch(url):
            return homepage if url == HOME_URL else league

        with (
            patch("football_analysis.live_links._fetch", side_effect=fetch),
            patch(
                "football_analysis.cantonese.fetch_mandarin_matches",
                return_value={"5": ("美因茨", "拜仁慕尼黑")},
            ) as fetch_mandarin,
        ):
            self.assertEqual(
                _collect_links(rows, "26161"),
                {"5": "http://huakangsy.cn/play/3018863_1.html"},
            )
        fetch_mandarin.assert_called_once_with("26161")

    def test_homepage_league_links_are_limited_to_directories(self):
        html = (
            '<a href="/lanqiu/">篮球直播</a>'
            '<a href="/zuqiu/dejia/">德甲</a>'
            '<a href="/zuqiu/3018863.html">比赛详情</a>'
            '<a href="https://example.com/zuqiu/other/">外站</a>'
        )
        self.assertEqual(
            parse_league_links(html, HOME_URL),
            ["http://huakangsy.cn/zuqiu/dejia/"],
        )

    def test_short_team_name_matches_full_live_page_title(self):
        rows = [{"home_team": "诺丁汉", "away_team": "曼彻斯特城"}]
        page = (
            '<a href="/zuqiu/3003947.html" '
            'title="诺丁汉森林 vs 曼彻斯特城">诺丁汉森林</a>'
        )
        self.assertEqual(
            parse_match_links(page, HOME_URL, rows),
            {
                ("诺丁汉", "曼彻斯特城"):
                    "http://huakangsy.cn/play/3003947_1.html"
            },
        )

    def test_first_source_suffix_is_not_duplicated(self):
        page = (
            '<a href="/zuqiu/3003947_1.html" '
            'title="诺丁汉森林 vs 曼彻斯特城">直播</a>'
        )
        self.assertEqual(
            parse_match_links(page, HOME_URL, [{
                "home_team": "诺丁汉森林",
                "away_team": "曼彻斯特城",
            }]),
            {
                ("诺丁汉森林", "曼彻斯特城"):
                    "http://huakangsy.cn/play/3003947_1.html"
            },
        )

    def test_does_not_match_unrelated_title_from_shared_page_container(self):
        rows = [{"home_team": "诺丁汉", "away_team": "曼彻斯特城"}]
        page = """
        <div>
          <a href="/zuqiu/3003947.html" title="诺丁汉森林 vs 曼彻斯特城">
            诺丁汉森林
          </a>
          <a href="/zuqiu/3003946.html" title="曼彻斯特联 vs 阿斯顿维拉">
            曼彻斯特联
          </a>
        </div>
        """
        self.assertEqual(
            parse_match_links(page, HOME_URL, rows),
            {
                ("诺丁汉", "曼彻斯特城"):
                    "http://huakangsy.cn/play/3003947_1.html"
            },
        )

    def test_fetch_preserves_http_charset_instead_of_apparent_guess(self):
        response = Mock()
        response.encoding = "utf-8"
        response.apparent_encoding = "gbk"
        response.text = "诺丁汉森林 vs 曼彻斯特城"
        with patch("football_analysis.live_links.requests.get", return_value=response):
            self.assertEqual(_fetch(HOME_URL), response.text)
        self.assertEqual(response.encoding, "utf-8")

    def test_matched_link_renders_between_match_and_score(self):
        body = render_query_html(
            [{
                **self.rows[0],
                "kind": "升盘",
                "level": "关注",
                "match_time": "10-07 02:45",
                "final_score": "",
            }],
            issues=[],
            live_links={
                ("26135", "1"):
                    "http://huakangsy.cn/play/500105779_1.html"
            },
        )
        self.assertLess(body.index(">比赛<"), body.index(">直播<"))
        self.assertLess(body.index(">直播<"), body.index(">比分<"))
        self.assertIn(
            'href="http://huakangsy.cn/play/500105779_1.html"',
            body,
        )
        self.assertIn("image/gif;base64,", body)
        self.assertIn(
            ".live-link img { display: block; width: 10px; height: 10px;",
            body,
        )
        self.assertIn('alt="直播"', body)


class CrzbLiveLinkTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "match_no": "13", "home_team": "斯洛文尼", "away_team": "瑞士",
            "match_time": "11-14 03:45",
        }]
        self.match = {
            "tournament_id": 2981508,
            "home_name_zh": "斯洛文尼亚", "away_name_zh": "瑞士",
            "match_time_int": int(datetime(
                2026, 11, 14, 3, 45, tzinfo=timezone(timedelta(hours=8))
            ).timestamp()),
            "is_zb": 1,
        }
        self.anchors = {"more": [{
            "tournament_id": 2981508, "tournament_type": 1, "member_id": 1700,
        }]}

    def test_api_uses_site_headers_and_returns_data(self):
        response = Mock()
        response.json.return_value = {"code": 200, "data": [self.match]}
        with patch("football_analysis.live_links.requests.get", return_value=response) as get:
            self.assertEqual(_fetch_crzb_json("/web/web_football_score_list"), [self.match])
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(get.call_args.args[0],
                         "https://www.crzb.com/api/web/web_football_score_list")
        self.assertEqual(get.call_args.kwargs["headers"]["site"], "crzb")
        self.assertEqual(get.call_args.kwargs["headers"]["timezone"], "UTC+8")

    def test_api_errors_and_invalid_payloads_are_rejected(self):
        for payload in ({"code": 500, "data": []}, {"code": 200, "data": None}, []):
            response = Mock()
            response.json.return_value = payload
            with self.subTest(payload=payload), patch(
                "football_analysis.live_links.requests.get", return_value=response,
            ), self.assertRaises(ValueError):
                _fetch_crzb_json("/web/web_football_score_list")

    def test_matches_abbreviation_and_uses_returned_anchor(self):
        with patch("football_analysis.live_links._fetch_crzb_json", side_effect=[
            [self.match], self.anchors,
        ]) as fetch:
            self.assertEqual(_collect_crzb_links(self.rows), {
                "13": "https://www.crzb.com/detail/1/2981508/1700",
            })
        self.assertIn("get_public_anchor?tournament_id=2981508", fetch.call_args.args[0])

    def test_rejects_youth_reverse_fixture_and_different_date(self):
        for changes in (
            {"home_name_zh": "斯洛文尼亚U21", "away_name_zh": "瑞士U21"},
            {"home_name_zh": "瑞士", "away_name_zh": "斯洛文尼亚"},
            {"match_time_int": self.match["match_time_int"] - 86400},
            {"is_zb": 0},
        ):
            with self.subTest(changes=changes), patch(
                "football_analysis.live_links._fetch_crzb_json",
                return_value=[{**self.match, **changes}],
            ) as fetch:
                self.assertEqual(_collect_crzb_links(self.rows), {})
                self.assertEqual(fetch.call_count, 1)

    def test_no_link_without_a_valid_anchor_for_this_match(self):
        for anchors in ({"more": []}, {"more": [{
            "tournament_id": 999, "tournament_type": 1, "member_id": 1700,
        }]}, {"more": [{
            "tournament_id": 2981508, "tournament_type": 2, "member_id": 1700,
        }]}):
            with self.subTest(anchors=anchors), patch(
                "football_analysis.live_links._fetch_crzb_json",
                side_effect=[[self.match], anchors],
            ):
                self.assertEqual(_collect_crzb_links(self.rows), {})

    def test_one_failed_anchor_does_not_drop_other_matches(self):
        rows = self.rows + [{"match_no": "10", "home_team": "荷兰", "away_team": "希腊"}]
        other = {**self.match, "tournament_id": 2981461,
                 "home_name_zh": "荷兰", "away_name_zh": "希腊"}
        with patch("football_analysis.live_links._fetch_crzb_json", side_effect=[
            [self.match, other], requests.Timeout(), {"more": [{
                "tournament_id": 2981461, "tournament_type": 1, "member_id": 1753,
            }]},
        ]):
            self.assertEqual(_collect_crzb_links(rows), {
                "10": "https://www.crzb.com/detail/1/2981461/1753",
            })

    def test_falls_back_when_old_provider_is_unavailable_and_500_fails(self):
        with (
            patch("football_analysis.live_links._fetch", side_effect=requests.Timeout()),
            patch("football_analysis.cantonese.fetch_mandarin_matches", side_effect=RuntimeError()),
            patch("football_analysis.live_links._collect_crzb_links", return_value={"13": "new"}) as fallback,
        ):
            self.assertEqual(_collect_links(self.rows, "26163"), {"13": "new"})
        fallback.assert_called_once_with(self.rows)

    def test_only_fills_missing_matches_and_preserves_old_links(self):
        rows = [{**self.rows[0], "league": "英超"}, {
            "match_no": "10", "home_team": "荷兰", "away_team": "希腊", "league": "欧国联",
        }]
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links", return_value={"13": "old"}),
            patch("football_analysis.live_links._collect_crzb_links", return_value={"10": "new"}) as fallback,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {"13": "old", "10": "new"})
        fallback.assert_called_once_with([rows[1]])

    def test_crzb_keeps_full_database_names_when_500_returns_short_names(self):
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={
                "13": ("斯洛文", "瑞士"),
            }),
            patch("football_analysis.live_links._collect_huakang_links", return_value={}),
            patch("football_analysis.live_links._fetch_crzb_json", side_effect=[
                [self.match], self.anchors,
            ]),
        ):
            self.assertEqual(_collect_links(self.rows, "26163"), {
                "13": "https://www.crzb.com/detail/1/2981508/1700",
            })

    def test_new_provider_failure_preserves_existing_links(self):
        rows = [{"match_no": "1", "league": "英超"}] + self.rows
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links", return_value={"1": "old"}),
            patch("football_analysis.live_links._fetch_crzb_json", side_effect=requests.Timeout()),
        ):
            self.assertEqual(_collect_links(rows, "26163"), {"1": "old"})


class LiveSourceRoutingTests(unittest.TestCase):
    def test_non_big_five_go_directly_to_crzb_without_500_or_old_site(self):
        for league in ("欧国联", "欧冠", "英足总杯", "英冠", "国际友谊", "日职", "", None):
            rows = [{"match_no": "1", "league": league}]
            with (
                self.subTest(league=league),
                patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
                patch("football_analysis.live_links._collect_huakang_links") as old,
                patch("football_analysis.live_links._collect_crzb_links", return_value={"1": "crzb"}) as crzb,
            ):
                self.assertEqual(_collect_links(rows, "26163"), {"1": "crzb"})
            old.assert_not_called()
            mandarin.assert_not_called()
            crzb.assert_called_once_with(rows)

    def test_each_big_five_league_keeps_old_provider_and_name_conversion(self):
        for league in ("英超", "西甲", "德甲", "意甲", "法甲"):
            rows = [{"match_no": "1", "league": league,
                     "home_team": "原主队", "away_team": "原客队"}]
            with (
                self.subTest(league=league),
                patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={
                    "1": ("转换主队", "转换客队"),
                }) as mandarin,
                patch("football_analysis.live_links._collect_huakang_links", return_value={"1": "old"}) as old,
                patch("football_analysis.live_links._collect_crzb_links") as crzb,
            ):
                self.assertEqual(_collect_links(rows, "26163"), {"1": "old"})
            mandarin.assert_called_once_with("26163")
            old.assert_called_once_with([{
                **rows[0], "home_team": "转换主队", "away_team": "转换客队",
            }])
            crzb.assert_not_called()

    def test_mixed_issue_uses_crzb_for_other_leagues_and_missing_big_five(self):
        rows = [
            {"match_no": "1", "league": "英超"},
            {"match_no": "2", "league": "欧国联"},
            {"match_no": "3", "league": "意甲"},
        ]
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links", return_value={"1": "old"}) as old,
            patch("football_analysis.live_links._collect_crzb_links", return_value={"2": "new2", "3": "new3"}) as crzb,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {
                "1": "old", "2": "new2", "3": "new3",
            })
        old.assert_called_once_with([rows[0], rows[2]])
        crzb.assert_called_once_with(rows[1:])

    def test_non_big_five_failure_does_not_fall_back_to_old_provider(self):
        rows = [{"match_no": "1", "league": "欧国联"}]
        with (
            patch("football_analysis.live_links._fetch_crzb_json", side_effect=requests.Timeout()),
            patch("football_analysis.live_links._collect_huakang_links") as old,
            patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {})
        old.assert_not_called()
        mandarin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
