import unittest
import os
from unittest import mock

from football_analysis.browser_report import render_browser_html
from football_analysis.llm import endpoint_config
from football_analysis.scout import MatchScout, _parse_scout_text, build_scout_prompt, scout_matches


def _analysis(*, action: str = "观望") -> dict:
    return {
        "match_no": "5",
        "league": "英冠",
        "match_time": "10-02 03:00",
        "home_team": "主队",
        "away_team": "客队",
        "prediction": {
            "action": action,
            "market_direction": "主胜",
        },
    }


class _FakeScout(MatchScout):
    def __init__(self, block: dict):
        super().__init__(api_key="fake")
        self.block = block

    def scout(self, analysis: dict) -> dict:
        return self.block


class BuildScoutPromptTest(unittest.TestCase):
    def test_contains_match_context_and_json_contract(self):
        prompt = build_scout_prompt(_analysis())
        self.assertIn("主队 vs 客队", prompt)
        self.assertIn("英冠", prompt)
        self.assertIn("当前市场方向：主胜", prompt)
        self.assertIn('"found"', prompt)
        self.assertIn('"lean"', prompt)

    def test_marks_missing_market_direction(self):
        analysis = _analysis()
        analysis["prediction"]["market_direction"] = ""
        prompt = build_scout_prompt(analysis)
        self.assertIn("当前市场方向：无", prompt)
        self.assertIn("无明确指向", prompt)


class ParseScoutTextTest(unittest.TestCase):
    def test_parses_valid_json(self):
        block = _parse_scout_text(
            '{"found": true, "facts": ["主队前锋伤缺", "客队提前保级"], '
            '"lean": "与市场方向相反", "confidence": "high", "summary": "两句话总结。"}'
        )
        self.assertEqual(block["facts"], ["主队前锋伤缺", "客队提前保级"])
        self.assertEqual(block["lean"], "与市场方向相反")
        self.assertEqual(block["confidence"], "high")
        self.assertEqual(block["summary"], "两句话总结。")

    def test_strips_code_fences(self):
        block = _parse_scout_text(
            '```json\n{"found": true, "facts": [], "summary": "只有总结"}\n```'
        )
        self.assertEqual(block["summary"], "只有总结")
        self.assertEqual(block["lean"], "无明确指向")

    def test_found_false_is_empty(self):
        self.assertEqual(_parse_scout_text('{"found": false}'), {})

    def test_garbage_text_is_empty(self):
        self.assertEqual(_parse_scout_text("检索服务无响应"), {})

    def test_unknown_confidence_defaults_low(self):
        block = _parse_scout_text('{"facts": ["一条"], "confidence": "very high"}')
        self.assertEqual(block["confidence"], "low")

    def test_no_facts_and_no_summary_is_empty(self):
        self.assertEqual(_parse_scout_text('{"found": true, "facts": []}'), {})


class MatchScoutTest(unittest.TestCase):
    def test_disabled_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            scout = MatchScout(api_key="")
            self.assertFalse(scout.enabled)
            self.assertEqual(scout.scout(_analysis()), {})

    def test_endpoint_config_from_args(self):
        base, key, model = endpoint_config(
            base_url="https://api.deepseek.com/v1/", api_key="sk", model="deepseek-chat"
        )
        self.assertEqual(base, "https://api.deepseek.com/v1")
        self.assertEqual(key, "sk")
        self.assertEqual(model, "deepseek-chat")

    def test_search_flag_env_default(self):
        import os

        os.environ.pop("LLM_SEARCH", None)
        self.assertTrue(MatchScout(api_key="x").search)
        os.environ["LLM_SEARCH"] = "0"
        self.assertFalse(MatchScout(api_key="x").search)
        os.environ.pop("LLM_SEARCH", None)


class ScoutMatchesTest(unittest.TestCase):
    def test_only_targets_hold_matches_with_teams(self):
        hold = _analysis()
        recommended = _analysis(action="主推")
        no_teams = _analysis()
        no_teams["home_team"] = ""
        count = scout_matches(
            [hold, recommended, no_teams],
            scout=_FakeScout({"facts": ["一条"], "lean": "支持市场方向", "summary": "总结"}),
        )
        self.assertEqual(count, 1)
        self.assertIn("scout", hold)
        self.assertNotIn("scout", recommended)
        self.assertNotIn("scout", no_teams)

    def test_scout_returning_empty_leaves_no_key(self):
        hold = _analysis()
        count = scout_matches([hold], scout=_FakeScout({}))
        self.assertEqual(count, 0)
        self.assertNotIn("scout", hold)


class BrowserReportTest(unittest.TestCase):
    def test_renders_scout_section(self):
        item = _analysis()
        item["scout"] = {
            "facts": ["主队前锋伤缺"],
            "lean": "与市场方向相反",
            "confidence": "medium",
            "summary": "客队战意不足。",
        }
        html = render_browser_html([item], "26134", "http://example.com")
        self.assertIn("主队前锋伤缺", html)
        self.assertIn("与市场方向相反", html)
        self.assertIn("软信息补充", html)
        self.assertIn('class="scout-card"', html)

    def test_no_scout_section_without_data(self):
        html = render_browser_html([_analysis()], "26134", "http://example.com")
        self.assertNotIn('class="scout-card"', html)


if __name__ == "__main__":
    unittest.main()
