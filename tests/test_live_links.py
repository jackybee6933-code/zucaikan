import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from football_analysis.live_links import (
    HOME_URL,
    _collect_links,
    _fetch,
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


if __name__ == "__main__":
    unittest.main()
