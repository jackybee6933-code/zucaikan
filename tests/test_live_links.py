import unittest
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from football_analysis.live_links import (
    _collect_crzb_links,
    _collect_huakang_links,
    _collect_links,
    _fetch_crzb_json,
    _fetch_huakang_json,
    load_cached_live_links,
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

    def test_primary_uses_database_names_and_issue_match_number_without_500(self):
        rows = [{
            "issue": "26161",
            "league": "德甲",
            "match_no": "5",
            "home_team": "美因茨",
            "away_team": "拜仁",
        }]
        with (
            patch("football_analysis.live_links._fetch_huakang_json", side_effect=[
                [{"tournament_id": 3018863, "home_name_zh": "美因茨",
                  "away_name_zh": "拜仁慕尼黑", "is_zb": 1}],
                {"data": {"tournament_id": 3018863, "member_id": 133,
                          "nickname": "高清源"}},
            ]),
            patch(
                "football_analysis.cantonese.fetch_mandarin_matches",
                return_value={"5": ("美因茨", "拜仁慕尼黑")},
            ) as fetch_mandarin,
            patch("football_analysis.live_links._collect_crzb_links", return_value={}),
        ):
            self.assertEqual(
                _collect_links(rows, "26161"),
                {"5": [{"name": "高清源",
                        "url": "https://www.fmtg2f.com/detail/1/3018863/133"}]},
            )
        fetch_mandarin.assert_not_called()

    def test_backup_json_preserves_http_charset_instead_of_apparent_guess(self):
        response = Mock()
        response.encoding = "utf-8"
        response.apparent_encoding = "gbk"
        response.json.return_value = {"code": 200, "data": []}
        with patch("football_analysis.live_links.requests.get", return_value=response):
            self.assertEqual(_fetch_huakang_json("/web/web_football_score_list"), [])
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


class FeimaTeamAliasTests(unittest.TestCase):
    def cases(self):
        return (
            ("6", "AC米兰", "弗洛西诺", "弗洛西诺尼", "費辛隆尼", 2993865, 23, 1),
            ("8", "巴萨", "比利亚雷", "比利亚雷亚尔", "維拉利爾", 3013764, 22, 21),
        )

    def test_26168_short_names_match_feima_and_keep_all_anchors(self):
        for number, home, away, full_away, traditional, match_id, day, hour in self.cases():
            rows = [{"match_no": number, "home_team": home, "away_team": away,
                     "match_time": f"11-{day:02} {hour:02}:00"}]
            for away_name in (full_away, traditional):
                match = {
                    "tournament_id": match_id,
                    "home_name_zh": "AC米兰" if number == "6" else "巴塞罗那",
                    "away_name_zh": away_name,
                    "match_time_int": int(datetime(
                        2026, 11, day, hour, tzinfo=timezone(timedelta(hours=8))
                    ).timestamp()),
                    "is_zb": 1,
                }
                anchors = {"more": [
                    {"tournament_id": match_id, "tournament_type": 1,
                     "member_id": member, "username": f"主播 {member}"}
                    for member in (133, 1700)
                ]}
                with self.subTest(number=number, name=away_name), patch(
                    "football_analysis.live_links._fetch_huakang_json",
                    side_effect=[[match], anchors],
                ), patch("football_analysis.live_links._collect_crzb_links") as backup:
                    links = _collect_links(rows, "26168")
                    self.assertEqual(links.get(number), [
                        {"name": f"主播 {member}",
                         "url": f"https://www.fmtg2f.com/detail/1/{match_id}/{member}"}
                        for member in (133, 1700)
                    ])
                    backup.assert_not_called()

    def test_26168_aliases_do_not_match_other_teams(self):
        for number, home, away, full_away, _, match_id, day, hour in self.cases():
            names = (full_away + "女足", full_away + "U21", full_away + "B")
            if number == "8":
                names += ("比利亚罗夫莱多",)
            for wrong_name in names:
                rows = [{"match_no": number, "home_team": home, "away_team": away,
                         "match_time": f"11-{day:02} {hour:02}:00"}]
                match = {
                    "tournament_id": match_id,
                    "home_name_zh": "AC米兰" if number == "6" else "巴塞罗那",
                    "away_name_zh": wrong_name,
                    "match_time_int": int(datetime(
                        2026, 11, day, hour, tzinfo=timezone(timedelta(hours=8))
                    ).timestamp()),
                    "is_zb": 1,
                }
                with self.subTest(number=number, name=wrong_name), patch(
                    "football_analysis.live_links._fetch_huakang_json",
                    return_value=[match],
                ) as fetch:
                    self.assertEqual(_collect_huakang_links(rows), {})
                    fetch.assert_called_once()


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

    def test_collects_all_anchors_and_default_room_without_duplicates(self):
        first = {**self.anchors["more"][0], "username": "主播甲"}
        second = {**first, "member_id": 1701, "username": "主播乙"}
        data = {
            "data": {"tournament_id": 2981508, "member_id": 133, "nickname": "高清源"},
            "more": [first, second, first, {**second, "tournament_type": 2},
                     {**second, "tournament_id": 999}, {**second, "member_id": "bad"}],
        }
        with patch("football_analysis.live_links._fetch_crzb_json",
                   side_effect=[[self.match], data]):
            links = _collect_crzb_links(self.rows)["13"]
        self.assertEqual([item["name"] for item in links], ["高清源", "主播甲", "主播乙"])
        self.assertEqual([item["url"] for item in links], [
            f"https://crl4.crzbcx8.com/detail/1/2981508/{member}"
            for member in (133, 1700, 1701)
        ])

    def test_multi_anchor_cache_round_trip(self):
        sources = [{"name": "主播甲", "url": "https://www.crtv0.live/detail/1/2981508/1700"},
                   {"name": "主播乙", "url": "https://www.crtv0.live/detail/1/2981508/1701"}]
        with tempfile.TemporaryDirectory() as cache_dir, patch(
            "football_analysis.live_links._collect_links", return_value={"13": sources}
        ):
            result = live_links(self.rows, "26163", force=True, cache_dir=cache_dir)
            cached, expired, exists = load_cached_live_links(self.rows, "26163", cache_dir)
        self.assertEqual(cached, result)
        self.assertEqual(cached[("26163", "13")], sources)
        self.assertFalse(expired)
        self.assertTrue(exists)

    def test_popup_renders_all_anchors_and_escapes_untrusted_fields(self):
        body = render_query_html(
            [{**self.rows[0], "issue": "26163"}], issues=[],
            live_links={("26163", "13"): [
                {"name": '<img src=x onerror=alert(1)>', "url": "https://www.crtv0.live/detail/1/2981508/1700"},
                {"name": "主播乙", "url": "https://www.crtv0.live/detail/1/2981508/1701"},
                {"name": "恶意", "url": "javascript:alert(1)", "avatar": "javascript:alert(2)"},
            ]},
        )
        self.assertIn('class="live-link live-open"', body)
        self.assertIn('id="live-dialog"', body)
        self.assertIn('aria-labelledby="live-dialog-title"', body)
        self.assertIn("主播乙", body)
        self.assertIn("https://www.crtv0.live/detail/1/2981508/1701", body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body)
        self.assertNotIn("<img src=x onerror=alert(1)>", body)
        self.assertNotIn("javascript:alert", body)

    def test_popup_uses_compact_text_and_relative_avatar_size(self):
        body = render_query_html([], issues=[])
        for style in (
            "font-size: 12px; line-height: 1.35;",
            "gap: 6px; min-width: 0; padding: 6px 0;",
            ".live-anchor img { flex: 0 0 2em; width: 2em; height: 2em;",
            ".live-anchor span { overflow-wrap: anywhere; font-size: inherit; }",
        ):
            self.assertTrue(style in body, style)

    def test_api_uses_site_headers_and_returns_data(self):
        response = Mock()
        response.json.return_value = {"code": 200, "data": [self.match]}
        with patch("football_analysis.live_links.requests.get", return_value=response) as get:
            self.assertEqual(_fetch_crzb_json("/web/web_football_score_list"), [self.match])
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(get.call_args.args[0],
                         "https://crl4.crzbcx8.com/api/web/web_football_score_list")
        self.assertEqual(get.call_args.kwargs["headers"]["site"], "crzb")
        self.assertEqual(get.call_args.kwargs["headers"]["timezone"], "UTC+8")
        self.assertEqual(
            get.call_args.kwargs["headers"]["Referer"],
            "https://crl4.crzbcx8.com/score/football/lives",
        )

    def test_api_falls_back_to_backup_host_when_primary_fails(self):
        response = Mock()
        response.json.return_value = {"code": 200, "data": [self.match]}
        with patch("football_analysis.live_links.requests.get", side_effect=[
            requests.Timeout(), response,
        ]) as get:
            self.assertEqual(_fetch_crzb_json("/web/web_football_score_list"), [self.match])

        self.assertEqual(get.call_args_list[0].args[0],
                         "https://crl4.crzbcx8.com/api/web/web_football_score_list")
        self.assertEqual(get.call_args_list[1].args[0],
                         "https://www.crzb.com/api/web/web_football_score_list")
        self.assertEqual(
            get.call_args_list[1].kwargs["headers"]["Referer"],
            "https://www.crzb.com/score/football/lives",
        )
        self.assertEqual(get.call_count, 2)

    def test_api_uses_second_backup_when_first_two_hosts_fail(self):
        invalid = Mock()
        invalid.json.side_effect = ValueError("invalid JSON")
        response = Mock()
        response.json.return_value = {"code": 200, "data": [self.match]}
        with patch("football_analysis.live_links.requests.get", side_effect=[
            requests.Timeout(), invalid, response,
        ]) as get:
            self.assertEqual(_fetch_crzb_json("/web/web_football_score_list"), [self.match])

        self.assertEqual([call.args[0] for call in get.call_args_list], [
            "https://crl4.crzbcx8.com/api/web/web_football_score_list",
            "https://www.crzb.com/api/web/web_football_score_list",
            "https://www.crtv0.live/api/web/web_football_score_list",
        ])
        self.assertEqual(
            get.call_args.kwargs["headers"]["Referer"],
            "https://www.crtv0.live/score/football/lives",
        )

    def test_api_empty_primary_data_does_not_try_backups(self):
        for data in ([], {}):
            response = Mock()
            response.json.return_value = {"code": 200, "data": data}
            with self.subTest(data=data), patch(
                "football_analysis.live_links.requests.get", return_value=response,
            ) as get:
                self.assertEqual(_fetch_crzb_json("/web/web_football_score_list"), data)
                get.assert_called_once()

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
                "13": [{"name": "主播 1700", "url": "https://crl4.crzbcx8.com/detail/1/2981508/1700"}],
            })
        self.assertIn("get_public_anchor?tournament_id=2981508", fetch.call_args.args[0])

    def test_26166_bilbao_aliases_match_the_live_fixture(self):
        rows = [{"match_no": "4", "home_team": "毕尔巴鄂",
                 "away_team": "西班牙人", "match_time": "11-21 04:00"}]
        for home_name in ("毕尔巴鄂竞技", "畢爾包"):
            match = {**self.match, "tournament_id": 3013763,
                     "home_name_zh": home_name, "away_name_zh": "西班牙人",
                     "match_time_int": int(datetime(
                         2026, 11, 21, 4, tzinfo=timezone(timedelta(hours=8))
                     ).timestamp())}
            anchors = {"more": [{**self.anchors["more"][0],
                                "tournament_id": 3013763}]}
            with self.subTest(home_name=home_name), patch(
                "football_analysis.live_links._fetch_crzb_json",
                side_effect=[[match], anchors],
            ):
                self.assertEqual(_collect_crzb_links(rows), {
                    "4": [{"name": "主播 1700",
                           "url": "https://crl4.crzbcx8.com/detail/1/3013763/1700"}],
                })

    def test_bilbao_alias_does_not_match_youth_team(self):
        rows = [{"match_no": "4", "home_team": "毕尔巴鄂",
                 "away_team": "西班牙人"}]
        for home_name in ("毕尔巴鄂竞技U21", "畢爾包U21"):
            match = {**self.match, "home_name_zh": home_name,
                     "away_name_zh": "西班牙人"}
            with self.subTest(home_name=home_name), patch(
                "football_analysis.live_links._fetch_crzb_json",
                return_value=[match],
            ) as fetch:
                self.assertEqual(_collect_crzb_links(rows), {})
                fetch.assert_called_once()

    def test_26166_bayern_and_bielefeld_keep_all_anchors(self):
        cases = (
            ("1", "拜仁", "拜仁慕尼黑", "科隆", 3018869, 3, 30),
            ("2", "比勒费", "比勒菲尔德", "马格德堡", 3019191, 1, 30),
            ("2", "比勒费", "比勒費爾德", "马格德堡", 3019191, 1, 30),
        )
        for number, home, full_home, away, match_id, hour, minute in cases:
            rows = [{"match_no": number, "home_team": home, "away_team": away,
                     "match_time": f"11-21 {hour:02}:{minute:02}"}]
            match = {**self.match, "tournament_id": match_id,
                     "home_name_zh": full_home, "away_name_zh": away,
                     "match_time_int": int(datetime(
                         2026, 11, 21, hour, minute,
                         tzinfo=timezone(timedelta(hours=8))
                     ).timestamp())}
            data = {"more": [
                {"tournament_id": match_id, "tournament_type": 1,
                 "member_id": member, "username": f"主播 {member}"}
                for member in (1700, 1701)
            ]}
            with self.subTest(home=full_home), patch(
                "football_analysis.live_links._fetch_crzb_json",
                side_effect=[[match], data],
            ):
                links = _collect_crzb_links(rows)[number]
                self.assertEqual([source["url"] for source in links], [
                    f"https://crl4.crzbcx8.com/detail/1/{match_id}/{member}"
                    for member in (1700, 1701)
                ])

    def test_bayern_and_bielefeld_aliases_do_not_match_youth_teams(self):
        for home, full_home in (
            ("拜仁", "拜仁慕尼黑U21"),
            ("比勒费", "比勒菲尔德U21"),
        ):
            rows = [{"match_no": "1", "home_team": home, "away_team": "科隆"}]
            match = {**self.match, "home_name_zh": full_home, "away_name_zh": "科隆"}
            with self.subTest(home=full_home), patch(
                "football_analysis.live_links._fetch_crzb_json",
                return_value=[match],
            ) as fetch:
                self.assertEqual(_collect_crzb_links(rows), {})
                fetch.assert_called_once()

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
                "10": [{"name": "主播 1753", "url": "https://crl4.crzbcx8.com/detail/1/2981461/1753"}],
            })

    def test_falls_back_when_feima_is_unavailable_without_500(self):
        with (
            patch("football_analysis.live_links._fetch_huakang_json", side_effect=requests.Timeout()),
            patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
            patch("football_analysis.live_links._collect_crzb_links", return_value={"13": "new"}) as fallback,
        ):
            self.assertEqual(_collect_links(self.rows, "26163"), {"13": "new"})
        fallback.assert_called_once_with(self.rows)
        mandarin.assert_not_called()

    def test_backup_only_fills_missing_matches_and_preserves_primary_links(self):
        rows = [{**self.rows[0], "league": "英超"}, {
            "match_no": "10", "home_team": "荷兰", "away_team": "希腊", "league": "欧国联",
        }]
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links", return_value={"13": "old"}) as old,
            patch("football_analysis.live_links._collect_crzb_links", return_value={"10": "new"}) as fallback,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {"13": "old", "10": "new"})
        fallback.assert_called_once_with([rows[1]])
        old.assert_called_once_with(rows)

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
                "13": [{"name": "主播 1700", "url": "https://crl4.crzbcx8.com/detail/1/2981508/1700"}],
            })

    def test_backup_failure_preserves_feima_links(self):
        rows = [{"match_no": "1", "league": "英超"}] + self.rows
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links", return_value={"1": "old"}),
            patch("football_analysis.live_links._fetch_crzb_json", side_effect=requests.Timeout()),
        ):
            self.assertEqual(_collect_links(rows, "26163"), {"1": "old"})


class LiveSourceRoutingTests(unittest.TestCase):
    def test_every_league_uses_feima_first_without_500_or_backup(self):
        for league in ("英超", "西甲", "德甲", "意甲", "法甲",
                       "欧国联", "欧冠", "英足总杯", "英冠",
                       "国际友谊", "日职", "", None):
            rows = [{"match_no": "1", "league": league}]
            with (
                self.subTest(league=league),
                patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
                patch("football_analysis.live_links._collect_huakang_links",
                      return_value={"1": "feima"}) as feima,
                patch("football_analysis.live_links._collect_crzb_links") as crzb,
            ):
                self.assertEqual(_collect_links(rows, "26163"), {"1": "feima"})
            feima.assert_called_once_with(rows)
            mandarin.assert_not_called()
            crzb.assert_not_called()

    def test_every_league_falls_back_to_crzb_with_original_names(self):
        for league in ("英超", "西甲", "德甲", "意甲", "法甲", "欧国联", "", None):
            rows = [{"match_no": "1", "league": league,
                     "home_team": "原主队", "away_team": "原客队"}]
            with (
                self.subTest(league=league),
                patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={
                    "1": ("转换主队", "转换客队"),
                }) as mandarin,
                patch("football_analysis.live_links._collect_huakang_links",
                      return_value={}) as feima,
                patch("football_analysis.live_links._collect_crzb_links",
                      return_value={"1": "crzb"}) as crzb,
            ):
                calls = Mock()
                calls.attach_mock(feima, "primary")
                calls.attach_mock(crzb, "backup")
                self.assertEqual(_collect_links(rows, "26163"), {"1": "crzb"})
            mandarin.assert_not_called()
            feima.assert_called_once_with(rows)
            crzb.assert_called_once_with(rows)
            self.assertEqual([call[0] for call in calls.mock_calls],
                             ["primary", "backup"])

    def test_feima_success_keeps_all_anchors_without_using_backup(self):
        sources = [
            {"name": "主播甲", "url": "https://example.test/live/1"},
            {"name": "主播乙", "url": "https://example.test/live/2"},
        ]
        for league in ("英超", "西甲", "德甲", "意甲", "法甲"):
            rows = [{"match_no": "1", "league": league}]
            with (
                self.subTest(league=league),
                patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
                patch("football_analysis.live_links._collect_huakang_links",
                      return_value={"1": sources}) as feima,
                patch("football_analysis.live_links._collect_crzb_links") as crzb,
            ):
                self.assertEqual(_collect_links(rows, "26166"), {"1": sources})
            feima.assert_called_once_with(rows)
            crzb.assert_not_called()
            mandarin.assert_not_called()

    def test_mixed_issue_uses_crzb_only_for_missing_matches(self):
        rows = [
            {"match_no": "1", "league": "英超"},
            {"match_no": "2", "league": "欧国联"},
            {"match_no": "3", "league": "意甲"},
        ]
        with (
            patch("football_analysis.cantonese.fetch_mandarin_matches", return_value={}),
            patch("football_analysis.live_links._collect_huakang_links",
                  return_value={"1": "feima", "3": "feima3"}) as feima,
            patch("football_analysis.live_links._collect_crzb_links",
                  return_value={"2": "crzb2"}) as crzb,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {
                "1": "feima", "2": "crzb2", "3": "feima3",
            })
        feima.assert_called_once_with(rows)
        crzb.assert_called_once_with([rows[1]])

    def test_both_providers_failing_returns_no_links(self):
        rows = [{"match_no": "1", "league": "欧国联"}]
        with (
            patch("football_analysis.live_links._fetch_crzb_json", side_effect=requests.Timeout()),
            patch("football_analysis.live_links._fetch_huakang_json",
                  side_effect=requests.Timeout()),
            patch("football_analysis.cantonese.fetch_mandarin_matches") as mandarin,
        ):
            self.assertEqual(_collect_links(rows, "26163"), {})
        mandarin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
