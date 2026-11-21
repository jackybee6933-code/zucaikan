import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

from football_analysis.live_links import HOME_URL, _collect_huakang_links


class FeimaLiveLinkTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "match_no": "1", "home_team": "拜仁", "away_team": "科隆",
            "match_time": "11-21 03:30",
        }]
        self.match = {
            "tournament_id": 3018869, "home_name_zh": "拜仁慕尼黑",
            "away_name_zh": "科隆", "is_zb": 1,
            "match_time_int": int(datetime(
                2026, 11, 21, 3, 30, tzinfo=timezone(timedelta(hours=8))
            ).timestamp()),
        }
        self.anchors = {
            "data": {"tournament_id": 3018869, "member_id": 133,
                     "nickname": "高清源"},
            "more": [
                {"tournament_id": 3018869, "tournament_type": 1,
                 "member_id": 1700, "nickname": "主播甲"},
                {"tournament_id": 3018869, "tournament_type": 1,
                 "member_id": 1700, "nickname": "重复主播"},
                {"tournament_id": 3018869, "tournament_type": 2,
                 "member_id": 1701, "nickname": "篮球主播"},
                {"tournament_id": 999, "tournament_type": 1,
                 "member_id": 1702, "nickname": "其他比赛"},
            ],
        }

    def response(self, data):
        response = Mock()
        response.json.return_value = {"code": 200, "data": data}
        response.text = json.dumps(response.json.return_value)
        return response

    def test_backup_uses_new_schedule_and_all_valid_rooms_on_same_host(self):
        with patch("football_analysis.live_links.requests.get", side_effect=[
            self.response([self.match]), self.response(self.anchors),
        ]) as get:
            self.assertEqual(_collect_huakang_links(self.rows), {
                "1": [
                    {"name": "高清源",
                     "url": "https://www.fmtg2f.com/detail/1/3018869/133"},
                    {"name": "主播甲",
                     "url": "https://www.fmtg2f.com/detail/1/3018869/1700"},
                ],
            })
        self.assertEqual(HOME_URL, "https://www.fmtg2f.com/score/football/lives")
        self.assertEqual([call.args[0] for call in get.call_args_list], [
            "https://www.fmtg2f.com/api/web/web_football_score_list",
            "https://www.fmtg2f.com/api/web/get_public_anchor"
            "?tournament_id=3018869&type=1&tournament_type=1",
        ])
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["site"], "fmzb")
            self.assertEqual(call.kwargs["headers"]["Referer"], HOME_URL)
            self.assertEqual(call.kwargs["headers"]["timezone"], "UTC+8")

    def test_backup_rejects_youth_reversed_date_and_unbroadcast_matches(self):
        for changes in (
            {"home_name_zh": "拜仁慕尼黑U21"},
            {"home_name_zh": "科隆", "away_name_zh": "拜仁慕尼黑"},
            {"match_time_int": self.match["match_time_int"] - 86400},
            {"is_zb": 0},
        ):
            with self.subTest(changes=changes), patch(
                "football_analysis.live_links.requests.get",
                return_value=self.response([{**self.match, **changes}]),
            ) as get:
                self.assertEqual(_collect_huakang_links(self.rows), {})
                get.assert_called_once()

    def test_backup_preserves_short_and_cantonese_team_aliases(self):
        cases = (
            ("曼联", "维拉", "曼彻斯特联", "阿斯顿维拉"),
            ("国米", "科莫", "国际米兰", "科莫"),
            ("马竞技", "巴萨", "马德里竞技", "巴塞罗那"),
            ("維拉利爾", "加泰", "比利亚雷亚尔", "赫塔菲"),
            ("维拉利尔", "加泰", "比利亚雷亚尔", "赫塔费"),
            ("斯图加", "不来梅", "斯图加特", "云达不莱梅"),
            ("史特加", "不来梅", "斯图加特", "云达不莱梅"),
            ("毕尔巴", "西班人", "毕尔巴鄂竞技", "西班牙人"),
            ("诺丁汉", "曼彻斯特城", "诺丁汉森林", "曼彻斯特城"),
            ("伯恩茅", "诺丁汉", "伯恩茅斯", "诺丁汉森林"),
            ("热刺", "伊普斯", "托特纳姆热刺", "伊普斯维奇"),
            ("纽卡斯", "阿森纳", "纽卡斯尔联", "阿森纳"),
            ("纽卡斯尔", "阿森纳", "纽卡斯尔联", "阿森纳"),
            ("埃沃斯", "弗赖堡", "埃尔沃斯贝格", "弗赖堡"),
            ("埃沃斯堡", "弗赖堡", "埃尔沃斯贝格", "弗赖堡"),
            ("法兰克福", "霍芬海", "法兰克福", "霍芬海姆"),
            ("勒沃", "帕德博", "勒沃库森", "帕德博恩"),
            ("门兴", "多特", "门兴格拉德巴赫", "多特蒙德"),
            ("柏林联", "莱比锡红牛", "柏林联合", "RB莱比锡"),
            ("柏林联合", "莱红牛", "柏林联合", "RB莱比锡"),
            ("沙尔克", "斯图加", "沙尔克04", "斯图加特"),
            ("塞维利", "贝蒂斯", "塞维利亚", "皇家贝蒂斯"),
        )
        for home, away, full_home, full_away in cases:
            rows = [{**self.rows[0], "home_team": home, "away_team": away}]
            match = {**self.match, "home_name_zh": full_home,
                     "away_name_zh": full_away}
            with self.subTest(home=home), patch(
                "football_analysis.live_links.requests.get",
                side_effect=[self.response([match]), self.response(self.anchors)],
            ):
                self.assertEqual(len(_collect_huakang_links(rows)["1"]), 2)

    def test_backup_request_and_invalid_json_fail_without_primary_requests(self):
        with patch(
            "football_analysis.live_links.requests.get",
            side_effect=requests.Timeout(),
        ) as get:
            self.assertEqual(_collect_huakang_links(self.rows), {})
            get.assert_called_once()
        invalid = Mock()
        invalid.json.side_effect = ValueError("invalid JSON")
        invalid.text = "<html>verification</html>"
        with patch(
            "football_analysis.live_links.requests.get", return_value=invalid,
        ) as get:
            self.assertEqual(_collect_huakang_links(self.rows), {})
            get.assert_called_once()

    def test_backup_rejects_error_and_invalid_payloads(self):
        for payload in ({"code": 500, "data": []},
                        {"code": 200, "data": None}, []):
            response = Mock()
            response.json.return_value = payload
            response.text = json.dumps(payload)
            with self.subTest(payload=payload), patch(
                "football_analysis.live_links.requests.get", return_value=response,
            ) as get:
                self.assertEqual(_collect_huakang_links(self.rows), {})
                get.assert_called_once()

    def test_backup_failed_room_does_not_drop_other_matches(self):
        rows = self.rows + [{
            "match_no": "2", "home_team": "曼联", "away_team": "维拉",
        }]
        other = {**self.match, "tournament_id": 3003946,
                 "home_name_zh": "曼彻斯特联", "away_name_zh": "阿斯顿维拉"}
        anchors = {"data": {
            "tournament_id": 3003946, "member_id": 133, "nickname": "高清源",
        }}
        with patch("football_analysis.live_links.requests.get", side_effect=[
            self.response([self.match, other]), requests.Timeout(),
            self.response(anchors),
        ]):
            self.assertEqual(_collect_huakang_links(rows), {
                "2": [{"name": "高清源",
                       "url": "https://www.fmtg2f.com/detail/1/3003946/133"}],
            })


if __name__ == "__main__":
    unittest.main()
