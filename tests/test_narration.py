import unittest
import os
from unittest import mock

from football_analysis.browser_report import render_browser_html
from football_analysis.llm import MovementNarrator, build_prompt, narrate_movements


def _analysis(*, attention: bool = True) -> dict:
    return {
        "match_no": "3",
        "league": "英超",
        "match_time": "10-01 03:00",
        "home_team": "主队",
        "away_team": "客队",
        "prediction": {
            "final_probabilities": {"home": 0.55, "draw": 0.25, "away": 0.20},
            "model_direction": "主胜",
            "market_direction": "主胜",
        },
        "odds_analysis": {
            "asian_handicap": {
                "available": True,
                "recommended_side": "主队赢盘",
                "action": "轻仓",
                "confidence": 0.62,
            }
        },
        "history": {
            "home": {
                "matches": 10, "wins": 6, "draws": 2, "losses": 2,
                "goals_for": 18, "goals_against": 9,
                "rows": [
                    {"date": "09-25", "home": "\u4e3b\u961f", "away": "\u5bf9\u624bA", "score": "2-1", "league": "\u82f1\u8d85"},
                    {"date": "09-18", "home": "\u5bf9\u624bB", "away": "\u4e3b\u961f", "score": "0-0", "league": "\u82f1\u8d85"},
                ],
            },
            "away": {
                "matches": 10, "wins": 3, "draws": 3, "losses": 4,
                "goals_for": 11, "goals_against": 14,
                "rows": [
                    {"date": "09-24", "home": "\u5ba2\u961f", "away": "\u5bf9\u624bC", "score": "1-3", "league": "\u82f1\u8d85"},
                ],
            },
            "h2h": {
                "total": 6, "home_wins": 4, "draws": 1, "away_wins": 1,
                "goals_for": 12, "goals_against": 6,
                "rows": [
                    {"date": "2025-04-01", "home": "\u4e3b\u961f", "away": "\u5ba2\u961f", "score": "3-1", "league": "\u82f1\u8d85"},
                    {"date": "2024-10-05", "home": "\u5ba2\u961f", "away": "\u4e3b\u961f", "score": "1-2", "league": "\u82f1\u8d85"},
                ],
            },
        },
        "odds_movement": {
            "available": True,
            "attention": attention,
            "level": "重点关注",
            "kind": "升盘",
            "reasons": ["让球盘升盘：主让0.5 -> 主让0.75，倾向主队赢盘"],
            "asian": {
                "available": True,
                "initial": {
                    "line_display": "主让0.5", "home_water": 0.90, "away_water": 0.96,
                },
                "current": {
                    "line_display": "主让0.75", "home_water": 0.92, "away_water": 0.94,
                },
                "lean": "主队",
            },
            "total": {
                "available": True,
                "initial": {"line_text": "2.5", "over_water": 0.88, "under_water": 0.98},
                "current": {"line_text": "2.5", "over_water": 0.90, "under_water": 0.96},
            },
        },
        "warnings": [],
    }


class _FakeNarrator(MovementNarrator):
    def __init__(self):
        super().__init__(api_key="fake")

    def narrate(self, analysis: dict) -> str:
        return f"解读：{analysis.get('home_team')}盘口异动"


class BuildPromptTest(unittest.TestCase):
    def test_contains_key_facts(self):
        prompt = build_prompt(_analysis())
        self.assertIn("主让0.75", prompt)
        self.assertIn("重点关注", prompt)
        self.assertIn("主队赢盘", prompt)
        self.assertIn("55%", prompt)
        self.assertIn("只解读，不建议", prompt)

    def test_handles_missing_movement(self):
        prompt = build_prompt({})
        self.assertIn("联赛：未知", prompt)
        self.assertIn("无数据", prompt)


    def test_includes_history_detail(self):
        prompt = build_prompt(_analysis())
        self.assertIn("\u4e3b\u961f\u8fd1", prompt)
        self.assertIn("\u5ba2\u961f\u8fd1", prompt)
        self.assertIn("\u5386\u53f2\u4ea4\u950b(\u8fd1\u671f\u4f18\u5148)", prompt)
        self.assertIn("3-1", prompt)
    def test_marks_unavailable_asian(self):
        analysis = _analysis()
        analysis["odds_movement"]["asian"]["available"] = False
        prompt = build_prompt(analysis)
        self.assertIn("亚盘初盘/即时盘：无数据", prompt)

    def test_includes_warnings(self):
        analysis = _analysis()
        analysis["warnings"] = ["历史交锋缺失"]
        self.assertIn("数据告警：历史交锋缺失", build_prompt(analysis))


class NarratorTest(unittest.TestCase):
    def test_disabled_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            narrator = MovementNarrator(api_key="")
            self.assertFalse(narrator.enabled)
            self.assertEqual(narrator.narrate(_analysis()), "")

    def test_enabled_with_key(self):
        self.assertTrue(MovementNarrator(api_key="secret").enabled)

    def test_base_url_trailing_slash(self):
        narrator = MovementNarrator(api_key="x", base_url="https://api.deepseek.com/v1/")
        self.assertEqual(narrator.base_url, "https://api.deepseek.com/v1")

    def test_narrate_movements_only_touches_attention(self):
        alert = _analysis(attention=True)
        quiet = _analysis(attention=False)
        quiet["home_team"] = "无声主队"
        count = narrate_movements([alert, quiet], narrator=_FakeNarrator())
        self.assertEqual(count, 1)
        self.assertEqual(alert["narration"], "解读：主队盘口异动")
        self.assertNotIn("narration", quiet)


class BrowserReportTest(unittest.TestCase):
    def test_html_shows_narration_when_present(self):
        item = _analysis()
        item["narration"] = "盘口升盘配合主胜概率，市场倾向主队。"
        html = render_browser_html([item], "26134", "http://example.com")
        self.assertIn("盘口升盘配合主胜概率，市场倾向主队。", html)
        self.assertIn("大模型解读", html)
        self.assertIn('class="narration-row"', html)

    def test_html_without_narration(self):
        html = render_browser_html([_analysis()], "26134", "http://example.com")
        self.assertNotIn('class="narration-row"', html)


if __name__ == "__main__":
    unittest.main()
