import json
import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup

from football_analysis import llm, odds_pages
from football_analysis.query_report import _h2h_table_html, _lean_cell, render_query_html
from football_analysis.store import (
    SCHEMA,
    connect,
    list_issues,
    query_movements,
    save_issue_movements,
    update_team_reads,
)


def _analysis(**over) -> dict:
    base = {
        "match_no": "1",
        "league": "意甲",
        "match_time": "10-12 02:45",
        "home_team": "莱切",
        "away_team": "博洛尼亚",
        "history": {
            "home": {
                "matches": 10,
                "wins": 5,
                "draws": 1,
                "losses": 4,
                "goals_for": 18,
                "goals_against": 14,
                "home_matches": 6,
                "home_wins": 4,
                "home_draws": 0,
                "home_losses": 2,
                "away_matches": 4,
                "away_wins": 1,
                "away_draws": 1,
                "away_losses": 2,
                "handicap_win": 3,
                "handicap_push": 0,
                "handicap_win_rate": 0.375,
                "over_rate": 0.6,
                "rows": [
                    {
                        "date": "2026-10-10",
                        "home": "莱切",
                        "away": "里昂",
                        "score": "2 :1",
                        "league": "意甲",
                    }
                ],
            },
            "away": {
                "matches": 10,
                "wins": 3,
                "draws": 3,
                "losses": 4,
                "goals_for": 12,
                "goals_against": 13,
            },
            "h2h": {
                "total": 2,
                "home_wins": 1,
                "draws": 0,
                "away_wins": 1,
                "goals_for": 3,
                "goals_against": 2,
                "rows": [
                    {
                        "date": "2026-04-13",
                        "home": "博洛尼亚",
                        "away": "莱切",
                        "score": "2-0",
                        "league": "意甲",
                    }
                ],
            },
        },
        "odds_movement": {
            "asian": {
                "available": True,
                "initial": {"line_text": "受平手/半球", "home_water": 1.04, "away_water": 0.80},
                "current": {"line_text": "受半球", "home_water": 1.02, "away_water": 0.82},
                "lean": "客队",
            }
        },
        "prediction": {"final_probabilities": {"home": 0.35, "draw": 0.25, "away": 0.40}},
    }
    base.update(over)
    return base


class PromptTests(unittest.TestCase):
    def test_prompt_carries_team_data_and_page_text(self):
        prompt = llm.build_team_read_prompt(
            _analysis(), "## 近期战绩\n意甲 | 26-10-11 | 莱切1:2博洛尼亚"
        )
        self.assertIn("莱切（主） vs 博洛尼亚（客）", prompt)
        self.assertIn("赢盘率38%", prompt)
        self.assertIn("2026-10-10", prompt)          # 逐场明细
        self.assertIn("历史交锋", prompt)
        self.assertIn("受半球", prompt)              # 即时盘
        self.assertIn("客胜 40%", prompt)
        self.assertIn("500 数据分析页资料", prompt)
        self.assertIn("阵容情况", prompt)
        self.assertIn("首发/后备比例", prompt)
        self.assertIn("盘口异动", prompt)
        self.assertIn("莱切1:2博洛尼亚", prompt)
        self.assertIn("主队赢盘", prompt)            # 输出口径写在结尾
        self.assertIn("大球", prompt)               # 大小球方向也写进输出口径
        self.assertIn("over_tendency", llm.TEAM_READ_SYSTEM_PROMPT)

    def test_prompt_survives_missing_data(self):
        prompt = llm.build_team_read_prompt({})
        self.assertIn("无近期战绩数据", prompt)
        self.assertIn("亚盘：无数据", prompt)
        self.assertIn("500 数据分析页资料：未抓到", prompt)

    def test_prompt_distinguishes_confidence_handicap_and_match_result(self):
        prompt = llm.build_team_read_prompt(_analysis())
        self.assertIn("胜平负概率（非亚盘赢盘概率）", prompt)
        self.assertIn("主队赢盘不等于主胜", llm.TEAM_READ_SYSTEM_PROMPT)
        self.assertIn("不是另一方向概率的补数", llm.TEAM_READ_SYSTEM_PROMPT)
        self.assertIn("与 tendency 一致", llm.TEAM_READ_SYSTEM_PROMPT)
        self.assertIn("逆向判断", llm.TEAM_READ_SYSTEM_PROMPT)
        self.assertIn("与 over_tendency 一致", llm.TEAM_READ_SYSTEM_PROMPT)


class ParseTests(unittest.TestCase):
    def test_accepts_plain_fenced_and_embedded_json(self):
        body = '{"tendency": "主队赢盘", "confidence": 0.62, "reason": "主场赢盘率高"}'
        for raw in (body, f"```json\n{body}\n```", f"好的：\n{body}\n以上。"):
            parsed = llm.parse_team_read(raw)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual(parsed["tendency"], "主队赢盘")
            self.assertAlmostEqual(parsed["confidence"], 0.62)
            # 旧响应无 over_* 字段时，大小球倾向回退为「观望」、不报错
            self.assertEqual(parsed["over_tendency"], "观望")
            self.assertEqual(parsed["over_confidence"], 0.0)

    def test_parses_over_tendency_fields(self):
        body = json.dumps(
            {
                "tendency": "主队赢盘",
                "confidence": 0.6,
                "reason": "主场赢盘率高",
                "over_tendency": "大球",
                "over_confidence": 0.55,
                "over_reason": "双方近期大球率高",
            },
            ensure_ascii=False,
        )
        parsed = llm.parse_team_read(body)
        self.assertEqual(parsed["over_tendency"], "大球")
        self.assertAlmostEqual(parsed["over_confidence"], 0.55)
        self.assertEqual(parsed["over_reason"], "双方近期大球率高")

    def test_parses_and_limits_factors(self):
        parsed = llm.parse_team_read(json.dumps({
            "tendency": "观望",
            "confidence": 0.5,
            "reason": "阵容信息不足",
            "factors": ["伤停：主力中卫缺阵", "伤停：主力中卫缺阵", "阵容：首发比例未提供"] + ["多余"] * 8,
        }, ensure_ascii=False))
        self.assertEqual(parsed["factors"], ["伤停：主力中卫缺阵", "阵容：首发比例未提供", "多余"])

    def test_invalid_over_tendency_falls_back_to_观望(self):
        body = json.dumps(
            {
                "tendency": "观望",
                "over_tendency": "大",
                "over_confidence": 5,
                "over_reason": "x",
            },
            ensure_ascii=False,
        )
        parsed = llm.parse_team_read(body)
        self.assertEqual(parsed["over_tendency"], "观望")
        self.assertEqual(parsed["over_confidence"], 1.0)

    def test_rejects_bad_payloads(self):
        for raw in ("", "没有 JSON", '{"tendency": "主胜"}', "[1, 2]", '{"confidence": 0.5}'):
            self.assertIsNone(llm.parse_team_read(raw), raw)

    def test_clamps_confidence_and_keeps_long_reasons(self):
        reason = "详细分析 " * 30
        over_reason = "大小球依据 " * 30
        parsed = llm.parse_team_read(
            json.dumps({
                "tendency": "观望",
                "confidence": 7,
                "reason": reason,
                "over_reason": over_reason,
            },
                       ensure_ascii=False)
        )
        self.assertEqual(parsed["confidence"], 1.0)
        self.assertEqual(parsed["reason"], reason.strip())
        self.assertEqual(parsed["over_reason"], over_reason.strip())
        self.assertNotIn("  ", parsed["reason"])

    def test_reason_fields_are_capped_and_factors_allow_more_detail(self):
        parsed = llm.parse_team_read(json.dumps({
            "tendency": "观望",
            "reason": "理" * 250,
            "over_reason": "球" * 250,
            "factors": ["依" * 120],
        }, ensure_ascii=False))
        self.assertEqual(len(parsed["reason"]), 200)
        self.assertEqual(len(parsed["over_reason"]), 200)
        self.assertEqual(len(parsed["factors"][0]), 100)

    def test_preserves_direction_and_reason_when_confidence_is_below_half(self):
        away = llm.parse_team_read(json.dumps({
            "tendency": "\u5ba2\u961f\u8d62\u76d8",
            "confidence": 0.45,
            "reason": "客队抗压能力稍强，但资料不足，信心较低",
        }))
        self.assertEqual(away["tendency"], "\u5ba2\u961f\u8d62\u76d8")
        self.assertAlmostEqual(away["confidence"], 0.45)
        self.assertIn("客队抗压能力稍强", away["reason"])

        home = llm.parse_team_read(json.dumps({
            "tendency": "\u4e3b\u961f\u8d62\u76d8",
            "confidence": 0.35,
            "reason": "x",
        }))
        self.assertEqual(home["tendency"], "\u4e3b\u961f\u8d62\u76d8")
        self.assertAlmostEqual(home["confidence"], 0.35)

    def test_preserves_over_direction_when_confidence_is_below_half(self):
        parsed = llm.parse_team_read(json.dumps({
            "tendency": "\u89c2\u671b",
            "confidence": 0.5,
            "over_tendency": "\u5c0f\u7403",
            "over_confidence": 0.45,
            "over_reason": "小球略占优，资料不足",
        }))
        self.assertEqual(parsed["over_tendency"], "\u5c0f\u7403")
        self.assertAlmostEqual(parsed["over_confidence"], 0.45)
        self.assertEqual(parsed["over_reason"], "小球略占优，资料不足")


class _StubReader:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def read(self, analysis: dict, page_text: str = "") -> dict | None:
        self.calls.append((str(analysis.get("match_no")), page_text))
        return {"tendency": "客队赢盘", "confidence": 0.5, "reason": "客队客场赢盘率高"}


class EnrichTests(unittest.TestCase):
    def test_skips_without_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            items = [_analysis(match_no="1")]
            self.assertEqual(
                llm.enrich_team_reads(
                    items,
                    reader=llm.TeamReader(api_key="", fallback=("", "", "")),
                    pace=0,
                ),
                0,
            )
            self.assertNotIn("team_read", items[0])

    def test_attaches_result_and_passes_page_text(self):
        items = [_analysis(match_no="1"), _analysis(match_no="2")]
        reader = _StubReader()
        count = llm.enrich_team_reads(items, reader=reader, page_texts=["资料一"], pace=0)
        self.assertEqual(count, 1)
        self.assertEqual(items[0]["team_read"]["tendency"], "客队赢盘")
        self.assertNotIn("team_read", items[1])
        self.assertEqual(reader.calls, [("1", "资料一")])

    def test_low_confidence_survives_enrichment_storage_and_rendering(self):
        result = {
            "tendency": "客队赢盘",
            "confidence": 0.45,
            "reason": "客队抗逆能力稍强，但信心较低",
            "over_tendency": "小球",
            "over_confidence": 0.4,
            "over_reason": "小球略占优，但信心较低",
        }
        reader = mock.Mock(enabled=True)
        reader.read.return_value = result
        items = [_analysis()]
        self.assertEqual(
            llm.enrich_team_reads(items, reader=reader, page_texts=["资料"], pace=0),
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            save_issue_movements(db, items, "26156")
            row = query_movements(db, issue="26156")[0]
        self.assertEqual(json.loads(row["team_read"]), result)
        rendered = _lean_cell(row)
        self.assertIn("客队赢盘 45%", rendered)
        self.assertIn("小球 40%", rendered)
        self.assertIn(result["reason"], rendered)
        self.assertIn(result["over_reason"], rendered)


class ReaderTests(unittest.TestCase):
    def _response(self, content: str):
        response = mock.MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": content}}]}
        response.raise_for_status.return_value = None
        return response

    def test_read_posts_prompt_and_parses(self):
        reader = llm.TeamReader(
            base_url="http://x/v1", api_key="k", model="m", fallback=("", "", "")
        )
        with mock.patch.object(llm.requests, "post", return_value=self._response(
            '{"tendency": "观望", "confidence": 0.2, "reason": "资料不足"}'
        )) as post:
            result = reader.read(_analysis(), "资料")
        self.assertEqual(result["tendency"], "观望")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["messages"][0]["content"], llm.TEAM_READ_SYSTEM_PROMPT)
        self.assertIn("资料", body["messages"][1]["content"])

    def test_read_returns_none_on_failure(self):
        reader = llm.TeamReader(api_key="k", fallback=("", "", ""))
        with mock.patch.object(llm.requests, "post", side_effect=RuntimeError("boom")):
            self.assertIsNone(reader.read(_analysis()))

    def _throttled(self, status: int = 429):
        response = mock.MagicMock()
        response.status_code = status
        return response

    def test_read_retries_after_429(self):
        ok = self._response('{"tendency": "观望", "confidence": 0.3, "reason": "资料不足"}')
        ok.status_code = 200
        reader = llm.TeamReader(api_key="k", fallback=("", "", ""), retry_wait=0.01)
        with mock.patch.object(llm.requests, "post",
                               side_effect=[self._throttled(), self._throttled(503), ok]) as post:
            result = reader.read(_analysis())
        self.assertEqual(result["tendency"], "观望")
        self.assertEqual(post.call_count, 3)

    def test_read_gives_up_when_always_throttled(self):
        reader = llm.TeamReader(
            api_key="k", fallback=("", "", ""), retry_wait=0.01, attempts=2
        )
        with mock.patch.object(llm.requests, "post",
                               side_effect=[self._throttled(), self._throttled()]) as post:
            self.assertIsNone(reader.read(_analysis()))
        self.assertEqual(post.call_count, 2)

    def test_falls_back_to_backup_endpoint(self):
        ok = self._response('{"tendency": "客队赢盘", "confidence": 0.4, "reason": "备用结果"}')
        reader = llm.TeamReader(
            base_url="http://primary/v1",
            api_key="k1",
            model="m1",
            fallback=("http://backup/v1", "k2", "m2"),
            retry_wait=0.01,
            attempts=1,
        )
        with mock.patch.object(
            llm.requests, "post", side_effect=[self._throttled(), ok]
        ) as post:
            result = reader.read(_analysis())
        self.assertEqual(result["tendency"], "客队赢盘")
        self.assertAlmostEqual(result["confidence"], 0.4)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args.args[0].startswith("http://backup/v1"))
        self.assertEqual(post.call_args.kwargs["json"]["model"], "m2")

    def test_backup_endpoint_sticks_for_later_reads(self):
        reader = llm.TeamReader(
            base_url="http://primary/v1",
            api_key="k1",
            model="m1",
            fallback=("http://backup/v1", "k2", "m2"),
            retry_wait=0.01,
            attempts=1,
        )
        first = self._response(
            '{"tendency": "\\u5ba2\\u961f\\u8d62\\u76d8", "confidence": 0.4, "reason": "\\u5907\\u7528\\u7ed3\\u679c"}'
        )
        second = self._response(
            '{"tendency": "\\u4e3b\\u961f\\u8d62\\u76d8", "confidence": 0.6, "reason": "\\u7ed3\\u679c"}'
        )
        with mock.patch.object(
            llm.requests, "post", side_effect=[self._throttled(), first, second]
        ) as post:
            self.assertIsNotNone(reader.read(_analysis(match_no="1")))
            self.assertIsNotNone(reader.read(_analysis(match_no="2")))
        self.assertEqual(post.call_count, 3)
        self.assertTrue(post.call_args_list[0].args[0].startswith("http://primary/v1"))
        self.assertTrue(post.call_args_list[1].args[0].startswith("http://backup/v1"))
        self.assertTrue(post.call_args_list[2].args[0].startswith("http://backup/v1"))
        self.assertEqual(reader._active_endpoint, 1)

    def test_enabled_when_only_backup_is_configured(self):
        reader = llm.TeamReader(api_key="", fallback=("http://backup/v1", "k2", "m2"))
        self.assertTrue(reader.enabled)
        self.assertEqual(reader.endpoints[1], ("http://backup/v1", "k2", "m2"))


class ShujuTextTests(unittest.TestCase):
    def test_only_ok_pages_are_used(self):
        raw = {
            "1|shuju": {"status": "ok", "text": " 球队资料 "},
            "2|shuju": {"status": "empty", "text": ""},
            "3|shuju": {"status": "timeout", "text": ""},
        }
        self.assertEqual(odds_pages.shuju_text(raw, "1"), "球队资料")
        self.assertEqual(odds_pages.shuju_text(raw, "2"), "")
        self.assertEqual(odds_pages.shuju_text(raw, "3"), "")
        self.assertEqual(odds_pages.shuju_text(raw, "9"), "")

    def test_references_are_separate_from_analysis_text(self):
        raw = {
            "1|shuju": {
                "status": "ok",
                "text": (
                    "## 近期战绩\n主队 近10场 6胜\n"
                    "## 推介\n主队赢盘\n"
                    "## 对赛成绩\n主队 3胜1平\n"
                    "## 分析\n主队近期状态较好"
                ),
            }
        }
        refs = odds_pages.shuju_references(raw, "1")
        self.assertEqual(refs["推介"], "主队赢盘")
        self.assertEqual(refs["对赛成绩"], "主队 3胜1平")
        self.assertEqual(refs["分析"], "主队近期状态较好")
        analysis = odds_pages.shuju_analysis_text(raw, "1")
        self.assertIn("近期战绩", analysis)
        self.assertNotIn("主队赢盘", analysis)
        self.assertNotIn("主队 3胜1平", analysis)
        self.assertNotIn("主队近期状态较好", analysis)

    def test_references_capture_fixture_specific_h2h_heading(self):
        raw = {
            "1|shuju": {
                "status": "ok",
                "text": (
                    "## Home VS Away 对战历史\n"
                    "2026-04-13 Home 2-0 Away\n"
                    "## Home VS Away 预计阵容\n"
                    "阵容内容"
                ),
            }
        }

        refs = odds_pages.shuju_references(raw, "1")
        analysis = odds_pages.shuju_analysis_text(raw, "1")

        self.assertEqual(refs["对战历史"], "2026-04-13 Home 2-0 Away")
        self.assertNotIn("2-0", analysis)

    def test_only_h2h_references_preserve_table_rows(self):
        raw = {
            "1|shuju": {
                "status": "ok",
                "text": (
                    "## 对战历史\n表头\n比赛一\n比赛二\n"
                    "## 推介\n推介正文第一行\n推介正文第二行"
                ),
            }
        }

        refs = odds_pages.shuju_references(raw, "1")

        self.assertEqual(refs["对战历史"], "表头\n比赛一\n比赛二")
        self.assertEqual(refs["推介"], "推介正文第一行 推介正文第二行")


class StoreAndReportTests(unittest.TestCase):
    def test_handicap_and_total_popups_use_cantonese_team_names(self):
        h2h = (
            "League | 2026-04-23 | Home 2:2 Away | 0:0 | 平 | odds | line | 赢 | 大 | note"
        )
        item = _analysis(
            home_team="Home",
            away_team="Away",
            payload_json=json.dumps({
                "home_team_cantonese": "\u7cb5\u8bed\u4e3b\u961f",
                "away_team_cantonese": "\u7cb5\u8bed\u5ba2\u961f",
            }, ensure_ascii=False),
            team_read=json.dumps({
                "tendency": "\u4e3b\u961f\u8d62\u76d8",
                "confidence": 0.6,
                "reason": "Home \u7403\u961f\u7684\u7403\u5458\u56de\u5f52",
                "factors": ["Away \u9632\u5b88\u7403\u5458\u505c\u8d5b"],
                "over_tendency": "\u5c0f\u7403",
                "over_confidence": 0.55,
                "over_reason": "Home \u653b\u51fb\u4eba\u5458\u4e0d\u6574",
                "source_references": {
                    "\u63a8\u4ecb": "Home \u8fd1\u51b5\u8f83\u597d",
                    "\u5bf9\u8d5b\u6210\u7ee9": h2h,
                },
            }, ensure_ascii=False),
        )
        rendered = _lean_cell(item)
        soup = BeautifulSoup(rendered, "html.parser")
        popups = [
            trigger.select_one("template").decode_contents()
            for trigger in soup.select(".model-lean-tip")
        ]

        self.assertEqual(len(popups), 2)
        self.assertIn("\u7cb5\u8bed\u4e3b\u961f", popups[0])
        self.assertIn("\u7cb5\u8bed\u5ba2\u961f", popups[0])
        self.assertIn("\u7cb5\u8bed\u4e3b\u961f", popups[1])
        self.assertIn("\u7cb5\u8bed\u5ba2\u961f", popups[1])
        self.assertNotIn("Home", rendered)
        self.assertNotIn("Away", rendered)

    def test_legacy_low_confidence_direction_is_preserved_on_render(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "\u5ba2\u961f\u8d62\u76d8",
            "confidence": 0.45,
            "reason": "x",
        })))
        self.assertIn("\u5ba2\u961f\u8d62\u76d8 45%", rendered)
        self.assertNotIn("\u6a21\u578b ", rendered)
        self.assertNotIn("\u4e3b\u961f\u8d62\u76d8 55%", rendered)

    def test_legacy_low_confidence_over_direction_is_preserved_on_render(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "\u89c2\u671b",
            "confidence": 0.5,
            "over_tendency": "\u5c0f\u7403",
            "over_confidence": 0.45,
            "over_reason": "x",
        })))
        self.assertIn("\u5c0f\u7403 45%", rendered)
        self.assertNotIn("\u6a21\u578b ", rendered)
        self.assertNotIn("\u5927\u7403 55%", rendered)

    def test_team_read_round_trip_and_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            item = _analysis()
            item["team_read"] = {
                "tendency": "主队赢盘",
                "confidence": 0.55,
                "reason": "主场赢盘率高于客队",
                "factors": ["伤停：客队主力中卫缺阵", "盘口：主让半球升到一球"],
                "source_references": {"推介": "主队状态稳定"},
            }
            save_issue_movements(db, [item], "26140")
            rows = query_movements(db, issue="26140")
            self.assertEqual(json.loads(rows[0]["team_read"])["tendency"], "主队赢盘")
            html = render_query_html(
                rows, issues=list_issues(db), issue="26140", db_path=str(db)
            )
            self.assertIn("主队赢盘 55%", html)
            self.assertNotIn("模型 主队赢盘", html)
            self.assertIn(">AI分析<", html)
            self.assertIn("伤停：客队主力中卫缺阵", html)
            self.assertIn("盘口：主让半球升到一球", html)
            self.assertIn('<h4>重点因素</h4><ul>', html)
            self.assertIn("主场赢盘率高于客队", html)
            self.assertIn('id="ai-pop" role="tooltip" hidden', html)
            self.assertIn("width: min(640px, calc(100vw - 20px))", html)
            self.assertIn("max-height: min(480px, calc(100vh - 20px))", html)
            self.assertIn("background: rgba(255, 255, 255, .85)", html)
            self.assertIn(
                '<h4>足彩推荐</h4><div class="ai-tip-recommendation-text"><p>主队状态稳定</p></div>',
                html,
            )
            self.assertIn("ai-tip-recommendation", html)
            self.assertIn("pop.addEventListener('mouseenter', cancelHide)", html)
            self.assertIn("pop.contains(event.target)", html)
            self.assertNotIn("盘口 客队（博洛尼亚）", html)

    def test_source_references_render_in_structured_ai_tooltip(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "主队赢盘",
            "confidence": 0.6,
            "reason": "模型理由",
            "source_references": {
                "推介": "主队赢盘",
                "对赛成绩": "主队 3胜1平",
                "分析": "近期状态较好",
            },
        }, ensure_ascii=False)))
        self.assertIn('data-ai-tip tabindex="0"', rendered)
        self.assertIn('<template class="ai-tip-content">', rendered)
        self.assertIn('<div class="ai-tip-title">AI分析依据</div>', rendered)
        self.assertIn(
            '<section class="ai-tip-section"><h4>模型理由</h4>'
            '<p class="ai-tip-model-reason">模型理由</p></section>',
            rendered,
        )
        self.assertIn('<section class="ai-tip-section ai-tip-recommendation"><h4>足彩推荐</h4><div class="ai-tip-recommendation-text"><p>主队赢盘</p></div></section>', rendered)
        self.assertNotIn('title="', rendered)
        self.assertNotIn("对赛成绩", rendered)
        self.assertNotIn("主队 3", rendered)
        self.assertIn("分析", rendered)
        self.assertIn("近期状态较好", rendered)

    def test_team_aliases_render_in_handicap_and_total_goals_popups(self):
        h2h = (
            "英超 | 2026-04-23 | [9]伯恩茅斯2:2利兹联[15] | 0:0 | 平 | odds | line | 输 | 大 | note"
        )
        rendered = _lean_cell(
            _analysis(team_read=json.dumps({
                "tendency": "主队赢盘",
                "confidence": 0.6,
                "reason": "伯恩茅斯状态较好",
                "over_tendency": "大球",
                "over_confidence": 0.55,
                "over_reason": "利兹联防线偏弱",
                "source_references": {
                    "分析": "伯恩茅斯近期表现稳定",
                    "交战历史": h2h,
                },
            }, ensure_ascii=False)),
        )
        self.assertIn("伯恩茅斯状态较好", rendered)
        self.assertIn("伯恩茅斯近期表现稳定", rendered)
        self.assertIn("利兹联防线偏弱", rendered)
        self.assertIn("<td>伯恩茅斯</td><td>2:2</td><td>利兹联</td>", rendered)

    def test_ai_tooltip_trigger_is_limited_to_display_text(self):
        h2h = (
            "英超 | 2026-04-23 | Home 2:2 Away | 0:0 | 平 | odds | line | 输 | 大 | note"
        )
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "主队赢盘",
            "confidence": 0.6,
            "reason": "模型理由",
            "over_tendency": "大球",
            "over_confidence": 0.6,
            "source_references": {
                "推介": "主队赢盘",
                "交战历史": h2h,
            },
        }, ensure_ascii=False)))
        self.assertEqual(
            rendered.count('class="model-lean"><span class="model-lean-tip"'),
            2,
        )
        self.assertEqual(rendered.count(' data-ai-tip tabindex="0"'), 2)
        self.assertNotIn('<div class="model-lean" data-ai-tip', rendered)
        self.assertIn(
            ".model-lean-tip[data-ai-tip] { cursor: help;",
            render_query_html([], issues=[]),
        )

    def test_reasons_are_only_in_their_market_popups(self):
        reason = "盘口依据：主队近况与让球走势一致，需防范轮换。"
        over_reason = "大小球依据：近期进球偏多，但盘口升高降低大球优势。"
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "主队赢盘",
            "confidence": 0.6,
            "reason": reason,
            "factors": ["盘口重点因素"],
            "over_tendency": "大球",
            "over_confidence": 0.65,
            "over_reason": over_reason,
            "source_references": {
                "交战历史": (
                    "英超 | 2026-04-23 | Home 2:2 Away | 0:0"
                    " | 平 | odds | line | 输 | 大 | -"
                ),
            },
        }, ensure_ascii=False)))
        soup = BeautifulSoup(rendered, "html.parser")
        triggers = soup.select(".model-lean-tip")
        self.assertEqual(len(triggers), 2)
        handicap_popup = triggers[0].select_one("template").decode_contents()
        over_popup = triggers[1].select_one("template").decode_contents()
        self.assertIn(
            f'<h4>模型理由</h4><p class="ai-tip-model-reason">{reason}</p>',
            handicap_popup,
        )
        self.assertIn("盘口重点因素", handicap_popup)
        self.assertNotIn(over_reason, handicap_popup)
        self.assertIn(
            f'<h4>模型理由</h4><p class="ai-tip-model-reason">{over_reason}</p>',
            over_popup,
        )
        self.assertIn(".ai-tip-model-reason { color: #2563eb; }", render_query_html([], issues=[]))
        self.assertIn("对赛往绩", over_popup)
        self.assertIn("<td>Home</td><td>2:2</td><td>Away</td>", over_popup)
        self.assertNotIn(reason, over_popup)
        for template in soup.select("template"):
            template.decompose()
        self.assertEqual(soup.get_text(" ", strip=True), "主队赢盘 60% 大球 65%")
        self.assertFalse(soup.select(".model-reason"))

    def test_over_reason_has_popup_without_h2h(self):
        reason = "双方近期进球率偏高，盘口较深需保留风险。"
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "over_tendency": "大球",
            "over_confidence": 0.6,
            "over_reason": reason,
        }, ensure_ascii=False)))
        soup = BeautifulSoup(rendered, "html.parser")
        trigger = soup.select_one("[data-ai-tip]")
        self.assertIsNotNone(trigger)
        popup = trigger.select_one("template").decode_contents()
        self.assertIn(
            f'<h4>模型理由</h4><p class="ai-tip-model-reason">{reason}</p>',
            popup,
        )
        self.assertNotIn("h2h-table", popup)
        soup.select_one("template").decompose()
        self.assertEqual(soup.get_text(" ", strip=True), "大球 60%")

    def test_h2h_popup_is_available_when_over_confidence_is_zero(self):
        h2h = (
            "英超 | 2026-04-23 | Home 2:2 Away | 0:0 | 平 | odds | line | 输 | 大 | note"
        )
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "主队赢盘",
            "confidence": 0.6,
            "over_tendency": "观望",
            "over_confidence": 0.0,
            "source_references": {"交战历史": h2h},
        }, ensure_ascii=False)))

        self.assertIn("对赛往绩", rendered)
        self.assertIn("2026-04-23", rendered)
        self.assertIn("<td>Home</td><td>2:2</td><td>Away</td>", rendered)

    def test_handicap_tooltip_omits_h2h_but_keeps_over_popup(self):
        h2h = (
            "英超 | 2026-04-23 | Home 2:2 Away | 0:0 | 平 | odds | line | 输 | 大 | note"
        )
        for heading in ("交战历史", "对战历史", "对赛成绩"):
            with self.subTest(heading=heading):
                rendered = _lean_cell(_analysis(team_read=json.dumps({
                    "tendency": "主队赢盘",
                    "confidence": 0.6,
                    "reason": "盘口模型理由",
                    "factors": ["盘口重点因素"],
                    "over_tendency": "大球",
                    "over_confidence": 0.6,
                    "source_references": {
                        heading: h2h,
                        "推介": "主队赢盘",
                        "分析": "近期状态较好",
                    },
                }, ensure_ascii=False)))
                handicap_popup, over_popup = rendered.split("</template>", 1)
                self.assertIn("AI分析依据", handicap_popup)
                self.assertIn("盘口模型理由", handicap_popup)
                self.assertIn("盘口重点因素", handicap_popup)
                self.assertIn("<h4>足彩推荐</h4>", handicap_popup)
                self.assertIn("近期状态较好", handicap_popup)
                self.assertNotIn(heading, handicap_popup)
                self.assertNotIn("2026-04-23", handicap_popup)
                self.assertIn("对赛往绩", over_popup)
                self.assertIn('<table class="h2h-table">', over_popup)
                self.assertIn("<td>Home</td><td>2:2</td><td>Away</td>", over_popup)
                self.assertEqual(rendered.count("2026-04-23"), 1)

    def test_recommendation_strips_result_legend_and_colors_win_loss(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "主队赢盘",
            "confidence": 0.6,
            "source_references": {
                "心水推荐": "近况 W W L ( W=胜、D=和、L=负 )"
            },
        }, ensure_ascii=False)))
        self.assertIn(
            '<div class="ai-tip-recommendation-text"><p>近况 '
            '<span class="ai-tip-result-win">W</span> '
            '<span class="ai-tip-result-win">W</span> '
            '<span class="ai-tip-result-loss">L</span></p></div>',
            rendered,
        )
        self.assertNotIn("W=胜", rendered)
        self.assertNotIn("D=和", rendered)
        self.assertNotIn("<strong>", rendered)

    def test_result_sequences_are_colored_in_all_tooltip_sections(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "涓婚槦璧㈢洏",
            "confidence": 0.6,
            "source_references": {
                "Recent form": "DLLLDL",
                "Road record": "WDWWL1/**W**",
            },
        }, ensure_ascii=False)))
        self.assertIn(
            '<h4>Recent form</h4><p>D'
            '<span class="ai-tip-result-loss">L</span>'
            '<span class="ai-tip-result-loss">L</span>'
            '<span class="ai-tip-result-loss">L</span>D'
            '<span class="ai-tip-result-loss">L</span></p>',
            rendered,
        )
        self.assertIn(
            '<h4>Road record</h4><p>'
            '<span class="ai-tip-result-win">W</span>D'
            '<span class="ai-tip-result-win">W</span>'
            '<span class="ai-tip-result-win">W</span>'
            '<span class="ai-tip-result-loss">L</span>1/'
            '<span class="ai-tip-result-win">W</span></p>',
            rendered,
        )
        self.assertNotIn("**", rendered)

    def test_recommendation_analysis_starts_after_head_to_head_record(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "涓婚槦璧㈢洏",
            "confidence": 0.6,
            "source_references": {
                "心水推荐": "推介 - Roma 对赛成绩 - Roma 2胜1和0负 Analysis text",
            },
        }, ensure_ascii=False)))
        self.assertIn(
            '<div class="ai-tip-recommendation-text">'
            '<div class="ai-tip-recommendation-pick"><div>足彩推荐 - Roma</div>'
            '<div>对赛成绩 - Roma 2<span class="ai-tip-result-win">胜</span>'
            '1和0<span class="ai-tip-result-loss">负</span></div></div>'
            '<p class="ai-tip-recommendation-analysis">Analysis text</p></div>',
            rendered,
        )

    def test_recommendation_records_render_on_separate_lines(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "涓婚槦璧㈢洏",
            "confidence": 0.6,
            "source_references": {
                "心水推荐": (
                    "Roma (主) | 近况走势 - WLDLWW | 盘路赢输 - WL1/LLLW | "
                    "Man U | 近况走势 - DLDDLL | 盘路赢输 - 1/WLLLLL | "
                    "推介 - Roma 对赛成绩 - Roma 2胜1和0负 分析正文"
                ),
            },
        }, ensure_ascii=False)))
        self.assertNotIn("<h4>心水推荐</h4>", rendered)
        self.assertIn(
            '<div class="ai-tip-recommendation-grid"><div>Roma (主)</div>'
            '<div>近况走势 - ',
            rendered,
        )
        self.assertIn('</span></div><div>盘路赢输 - ', rendered)
        self.assertIn('</div><div>Man U</div><div>近况走势 - ', rendered)
        self.assertIn(
            '<div class="ai-tip-recommendation-pick"><div>足彩推荐 - Roma</div>'
            '<div>对赛成绩 - Roma 2<span class="ai-tip-result-win">胜</span>1和0<span class="ai-tip-result-loss">负</span></div>',
            rendered,
        )
        self.assertIn(
            '<p class="ai-tip-recommendation-analysis">分析正文</p>',
            rendered,
        )

    def test_recommendation_records_render_as_rows_without_pipe_separators(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "涓婚槦璧㈢洏",
            "confidence": 0.6,
            "source_references": {
                "心水推荐": (
                    "Home (主) 近况走势 - WWDLL 盘路赢输 - WWLWL "
                    "Away 近况走势 - WDLWD 盘路赢输 - WLLWL "
                    "推介 - Home 对赛成绩 - Home 2胜1和0负 分析正文"
                ),
            },
        }, ensure_ascii=False)))
        self.assertIn(
            '<div class="ai-tip-recommendation-grid"><div>Home (主)</div>'
            '<div>近况走势 - ',
            rendered,
        )
        self.assertIn(
            '</div><div>Away</div><div>近况走势 - ',
            rendered,
        )
        self.assertIn(
            '<div class="ai-tip-recommendation-pick"><div>足彩推荐 - Home</div>'
            '<div>对赛成绩 - Home 2<span class="ai-tip-result-win">胜</span>1和0<span class="ai-tip-result-loss">负</span></div>',
            rendered,
        )

    def test_ai_tooltip_uses_one_font_size(self):
        rendered = render_query_html([], issues=[], issue="", leagues=[])
        self.assertIn(".ai-tip-section h4 { margin: 0 0 3px; color: #475569; font-size: 13px;", rendered)
        self.assertIn(".ai-tip-recommendation h4 { margin-bottom: 5px; color: #1d4ed8; font-size: 13px;", rendered)
        self.assertIn(".ai-tip-recommendation-text { color: #0f172a; font-size: 13px;", rendered)
        self.assertIn(
            ".ai-tip-recommendation-pick { display: grid; grid-template-columns: "
            "repeat(2, minmax(0, 1fr));",
            rendered,
        )
        self.assertIn(
            ".ai-tip-recommendation-pick > :only-child { grid-column: 1 / -1; }",
            rendered,
        )
        self.assertIn(
            ".h2h-table tbody tr:nth-child(even) { background: #f8fafc; }",
            rendered,
        )

    def test_over_tendency_without_confidence_is_hidden(self):
        row = _analysis(
            team_read=json.dumps(
                {
                    "tendency": "主队赢盘",
                    "confidence": 0.55,
                    "over_tendency": "大球",
                    "over_confidence": 0.0,
                    "over_reason": "大小球资料不足",
                },
                ensure_ascii=False,
            )
        )
        rendered = _lean_cell(row)
        self.assertNotIn("大球", rendered)
        self.assertNotIn("大小球资料不足", rendered)

    def test_over_tendency_hovers_h2h_score_popup(self):
        h2h = (
            "赛事 | 比赛日期 | 主队比分客队 | 半场 | 赛果 | 平均欧指 | 让球指数 | 盘路 | 大小 | 备注\n"
            "英超 | 2026-10-31 | [13]伯恩茅斯VS利兹联[7] | VS | - | 2.093.343.56 | 1.08半球0.76 | - | - | -\n"
            "英超 | 2026-04-23 | [9]伯恩茅斯2:2利兹联[15] | 0:0 | 平 | odds-hidden | 0.95半球1.01 | 输 | 大 | note-hidden\n"
            "英超 | 2025-09-27 | [12]利兹联2:2伯恩茅斯[4] | 1:1 | 平 | odds-hidden | 0.82受半球0.98 | 赢 | 大 | note-hidden"
        )
        row = _analysis(
            league_color="#FF1717",
            team_read=json.dumps(
                {
                    "tendency": "主队赢盘",
                    "confidence": 0.55,
                    "over_tendency": "大球",
                    "over_confidence": 0.6,
                    "source_references": {
                        "交战历史": h2h,
                    },
                },
                ensure_ascii=False,
            )
        )
        rendered = _lean_cell(row)
        popup = rendered[rendered.index('aria-label="大球 60%'):]
        self.assertIn(
            'aria-label="大球 60%，大小球分析依据"',
            popup,
        )
        self.assertIn(
            '<div class="ai-tip-title">大小球分析依据</div>',
            popup,
        )
        self.assertEqual(popup.count("<tr>"), 3)
        self.assertIn(
            '<div class="h2h-summary"><span>2 场交锋</span>'
            '<span>总进球 8</span><span>场均进球 4.0</span></div>',
            popup,
        )
        table = BeautifulSoup(popup, "html.parser").select_one(".h2h-table")
        table = BeautifulSoup(str(table), "html.parser")
        self.assertEqual(
            [cell.get_text() for cell in table.select("thead th")],
            ["赛事名", "比赛日期", "主队名", "全场比分", "客队名", "半场比分", "亚盘", "盘路", "大小"],
        )
        self.assertEqual(
            [[cell.get_text() for cell in row.select("td")][6:]
             for row in table.select("tbody tr")],
            [["半球", "输", "大"], ["受半球", "赢", "大"]],
        )
        self.assertIn("<td>伯恩茅斯</td><td>2:2</td><td>利兹联</td>", popup)
        self.assertIn(
            '<span class="league-badge" style="background:#FF1717;">英超</span>',
            popup,
        )
        self.assertIn('<span class="ai-tip-result-loss">输</span>', popup)
        self.assertIn('<span class="ai-tip-result-win">赢</span>', popup)
        self.assertNotIn("VS", popup)
        self.assertNotIn("平均欧指", popup)
        self.assertNotIn("odds-hidden", popup)
        self.assertNotIn("备注", popup)
        self.assertNotIn("note-hidden", popup)

    def test_h2h_asian_column_removes_water_and_preserves_bare_lines(self):
        for raw, expected in (
            ("0.95一球0.83", "一球"),
            ("1受平手/半球0.7", "受平手/半球"),
            ("0.76平手0.94", "平手"),
            ("0.9半球0.8", "半球"),
            ("0.95 受一球/球半 1.01", "受一球/球半"),
            ("受半球", "受半球"),
            ("0.25", "0.25"),
            ("0/0.5", "0/0.5"),
            ("---", "---"),
            ("", ""),
        ):
            with self.subTest(raw=raw):
                rendered = _h2h_table_html(
                    f"联赛 | 2026-01-01 | 主队2:1客队 | 1:0 | 胜 | odds | {raw} | 赢 | 大 | -"
                )
                table = BeautifulSoup(rendered, "html.parser")
                cells = table.select("tbody tr td")
                self.assertEqual(cells[6].get_text(), expected)
                self.assertEqual([cell.get_text() for cell in cells[7:]], ["赢", "大"])
                self.assertIn("<span>总进球 3</span>", rendered)

    def test_over_popup_keeps_u21_out_of_full_time_scores_and_totals(self):
        h2h = (
            "荷乙 | 2026-11-03 | [17]阿贾克斯U21VS维特斯[10] | VS | - | odds | line | - | - | -\n"
            "荷乙 | 2026-04-06 | [15]维特斯6:1阿贾克斯U21[19] | 2:0 | 负 | odds | line | 输 | 大 | -\n"
            "荷乙 | 2025-12-02 | [20]阿贾克斯U212:0维特斯[19] | 1:0 | 胜 | odds | line | 赢 | 小 | -\n"
            "荷乙 | 2025-04-15 | [17]阿贾克斯U211:1维特斯[20] | 1:0 | 平 | odds | line | 走 | 小 | -\n"
            "荷乙 | 2024-09-22 | [9]维特斯2:2阿贾克斯U21[11] | 1:2 | 平 | odds | line | 赢 | 大 | -\n"
            "荷杯 | 2008-09-25 | 阿贾克斯U210:2维特斯 | 0:1 | 负 | -- | --- | - | 小 | -"
        )
        row = _analysis(team_read=json.dumps({
            "over_tendency": "大球",
            "over_confidence": 0.6,
            "source_references": {"交战历史": h2h},
        }, ensure_ascii=False))
        rendered = _lean_cell(row)
        for home, score, away in (
            ("维特斯", "6:1", "阿贾克斯U21"),
            ("阿贾克斯U21", "2:0", "维特斯"),
            ("阿贾克斯U21", "1:1", "维特斯"),
            ("维特斯", "2:2", "阿贾克斯U21"),
            ("阿贾克斯U21", "0:2", "维特斯"),
        ):
            self.assertIn(f"<td>{home}</td><td>{score}</td><td>{away}</td>", rendered)
        self.assertIn(
            "<span>5 场交锋</span><span>总进球 17</span><span>场均进球 3.4</span>",
            rendered,
        )
        self.assertNotIn("VS", rendered)
        self.assertNotIn("<td>阿贾克斯U</td>", rendered)

    def test_h2h_preserves_youth_suffixes_and_two_digit_scores(self):
        for suffix in ("", "U19", "U21", "U23", "u21", "(U21)", "U21 "):
            with self.subTest(suffix=suffix):
                home = f"Home{suffix}"
                rendered = _h2h_table_html(
                    f"League | 2026-01-01 | [1]{home}12:10AwayU21[2]"
                    " | 5:4 | 胜 | odds | line | 赢 | 大 | -"
                )
                self.assertIn(
                    f"<td>{home.strip()}</td><td>12:10</td><td>AwayU21</td>",
                    rendered,
                )
                self.assertIn("<span>总进球 22</span>", rendered)

    def test_query_uses_cached_h2h_for_existing_over_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            (cache_dir / "odds_pages_26154.json").write_text(
                json.dumps(
                    {
                        "fixture-1|shuju": {
                            "status": "ok",
                            "text": (
                                "## Home VS Away 对战历史\n"
                                "赛事 | 比赛日期 | 主队比分客队 | 半场 | 赛果 | 平均欧指 | 让球指数 | 盘路 | 大小 | 备注\n"
                                "英超 | 2026-10-31 | Home VS Away | VS | - | odds | line | - | - | -\n"
                                "英超 | 2026-04-13 | Home 2:0 Away | 1:0 | 胜 | odds-hidden | line-hidden | 赢 | 大 | note-hidden"
                            ),
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            row = {
                "issue": "26154",
                "match_no": "1",
                "team_read": json.dumps(
                    {
                        "over_tendency": "大球",
                        "over_confidence": 0.6,
                    },
                    ensure_ascii=False,
                ),
                "payload_json": "{}",
            }
            html = render_query_html(
                [row],
                issues=[],
                issue="26154",
                analyses=[{"match_no": "1", "fixture_id": "fixture-1"}],
                cache_dir=cache_dir,
            )

        self.assertIn(
            '<div class="model-lean"><span class="model-lean-tip" data-ai-tip tabindex="0" role="note" '
            'aria-describedby="ai-pop" aria-label="大球 60%，大小球分析依据">大球 60%'
            '<template class="ai-tip-content">',
            html,
        )
        self.assertIn('<div class="ai-tip-title">大小球分析依据</div>', html)
        h2h_table = html.split('<table class="h2h-table">', 1)[1].split("</table>", 1)[0]
        self.assertIn("<td>Home</td><td>2:0</td><td>Away</td>", h2h_table)
        self.assertEqual(h2h_table.count("<tr>"), 2)
        self.assertNotIn("VS", h2h_table)
        self.assertNotIn("odds-hidden", h2h_table)
        self.assertNotIn("note-hidden", h2h_table)

    def test_migration_adds_team_read_column(self):
        # Windows 上 sqlite 的句柄释放比 close() 慢一拍，清理失败不算这条用例的失败点
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "old.sqlite"
            old_schema = SCHEMA.replace("    team_read TEXT NOT NULL DEFAULT '',\n", "")
            with sqlite3.connect(db) as con:
                con.executescript(old_schema)
            conn = connect(db)
            columns = {row["name"] for row in conn.execute(
                "SELECT name FROM pragma_table_info('match_movements')"
            )}
            conn.close()
            self.assertIn("team_read", columns)


class UpdateTeamReadTests(unittest.TestCase):
    """复盘按钮点击即走 --refresh 重新抓取最终比分；更新覆盖该期全部 run。"""


    def test_overwrites_all_runs_by_match_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [_analysis(match_no="1")], "26140")
            save_issue_movements(db, [_analysis(match_no="1"), _analysis(match_no="2")], "26140")
            payload = {"tendency": "客队赢盘", "confidence": 0.4, "reason": "客场赢盘率高"}
            updated = update_team_reads(db, "26140", {"1": payload, "2": payload})
            # 两个 run × match_no=1 + 第二个 run 的 match_no=2，共 3 行
            self.assertEqual(updated, 3)
            rows = query_movements(db, issue="26140")
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[0]["team_read"])["tendency"], "客队赢盘")

    def test_empty_dict_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [_analysis(match_no="1")], "26140")
            self.assertEqual(update_team_reads(db, "26140", {}), 0)

    def test_expected_run_id_rejects_stale_ai_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            save_issue_movements(db, [_analysis(match_no="1")], "26140")
            save_issue_movements(db, [_analysis(match_no="1")], "26140")

            updated = update_team_reads(
                db,
                "26140",
                {"1": {"tendency": "观望"}},
                expected_run_id=1,
            )

            self.assertIsNone(updated)
            rows = query_movements(db, issue="26140", latest_only=False)
            self.assertTrue(all(not row["team_read"] for row in rows))


if __name__ == "__main__":
    unittest.main()
