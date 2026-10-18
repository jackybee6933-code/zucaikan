import unittest
from unittest import mock

from football_analysis import pipeline
from football_analysis.odds import (
    nowscore_handicap_numeric,
    nowscore_handicap_to_home_line,
)
from football_analysis.odds_movement import (
    analyze_prematch_movement,
    parse_prematch_movement_html,
)


PREMATCH_HTML = """
<div class="fenxiBar">赛前指数</div>
<div class="contentBox">
  <table class="mytable">
    <tr onclick="GoUrl(4,0)">
      <th>欧</th>
      <td class="firstOdds">1.70</td><td class="firstOdds">4.30</td><td class="firstOdds">4.55</td>
      <td>1.79</td><td>4.05</td><td>4.25</td>
    </tr>
    <tr onclick="GoUrl(1,0)">
      <th>亚</th>
      <td class="firstOdds">0.89</td><td class="firstOdds">0.5/1</td><td class="firstOdds">0.99</td>
      <td>0.96</td><td>0.5/1</td><td>0.93</td>
    </tr>
    <tr onclick="GoUrl(2,0)">
      <th>大</th>
      <td class="firstOdds">0.83</td><td class="firstOdds">2.5/3</td><td class="firstOdds">1.03</td>
      <td>1.06</td><td>3/3.5</td><td>0.82</td>
    </tr>
  </table>
</div>
"""


class NowscoreLineTests(unittest.TestCase):
    def test_signed_line_conversion(self):
        self.assertAlmostEqual(nowscore_handicap_numeric("0.5/1"), 0.75)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("0.5/1"), -0.75)
        self.assertAlmostEqual(nowscore_handicap_numeric("-0.5/1"), -0.75)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("-0.5/1"), 0.75)
        self.assertAlmostEqual(nowscore_handicap_numeric("0/-0.5"), -0.25)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("0/-0.5"), 0.25)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("-1"), 1.0)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("1"), -1.0)
        self.assertAlmostEqual(nowscore_handicap_to_home_line("0"), 0.0)


class PrematchMovementTests(unittest.TestCase):
    def test_parses_first_and_current_values(self):
        raw = parse_prematch_movement_html(PREMATCH_HTML)
        self.assertTrue(raw["found"])
        self.assertEqual(raw["asian"]["initial"]["line_text"], "0.5/1")
        self.assertEqual(raw["asian"]["current"]["line_text"], "0.5/1")
        self.assertAlmostEqual(raw["asian"]["initial"]["home_water"], 0.89)
        self.assertAlmostEqual(raw["asian"]["current"]["away_water"], 0.93)
        self.assertAlmostEqual(raw["total"]["initial"]["line"], 2.75)
        self.assertAlmostEqual(raw["total"]["current"]["line"], 3.25)
        self.assertAlmostEqual(raw["europe"]["initial"]["home"], 1.70)
        self.assertAlmostEqual(raw["europe"]["current"]["away"], 4.25)

    def test_analyze_keeps_total_snapshot(self):
        raw = parse_prematch_movement_html(PREMATCH_HTML)
        movement = analyze_prematch_movement(raw)
        self.assertEqual(movement["total"]["initial"]["line_text"], "2.5/3")
        self.assertEqual(movement["total"]["current"]["line_text"], "3/3.5")
        self.assertAlmostEqual(movement["total"]["current"]["over_water"], 1.06)
        self.assertAlmostEqual(movement["total"]["current"]["under_water"], 0.82)
        self.assertAlmostEqual(movement["total"]["initial"]["line"], 2.75)
        self.assertAlmostEqual(movement["total"]["current"]["line"], 3.25)
        self.assertAlmostEqual(movement["total"]["line_change"], 0.5)
        self.assertEqual(movement["total"]["kind"], "大小盘升")
        self.assertEqual(movement["total"]["level"], "重点关注")
        self.assertTrue(movement["attention"])
        self.assertIn(movement["total"]["note"], movement["reasons"])

    def test_flags_asian_water_reversal(self):
        movement = analyze_prematch_movement(parse_prematch_movement_html(PREMATCH_HTML))
        self.assertTrue(movement["attention"])
        # PREMATCH_HTML has both asian water reversal and total line rise, level = 重点关注
        self.assertEqual(movement["level"], "重点关注")
        self.assertEqual(movement["asian"]["level"], "关注")
        self.assertIn("水位反转", movement["asian"]["note"])
        self.assertEqual(movement["asian"]["kind"], "水位反转")
        self.assertEqual(movement["total"]["kind"], "大小盘升")
        self.assertEqual(movement["kind"], "水位反转")

    def test_home_favorite_drop_is_downgrade(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(1,0)">
            <th>亚</th>
            <td>0.84</td><td>1</td><td>1.04</td>
            <td>0.98</td><td>0/0.5</td><td>0.88</td>
          </tr>
        </table></div>
        """
        raw = parse_prematch_movement_html(html)
        raw["teams"] = {"home": "主队B", "away": "客队A"}
        movement = analyze_prematch_movement(raw)
        self.assertEqual(movement["level"], "重点关注")
        self.assertEqual(movement["asian"]["direction"], "降盘")
        self.assertEqual(movement["asian"]["kind"], "降盘")
        self.assertEqual(movement["kind"], "降盘")
        self.assertEqual(movement["asian"]["lean"], "客队A")
        self.assertIn("倾向客队A赢盘", movement["asian"]["note"])
        self.assertNotIn("退盘", movement["asian"]["note"])

    def test_home_favorite_increase_is_upgrade(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(1,0)">
            <th>亚</th>
            <td>0.98</td><td>0/0.5</td><td>0.88</td>
            <td>0.84</td><td>1</td><td>1.04</td>
          </tr>
        </table></div>
        """
        raw = parse_prematch_movement_html(html)
        raw["teams"] = {"home": "主队B", "away": "客队A"}
        movement = analyze_prematch_movement(raw)
        self.assertEqual(movement["level"], "重点关注")
        self.assertEqual(movement["asian"]["direction"], "升盘")
        self.assertEqual(movement["asian"]["kind"], "升盘")
        self.assertEqual(movement["kind"], "升盘")
        self.assertEqual(movement["asian"]["lean"], "主队B")
        self.assertIn("倾向主队B赢盘", movement["asian"]["note"])

    def test_away_favorite_increase_is_upgrade(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(1,0)">
            <th>亚</th>
            <td>0.98</td><td>-0.5</td><td>0.90</td>
            <td>1.04</td><td>-1</td><td>0.84</td>
          </tr>
        </table></div>
        """
        raw = parse_prematch_movement_html(html)
        raw["teams"] = {"home": "主队B", "away": "客队A"}
        movement = analyze_prematch_movement(raw)
        self.assertEqual(movement["level"], "重点关注")
        self.assertEqual(movement["asian"]["direction"], "升盘")
        self.assertEqual(movement["asian"]["kind"], "升盘")
        self.assertEqual(movement["kind"], "升盘")
        self.assertEqual(movement["asian"]["lean"], "客队A")
        self.assertIn("倾向客队A赢盘", movement["asian"]["note"])
        self.assertNotIn("退盘", movement["asian"]["note"])

    def test_away_favorite_drop_is_downgrade(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(1,0)">
            <th>亚</th>
            <td>1.04</td><td>-1</td><td>0.84</td>
            <td>0.98</td><td>-0.5</td><td>0.90</td>
          </tr>
        </table></div>
        """
        raw = parse_prematch_movement_html(html)
        raw["teams"] = {"home": "主队B", "away": "客队A"}
        movement = analyze_prematch_movement(raw)
        self.assertEqual(movement["level"], "重点关注")
        self.assertEqual(movement["asian"]["direction"], "降盘")
        self.assertEqual(movement["asian"]["kind"], "降盘")
        self.assertEqual(movement["kind"], "降盘")
        self.assertEqual(movement["asian"]["lean"], "主队B")
        self.assertIn("倾向主队B赢盘", movement["asian"]["note"])

    def test_unchanged_odds_are_stable(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(1,0)">
            <th>亚</th>
            <td>0.90</td><td>0.5/1</td><td>0.95</td>
            <td>0.91</td><td>0.5/1</td><td>0.94</td>
          </tr>
          <tr onclick="GoUrl(2,0)">
            <th>大</th>
            <td>0.90</td><td>2.5</td><td>0.95</td>
            <td>0.91</td><td>2.5</td><td>0.94</td>
          </tr>
        </table></div>
        """
        movement = analyze_prematch_movement(parse_prematch_movement_html(html))
        self.assertFalse(movement["attention"])
        self.assertEqual(movement["level"], "稳定")

    def test_total_line_change_triggers_alert(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(2,0)">
            <th>大</th>
            <td>0.90</td><td>3</td><td>0.95</td>
            <td>0.91</td><td>3/3.5</td><td>0.94</td>
          </tr>
        </table></div>
        """
        movement = analyze_prematch_movement(parse_prematch_movement_html(html))
        self.assertTrue(movement["total_available"])
        self.assertTrue(movement["attention"])
        self.assertEqual(movement["total"]["kind"], "大小盘升")

    def test_europe_change_is_not_an_alert(self):
        html = """
        <div class="fenxiBar">赛前指数</div>
        <div class="contentBox"><table>
          <tr onclick="GoUrl(4,0)">
            <th>欧</th>
            <td>1.40</td><td>4.50</td><td>7.00</td>
            <td>1.75</td><td>3.60</td><td>5.00</td>
          </tr>
        </table></div>
        """
        movement = analyze_prematch_movement(parse_prematch_movement_html(html))
        self.assertFalse(movement["available"])
        self.assertFalse(movement["attention"])
        self.assertEqual(movement["level"], "稳定")


class PagesSplitRetentionTests(unittest.TestCase):
    """单场页分歧不得因采用 Nowscore 盘口而丢失。"""

    # [cid, 即时主水, 即时盘, 即时客水, 初主水, 初盘, 初客水]
    SPLIT_PAGES = {
        "9|yazhi": {
            "status": "ok",
            "rows": [
                ["5", "0.90", "半球", "0.96", "0.88", "平手", "0.98"],
                ["293", "0.95", "一球", "0.95", "0.90", "半球/一球", "0.92"],
            ],
        }
    }

    AGREED_PAGES = {
        "9|yazhi": {
            "status": "ok",
            "rows": [
                ["5", "0.90", "半球", "0.96", "0.88", "半球", "0.98"],
                ["293", "0.95", "半球", "0.95", "0.90", "半球", "0.92"],
            ],
        }
    }

    def _rows(self):
        return [
            {
                "fixture_id": "9",
                "titan_match_id": "3001",
                "home_name": "home",
                "away_name": "away",
            }
        ]

    def _run(self, pages_raw, nowscore):
        rows = self._rows()
        with mock.patch.object(pipeline, "fetch_nowscore_movement", return_value=nowscore):
            pipeline._attach_odds_movement(
                rows,
                fetcher=mock.MagicMock(),
                max_workers=1,
                pages_raw=pages_raw,
            )
        return rows[0]["odds_movement"]

    def test_split_kept_when_nowscore_covers_asian(self):
        nowscore = {
            "available": True,
            "source": "nowscore_prematch",
            "asian": {"available": True, "initial": {}, "current": {}},
            "total": {"available": False},
            "attention": True,
            "level": "关注",
            "kind": "升盘",
            "reasons": ["让球盘升盘"],
        }
        movement = self._run(self.SPLIT_PAGES, nowscore)
        # Nowscore 盘口保留，不被单场页覆盖
        self.assertEqual(movement["source"], "nowscore_prematch")
        self.assertTrue(movement["asian"]["available"])
        self.assertEqual(movement["titan_match_id"], "3001")
        # 单场页各公司分歧仍保留
        self.assertEqual(len(movement["split"]), 1)
        self.assertEqual(movement["split"][0]["market"], "亚盘")
        self.assertEqual(len(movement["split"][0]["rows"]), 2)

    def test_no_split_added_when_companies_agree(self):
        nowscore = {
            "available": True,
            "source": "nowscore_prematch",
            "asian": {"available": True, "initial": {}, "current": {}},
            "total": {"available": False},
            "attention": False,
            "level": "稳定",
            "kind": "",
            "reasons": [],
        }
        movement = self._run(self.AGREED_PAGES, nowscore)
        self.assertNotIn("split", movement)

    def test_pages_take_over_when_nowscore_missing_asian(self):
        nowscore = {
            "available": False,
            "source": "nowscore_prematch",
            "asian": {"available": False},
            "total": {"available": False},
            "attention": False,
            "level": "无数据",
            "kind": "",
            "reasons": [],
        }
        movement = self._run(self.SPLIT_PAGES, nowscore)
        # 单场页整体接管，盘口与分歧都来自单场页
        self.assertTrue(movement["asian"]["available"])
        self.assertEqual(movement["split"][0]["market"], "亚盘")

class PagesFallbackWithoutTitanIdTests(unittest.TestCase):
    """26143 期根因复现：jczq 失效（Titan 赛程无映射）时整期 14 场
    titan_match_id 全空，旧逻辑直接 return，单场页盘口从未进入 movements，
    查询页显示无数据。现在 titan_match_id 缺失不影响单场页兜底。"""

    PAGES = {
        "9|yazhi": {
            "status": "ok",
            "rows": [
                ["5", "0.90", "半球", "0.96", "0.88", "半球", "0.98"],
            ],
        }
    }

    def test_pages_still_attached_when_row_has_no_titan_id(self):
        rows = [
            {"fixture_id": "9", "home_name": "home", "away_name": "away"},
            # 无 titan_match_id（jczq 失效场景），不能整块跳过单场页
        ]
        with mock.patch.object(
            pipeline, "fetch_nowscore_movement", return_value={"available": False}
        ):
            pipeline._attach_odds_movement(
                rows,
                fetcher=mock.MagicMock(),
                max_workers=1,
                pages_raw=self.PAGES,
            )
        movement = rows[0].get("odds_movement")
        self.assertIsNotNone(movement, "无 titan_match_id 时单场页盘口也必须挂上")
        self.assertTrue(movement["asian"]["available"])

    def test_nothing_attached_without_pages_or_titan_id(self):
        """既无 titan_match_id 也无单场页产物时，维持旧行为：什么都不挂。"""
        rows = [{"fixture_id": "9", "home_name": "home", "away_name": "away"}]
        with mock.patch.object(
            pipeline, "fetch_nowscore_movement", return_value={"available": False}
        ):
            pipeline._attach_odds_movement(
                rows,
                fetcher=mock.MagicMock(),
                max_workers=1,
                pages_raw=None,
            )
        self.assertNotIn("odds_movement", rows[0])
if __name__ == "__main__":
    unittest.main()
