import copy
import json
import unittest

from bs4 import BeautifulSoup

from football_analysis.query_report import _lean_cell, render_query_html


def _row(initial=2.5, current=2.5, scores=("2:2", "2:1")):
    h2h = "\n".join(
        f"英超 | 2026-01-{index:02d} | Home {score} Away | 0:0"
        " | 平 | odds | line | 输 | 大 | -"
        for index, score in enumerate(scores, 1)
    )
    payload = {
        "history": {
            "home": {"matches": 10, "over": 7, "under": 3, "over_rate": 0.7},
            "away": {"matches": 10, "over": 6, "under": 4, "over_rate": 0.6},
        },
        "odds_movement": {
            "total": {
                "available": True,
                "initial": {"line": initial},
                "current": {"line": current},
            },
        },
    }
    read = {
        "tendency": "主队赢盘", "confidence": 0.6, "reason": "亚盘理由",
        "over_tendency": "大球", "over_confidence": 0.68,
        "over_reason": "原大小球理由",
        "source_references": {"交战历史": h2h},
    }
    return {
        "home_team": "Home", "away_team": "Away",
        "team_read": json.dumps(read, ensure_ascii=False),
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }


def _view(row, **kwargs):
    soup = BeautifulSoup(_lean_cell(row, **kwargs), "html.parser")
    popup = BeautifulSoup(
        soup.select(".model-lean-tip")[-1].select_one("template").decode_contents(),
        "html.parser",
    )
    section = popup.select_one(".ai-tip-market-view")
    return soup, popup, section


class TotalMarketViewTests(unittest.TestCase):
    def test_compact_spacing_is_scoped_to_total_goals_popup(self):
        soup, popup, _ = _view(_row())
        self.assertIsNotNone(popup.select_one(".ai-tip-total"))
        handicap = soup.select(".model-lean-tip")[0].select_one("template")
        self.assertIsNone(handicap.select_one(".ai-tip-total"))
        page = render_query_html([], issues=[])
        self.assertIn(".ai-tip-total { line-height: 1.35; }", page)
        self.assertIn(
            ".ai-tip-total .ai-tip-section + .ai-tip-section { margin-top: 5px; padding-top: 4px; }",
            page,
        )

    def test_market_view_omits_extra_notes(self):
        _, _, section = _view(_row())
        text = section.get_text()
        self.assertNotIn("仅作盘口参考，不代表确定赛果", text)
        self.assertNotIn("按历史各场盘口统计", text)

    def test_exact_one_goal_lower_line_references_under_in_red_bold(self):
        soup, popup, section = _view(_row())
        self.assertIsNotNone(section)
        text = section.get_text(" ", strip=True, types=None)
        self.assertNotIn("对赛 2 场，场均", text)
        self.assertIn("场均进球 3.5", popup.select_one(".h2h-summary").get_text())
        self.assertIn("低于对赛场均 1.00 球", text)
        self.assertIn("主队近10场：大球率70%，小球率30%", text)
        self.assertIn("客队近10场：大球率60%，小球率40%", text)
        self.assertIn("近期概率与盘口参考存在分歧", text)
        references = section.select("strong")
        self.assertEqual([item.get_text() for item in references], ["参考小球"])
        self.assertTrue(all("color:#dc2626" in item.get("style", "") for item in references))
        self.assertEqual(
            [item.get_text() for item in popup.select("h4")],
            ["模型理由", "盘口视角", "对赛往绩"],
        )
        self.assertNotIn("庄家视角", str(soup))

    def test_higher_line_references_over(self):
        _, _, section = _view(_row(initial=3.5, current=3.5, scores=("1:1", "2:1")))
        self.assertIn("高于对赛场均 1.00 球", section.get_text(types=None))
        self.assertIn("近期概率支持该方向", section.get_text(types=None))
        self.assertEqual([item.get_text() for item in section.select("strong")], ["参考大球"])

    def test_less_than_three_quarters_has_no_reference(self):
        _, _, section = _view(_row(initial=3.0, current=3.25))
        text = section.get_text(types=None)
        self.assertNotIn("初盘", text)
        self.assertNotIn("阈值", text)
        self.assertNotIn("缺少可用公司初盘", text)
        self.assertNotIn("场均", text)
        self.assertIn("主队近10场：大球率70%，小球率30%", text)
        self.assertIn("客队近10场：大球率60%，小球率40%", text)
        self.assertFalse(section.select("strong"))

    def test_user_example_below_threshold_hides_comparison(self):
        row = _row(initial=3.25, scores=("1:1", "2:1", "1:2"))
        payload = json.loads(row["payload_json"])
        payload["odds_movement"]["total"]["initial"]["line_text"] = "3/3.5"
        row["payload_json"] = json.dumps(payload)
        _, _, section = _view(row)
        text = section.get_text()
        self.assertNotIn("场均", text)
        self.assertNotIn("初盘", text)
        self.assertNotIn("0.58", text)
        self.assertNotIn("阈值", text)
        self.assertNotIn("缺少可用公司初盘", text)

    def test_exact_three_quarters_triggers_in_both_directions(self):
        for line, expected in ((2.75, "参考小球"), (4.25, "参考大球")):
            with self.subTest(line=line):
                _, _, section = _view(_row(initial=line, current=line))
                self.assertIn("达到 0.75 球阈值", section.get_text(types=None))
                self.assertEqual([item.get_text() for item in section.select("strong")], [expected])

    def test_current_line_alone_does_not_trigger(self):
        _, _, section = _view(_row(initial=3.0, current=2.5))
        self.assertFalse(section.select("strong"))
        self.assertNotIn("即时盘", section.get_text(types=None))

    def test_initial_line_alone_can_trigger(self):
        _, _, section = _view(_row(initial=2.5, current=3.0))
        self.assertEqual([item.get_text() for item in section.select("strong")], ["参考小球"])
        self.assertIn("初盘 2.5 球", section.get_text(types=None))
        self.assertNotIn("即时盘", section.get_text(types=None))

    def test_opposite_current_line_does_not_change_opening_reference(self):
        _, _, section = _view(_row(initial=2.5, current=4.5))
        self.assertEqual(
            [item.get_text() for item in section.select("strong")],
            ["参考小球"],
        )
        self.assertNotIn("即时盘", section.get_text(types=None))

    def test_missing_opening_line_does_not_fall_back_to_current(self):
        _, _, section = _view(_row(initial=None, current=2.5))
        self.assertIn("缺少可用公司初盘", section.get_text(types=None))
        self.assertNotIn("即时盘", section.get_text(types=None))
        self.assertFalse(section.select("strong"))

    def test_split_line_text_is_averaged_and_not_first_number(self):
        row = _row(initial=None, current=None)
        payload = json.loads(row["payload_json"])
        payload["odds_movement"]["total"]["initial"]["line_text"] = "2.5/3"
        row["payload_json"] = json.dumps(payload)
        _, _, section = _view(row)
        self.assertIn("初盘 2.5/3 球", section.get_text(types=None))
        self.assertIn("低于对赛场均 0.75 球", section.get_text(types=None))
        self.assertEqual([item.get_text() for item in section.select("strong")], ["参考小球"])

    def test_displayed_h2h_overrides_old_source_and_focus(self):
        row = _row()
        read = json.loads(row["team_read"])
        read["over_focus"] = {"h2h_average": 26.6, "opening_line": 2.5, "gap": 24.1}
        row["team_read"] = json.dumps(read)
        displayed = (
            "英超 | 2026-01-01 | Home 1:1 Away | 0:0"
            " | 平 | odds | line | 输 | 小 | -"
        )
        _, popup, section = _view(row, h2h_score=displayed)
        self.assertNotIn("场均", section.get_text(types=None))
        self.assertNotIn("初盘", section.get_text(types=None))
        self.assertIn("场均进球 2.0", popup.select_one(".h2h-summary").get_text())
        self.assertFalse(section.select("strong"))

    def test_unknown_rate_is_not_shown_as_one_hundred_percent_under(self):
        row = _row()
        payload = json.loads(row["payload_json"])
        payload["history"]["home"] = {"matches": 10, "over": 0, "under": 0, "over_rate": 0}
        payload["history"]["away"] = {}
        row["payload_json"] = json.dumps(payload)
        _, _, section = _view(row)
        self.assertIn("主队近期大小球率缺失", section.get_text(types=None))
        self.assertIn("客队近期大小球率缺失", section.get_text(types=None))
        self.assertNotIn("100%", section.get_text(types=None))

    def test_missing_or_invalid_data_does_not_invent_reference(self):
        for scores, total in (
            ((), {"available": True, "initial": {"line": 2.5}}),
            (("2:2", "2:1"), {"available": False}),
            (("2:2", "2:1"), {"available": True, "initial": {"line": "nan"}}),
            (("2:2", "2:1"), {"available": True, "initial": {"line_text": "无数据"}}),
        ):
            with self.subTest(scores=scores, total=total):
                row = _row(scores=scores)
                payload = json.loads(row["payload_json"])
                payload["odds_movement"]["total"] = total
                row["payload_json"] = json.dumps(payload)
                _, _, section = _view(row)
                self.assertIsNotNone(section)
                self.assertFalse(section.select("strong"))

    def test_original_tendencies_and_data_remain_unchanged(self):
        row = _row()
        read = json.loads(row["team_read"])
        read["over_reason"] = "原模型理由：结合即时盘判断大球"
        row["team_read"] = json.dumps(read, ensure_ascii=False)
        original = copy.deepcopy(row)
        soup, popup, _ = _view(row)
        self.assertEqual(row, original)
        self.assertEqual(popup.select_one(".ai-tip-model-reason").get_text(), read["over_reason"])
        handicap = soup.select(".model-lean-tip")[0].select_one("template")
        self.assertNotIn("盘口视角", str(handicap))
        for template in soup.select("template"):
            template.decompose()
        self.assertEqual(soup.get_text(" ", strip=True), "主队赢盘 60% 大球 68%")


if __name__ == "__main__":
    unittest.main()
