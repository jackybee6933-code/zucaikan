import json
import tempfile
import unittest
from pathlib import Path

from football_analysis.odds_pages import (
    build_pages_movements,
    load_odds_pages,
    page_failures,
    parse_odds_pages,
)
from football_analysis.pipeline import _build_single_analysis
from football_analysis.query_report import _split_html
from football_analysis.report import asian_display, total_display
from football_analysis.store import _extract_split


def _raw(rows_by_key: dict) -> dict:
    return {key: {"rows": rows} for key, rows in rows_by_key.items()}


ASIAN_ROWS = [
    ["5", "1.000", "一球", "0.840", "1.000", "一球", "0.840"],
    ["293", "0.730", "两球", "1.000", "0.910", "两球", "0.800"],
]

TOTAL_ROWS = [["5", "1.25", "3/3.5", "0.75", "0.75", "2.5", "1.00"]]


class OddsPagesTests(unittest.TestCase):
    def test_prefers_macau_cid_5(self):
        parsed = parse_odds_pages(_raw({"9|yazhi": ASIAN_ROWS}))
        asian = parsed["9"]
        self.assertEqual(asian["asian_line_text"], "一球")
        self.assertEqual(asian["asian_home_line"], -1.0)
        self.assertEqual(asian["asian_home_water"], 1.0)
        self.assertEqual(asian["asian_away_water"], 0.84)
        self.assertEqual(asian["asian_company"], "澳门")

    def test_falls_back_to_william_when_macau_missing(self):
        parsed = parse_odds_pages(_raw({"9|yazhi": [ASIAN_ROWS[1]]}))
        self.assertEqual(parsed["9"]["asian_line_text"], "两球")
        self.assertEqual(parsed["9"]["asian_company"], "威廉")

    def test_source_label_keeps_real_name_for_new_rows(self):
        rows = [["5", "*门", "1.000", "一球", "0.840", "1.000", "一球", "0.840"]]
        parsed = parse_odds_pages(_raw({"9|yazhi": rows}))
        self.assertEqual(parsed["9"]["asian_company"], "澳门")

    def test_strips_direction_markers_from_line(self):
        rows = [["293", "0.53↓", "2.5↓", "1.40↑", "1.38", "3.5", "0.55"]]
        parsed = parse_odds_pages(_raw({"9|daxiao": rows}))
        self.assertEqual(parsed["9"]["total_line_text"], "2.5")
        self.assertEqual(parsed["9"]["over_water"], 0.53)
        self.assertEqual(parsed["9"]["under_water"], 1.40)

    def test_strips_rise_fall_marker_from_handicap(self):
        rows = [["293", "0.910", "一球/球半 升", "0.800", "0.800", "半球/一球", "0.910"]]
        parsed = parse_odds_pages(_raw({"9|yazhi": rows}))
        self.assertEqual(parsed["9"]["asian_line_text"], "一球/球半")
        self.assertEqual(parsed["9"]["asian_home_line"], -1.25)

    def test_total_line_conversion(self):
        parsed = parse_odds_pages(_raw({"9|daxiao": TOTAL_ROWS}))
        total = parsed["9"]
        self.assertEqual(total["total_line_text"], "3/3.5")
        self.assertEqual(total["total_line"], 3.25)
        self.assertEqual(total["total_company"], "澳门")

    def test_skips_pages_without_company_rows(self):
        parsed = parse_odds_pages(_raw({"1|yazhi": [], "2|daxiao": []}))
        self.assertEqual(parsed, {})

    def test_merges_asian_and_total_for_one_match(self):
        parsed = parse_odds_pages(_raw({"9|yazhi": ASIAN_ROWS, "9|daxiao": TOTAL_ROWS}))
        self.assertEqual(set(parsed["9"]), {
            "asian_line_text", "asian_home_line", "asian_line_display",
            "asian_home_water", "asian_away_water", "asian_company",
            "total_line_text", "total_line", "over_water", "under_water",
            "total_company",
        })

    def test_build_single_analysis_uses_pages_data(self):
        parsed = parse_odds_pages(_raw({"9|yazhi": ASIAN_ROWS, "9|daxiao": TOTAL_ROWS}))
        row = {"match_no": "9", "league": "欧国联", "home_name": "A", "away_name": "B"}
        analysis = _build_single_analysis(row, parsed["9"], home_record={}, away_record={})
        self.assertTrue(analysis["market"]["has_asian"])
        self.assertTrue(analysis["market"]["has_total"])
        self.assertEqual(asian_display(analysis), "一球 1.00/0.84（澳门）")
        self.assertEqual(total_display(analysis), "3/3.5 1.25/0.75（澳门）")

    def test_load_reads_expected_filename(self):
        payload = _raw({"9|yazhi": ASIAN_ROWS})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_pages_26135.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            parsed = load_odds_pages(tmp, "26135")
            self.assertIn("9", parsed)
            self.assertEqual(load_odds_pages(tmp, "26134"), {})

    def test_load_handles_missing_file_and_bad_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_odds_pages(tmp, "26135"), {})
            (Path(tmp) / "odds_pages_26135.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(load_odds_pages(tmp, "26135"), {})
        self.assertEqual(load_odds_pages(None, "26135"), {})

class PagesMovementTests(unittest.TestCase):
    """单场页的初盘/即时盘要能直接喂给异动检测。"""

    def test_detects_handicap_rise(self):
        rows = [["293", "0.700", "半球/一球", "0.910", "0.800", "平手/半球", "0.910"]]
        mv = build_pages_movements(_raw({"9|yazhi": rows}))["9"]
        self.assertTrue(mv["asian"]["available"])
        self.assertTrue(mv["attention"])
        self.assertEqual(mv["kind"], "升盘")
        # 0.25 -> 0.75 跨过 ASIAN_LINE_BIG=0.5，因此是重点关注而非关注
        self.assertEqual(mv["level"], "重点关注")
        self.assertIn("升盘", mv["reasons"][0])

    def test_detects_total_drop(self):
        rows = [["293", "0.53↓", "2.5↓", "1.40↑", "1.38", "3.5", "0.55"]]
        mv = build_pages_movements(_raw({"9|daxiao": rows}))["9"]
        self.assertTrue(mv["total"]["available"])
        self.assertEqual(mv["kind"], "大盘降")
        self.assertEqual(mv["level"], "重点关注")

    def test_identical_lines_are_stable(self):
        rows = [["293", "1.000", "一球", "0.840", "1.000", "一球", "0.840"]]
        mv = build_pages_movements(_raw({"9|yazhi": rows}))["9"]
        self.assertTrue(mv["asian"]["available"])
        self.assertFalse(mv["attention"])
        self.assertEqual(mv["level"], "稳定")

    def test_line_display_uses_home_perspective(self):
        rows = [["293", "0.670", "受半球", "1.100", "0.750", "受半球", "1.000"]]
        mv = build_pages_movements(_raw({"9|yazhi": rows}))["9"]
        self.assertEqual(mv["asian"]["current"]["line"], 0.5)
        self.assertEqual(mv["asian"]["current"]["line_display"], "主受半球")

    def test_merges_asian_and_total_for_one_fixture(self):
        raw = _raw({
            "9|yazhi": [["293", "0.700", "半球/一球", "0.910", "0.800", "平手/半球", "0.910"]],
            "9|daxiao": [["293", "1.30", "2.5", "0.57", "1.30", "2.5", "0.57"]],
        })
        mv = build_pages_movements(raw)["9"]
        self.assertTrue(mv["asian"]["available"])
        self.assertTrue(mv["total"]["available"])

    def test_skips_pages_without_rows(self):
        self.assertEqual(build_pages_movements(_raw({"9|yazhi": []})), {})
        self.assertEqual(build_pages_movements({}), {})

    def test_passes_team_names_for_lean_text(self):
        rows = [["293", "0.700", "半球/一球", "0.910", "0.800", "平手/半球", "0.910"]]
        raw = _raw({"9|yazhi": rows})
        teams = {"9": {"home": "罗马尼亚", "away": "瑞典"}}
        mv = build_pages_movements(raw, teams)["9"]
        # 主让盘升盘 = 主队让得更多，倾向主队赢盘
        self.assertEqual(mv["asian"]["lean"], "罗马尼亚")


class PagesSplitTests(unittest.TestCase):
    """公司间盘口分歧 >= 0.5 时产出弹层数据块。"""

    ROWS = [
        ["293", "0.53↓", "2.5↓", "1.40↑", "1.38", "3.5", "0.55"],
        ["4", "1.35↑", "3.5", "0.55↓", "1.30", "3.5", "0.57"],
        ["6", "0.88", "3", "0.91", "0.88", "3", "0.91"],
    ]

    def test_flags_total_line_spread(self):
        blocks = build_pages_movements(_raw({"9|daxiao": self.ROWS}))["9"]["split"]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block["market"], "大小球")
        self.assertEqual(block["picked"], "293")
        self.assertEqual(block["rows"][0], {
            "cid": "293", "name": "威廉", "initial": "3.5", "current": "2.5",
        })

    def test_keeps_every_company_in_page_order(self):
        blocks = build_pages_movements(_raw({"9|daxiao": self.ROWS}))["9"]["split"]
        self.assertEqual([r["cid"] for r in blocks[0]["rows"]], ["293", "4", "6"])

    def test_unknown_company_falls_back_to_cid(self):
        rows = self.ROWS + [["9999", "0.83", "3", "0.89", "1.33", "3.5", "0.51"]]
        blocks = build_pages_movements(_raw({"9|daxiao": rows}))["9"]["split"]
        last = blocks[0]["rows"][-1]
        self.assertEqual(last["cid"], "9999")
        self.assertEqual(last["name"], "")

    def test_old_rows_use_company_name_table(self):
        rows = self.ROWS + [["1484", "0.83", "3", "0.89", "1.33", "3.5", "0.51"]]
        blocks = build_pages_movements(_raw({"9|daxiao": rows}))["9"]["split"]
        last = blocks[0]["rows"][-1]
        self.assertEqual(last["cid"], "1484")
        self.assertEqual(last["name"], "o***")

    def test_new_rows_keep_page_company_name(self):
        rows = [
            ["293", "威***", "0.53", "2.5", "1.40", "1.38", "3.5", "0.55"],
            ["1484", "o***", "1.35", "3.5", "0.55", "1.30", "3.5", "0.57"],
        ]
        blocks = build_pages_movements(_raw({"9|daxiao": rows}))["9"]["split"]
        self.assertEqual(
            [(r["cid"], r["name"]) for r in blocks[0]["rows"]],
            [("293", "威***"), ("1484", "o***")],
        )

    def test_no_split_when_companies_agree(self):
        rows = [
            ["293", "1.00", "2.5", "0.90", "1.00", "2.5", "0.90"],
            ["4", "0.95", "2.5", "0.95", "0.95", "2.5", "0.95"],
        ]
        self.assertNotIn("split", build_pages_movements(_raw({"9|daxiao": rows}))["9"])

    def test_no_split_with_single_company(self):
        mv = build_pages_movements(_raw({"9|daxiao": [self.ROWS[0]]}))["9"]
        self.assertNotIn("split", mv)

    def test_no_split_when_all_unchanged(self):
        """初盘与即时完全一致时，即使各公司盘口有差异也不挂分歧。"""
        rows = [
            ["293", "0.90", "2.5", "1.00", "0.90", "2.5", "1.00"],
            ["4", "0.85", "3", "1.05", "0.85", "3", "1.05"],
        ]
        mv = build_pages_movements(_raw({"9|daxiao": rows}))["9"]
        self.assertNotIn("split", mv)

    def test_split_kept_when_some_company_unchanged(self):
        """只要有任何一家初盘→即时盘口有变化，分歧照常展示。"""
        rows = [
            ["293", "0.90", "2.5", "1.00", "0.90", "2.5", "1.00"],
            ["4", "0.85", "3", "1.05", "0.85", "4", "1.05"],
        ]
        mv = build_pages_movements(_raw({"9|daxiao": rows}))["9"]
        self.assertIn("split", mv)

    def test_split_below_threshold_is_ignored(self):
        rows = [
            ["293", "1.00", "2.5", "0.90", "1.00", "2.5", "0.90"],
            ["4", "1.00", "2.75", "0.90", "1.00", "2.75", "0.90"],
        ]
        mv = build_pages_movements(_raw({"9|daxiao": rows}))["9"]
        self.assertNotIn("split", mv)

    def test_even_handicap_counts_as_zero(self):
        """平手解析成 0.0，与半球相差 0.5，属于真实分歧。"""
        rows = [
            ["293", "0.90", "平手", "1.00", "0.90", "受半球", "1.00"],
            ["4", "0.85", "半球", "1.05", "0.85", "半球", "1.05"],
        ]
        blocks = build_pages_movements(_raw({"9|yazhi": rows}))["9"]["split"]
        self.assertEqual([r["current"] for r in blocks[0]["rows"]], ["平手", "半球"])

    def test_empty_line_is_skipped_not_zero(self):
        """空缺的即时盘口不能当成平手参与跨度。"""
        rows = [
            ["293", "0.90", "平手", "1.00", "0.90", "平手/半球", "1.00"],
            ["4", "0.85", "半球", "1.05", "0.85", "半球", "1.05"],
            ["6", "0.85", "", "1.05", "0.85", "", "1.05"],
        ]
        blocks = build_pages_movements(_raw({"9|yazhi": rows}))["9"]["split"]
        self.assertEqual([r["cid"] for r in blocks[0]["rows"]], ["293", "4"])

    def test_handicap_spread_uses_signed_lines(self):
        # 主让两球(-2.0) 与 主让两球半(-2.5) 相差 0.5，应判定为分歧
        rows = [
            ["293", "0.70", "让两球", "0.95", "0.70", "让两球半", "0.95"],
            ["4", "0.70", "让两球半", "0.95", "0.70", "让两球半", "0.95"],
        ]
        blocks = build_pages_movements(_raw({"9|yazhi": rows}))["9"]["split"]
        self.assertEqual(blocks[0]["market"], "亚盘")
        self.assertEqual(blocks[0]["rows"][1]["current"], "让两球半")

    def test_one_block_per_market(self):
        raw = _raw({"9|daxiao": self.ROWS})
        raw["9|yazhi"] = {"rows": [
            ["293", "0.70", "让两球", "0.95", "0.70", "让两球半", "0.95"],
            ["4", "0.70", "让两球半", "0.95", "0.70", "让两球半", "0.95"],
        ]}
        blocks = build_pages_movements(raw)["9"]["split"]
        self.assertEqual(len(blocks), 2)
        self.assertEqual({b["market"] for b in blocks}, {"亚盘", "大小球"})


class SplitRenderTests(unittest.TestCase):
    BLOCK = {"market": "大小球", "picked": "293", "rows": [
        {"cid": "293", "name": "威廉", "initial": "3.5", "current": "2.5"},
        {"cid": "1484", "name": "", "initial": "3.5", "current": "3"},
    ]}

    def test_extract_split_from_payload(self):
        payload = json.dumps({"odds_movement": {"split": [self.BLOCK]}})
        self.assertEqual(_extract_split(payload), [self.BLOCK])

    def test_extract_split_tolerates_bad_payload(self):
        self.assertEqual(_extract_split(""), [])
        self.assertEqual(_extract_split("{broken"), [])
        self.assertEqual(_extract_split(json.dumps({"odds_movement": {}})), [])
        self.assertEqual(_extract_split(json.dumps({"odds_movement": {"split": ["x"]}})), [])

    def test_html_renders_label_and_template(self):
        html = _split_html({"split": [self.BLOCK]}, "sp-1")
        self.assertIn(">盘口分歧</div>", html)
        self.assertIn('data-split-for="sp-1"', html)
        self.assertIn('<template id="sp-1">', html)
        self.assertIn("威廉(293)", html)
        self.assertIn("1484", html)
        self.assertIn("3.5 \u2192 ", html)

    def test_html_bolds_the_picked_company(self):
        html = _split_html({"split": [self.BLOCK]}, "sp-2")
        self.assertEqual(html.count("<b>"), 1)
        self.assertIn("<b>2.5</b>", html)

    def test_html_falls_back_to_company_name_table(self):
        html = _split_html({"split": [self.BLOCK]}, "sp-6")
        self.assertIn("o***(1484)", html)

    def test_html_renders_each_market_block(self):
        blocks = [
            self.BLOCK,
            {"market": "亚盘", "picked": "293", "rows": [
                {"cid": "293", "name": "威廉", "initial": "让两球", "current": "让两球半"},
            ]},
        ]
        html = _split_html({"split": blocks}, "sp-3")
        self.assertIn("大小球", html)
        self.assertIn("亚盘", html)
        self.assertEqual(html.count("split-card-title"), 2)
        self.assertIn('class="split-cards"', html)

    def test_html_escapes_cell_text(self):
        evil = {"market": "x", "picked": "1", "rows": [
            {"cid": "1", "name": '<img src=x onerror="alert(1)">',
             "initial": "2.5", "current": "2.5"},
        ]}
        html = _split_html({"split": [evil]}, "sp-4")
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_html_empty_without_blocks(self):
        self.assertEqual(_split_html({}, "sp-5"), "")
        self.assertEqual(_split_html({"split": []}, "sp-5"), "")
        self.assertEqual(_split_html({"split": [{"market": "亚盘", "picked": "", "rows": []}]}, "sp-5"), "")
        self.assertEqual(_split_html({"split": ["x"]}, "sp-5"), "")



class PageStatusTests(unittest.TestCase):
    """抓取失败（timeout/error）与「上游没开盘」必须可区分。"""

    ROWS = [["293", "0.67", "受半球", "1.10", "0.75", "受半球", "1.00"]]

    def test_timeout_page_is_a_failure_not_empty(self):
        raw = {"9|yazhi": {"status": "timeout", "rows": []}}
        self.assertEqual(page_failures(raw), ["9|yazhi"])
        self.assertEqual(parse_odds_pages(raw), {})

    def test_error_page_is_a_failure(self):
        raw = {"9|yazhi": {"status": "error", "rows": [], "detail": "boom"}}
        self.assertEqual(page_failures(raw), ["9|yazhi"])

    def test_empty_page_is_not_a_failure(self):
        raw = {"9|yazhi": {"status": "empty", "rows": []}}
        self.assertEqual(page_failures(raw), [])
        self.assertEqual(parse_odds_pages(raw), {})

    def test_ok_page_is_not_a_failure(self):
        raw = {"9|yazhi": {"status": "ok", "rows": self.ROWS}}
        self.assertEqual(page_failures(raw), [])
        self.assertIn("9", parse_odds_pages(raw))

    def test_failed_page_is_ignored_even_with_rows(self):
        """防御性：失败页即便混进了残留行，也不能当成有效盘口。"""
        raw = {"9|yazhi": {"status": "timeout", "rows": self.ROWS}}
        self.assertEqual(parse_odds_pages(raw), {})
        self.assertEqual(page_failures(raw), ["9|yazhi"])

    def test_legacy_file_without_status_infers_from_rows(self):
        """向后兼容：旧产物没有 status 字段，按有无公司行推断。"""
        good = {"9|yazhi": {"rows": self.ROWS}}
        blank = {"9|daxiao": {"rows": []}}
        self.assertEqual(page_failures({**good, **blank}), [])
        self.assertIn("9", parse_odds_pages({**good, **blank}))

    def test_mixed_file_reports_only_failures(self):
        raw = {
            "9|yazhi": {"status": "ok", "rows": self.ROWS},
            "9|daxiao": {"status": "timeout", "rows": []},
            "8|yazhi": {"status": "empty", "rows": []},
            "8|daxiao": {"status": "error", "rows": []},
        }
        self.assertEqual(sorted(page_failures(raw)), ["8|daxiao", "9|daxiao"])
        parsed = parse_odds_pages(raw)
        self.assertEqual(set(parsed), {"9"})
        self.assertEqual(parsed["9"]["asian_line_text"], "受半球")

if __name__ == "__main__":
    unittest.main()
