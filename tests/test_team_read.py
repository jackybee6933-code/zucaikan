import json
import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import llm, odds_pages
from football_analysis.query_report import _lean_cell, render_query_html
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

    def test_clamps_confidence_and_trims_reason(self):
        parsed = llm.parse_team_read(
            json.dumps({"tendency": "观望", "confidence": 7, "reason": "很长 " * 60},
                       ensure_ascii=False)
        )
        self.assertEqual(parsed["confidence"], 1.0)
        self.assertLessEqual(len(parsed["reason"]), 80)
        self.assertNotIn("  ", parsed["reason"])

    def test_reverses_direction_when_confidence_is_below_half(self):
        away = llm.parse_team_read(json.dumps({
            "tendency": "\u5ba2\u961f\u8d62\u76d8",
            "confidence": 0.45,
            "reason": "x",
        }))
        self.assertEqual(away["tendency"], "\u4e3b\u961f\u8d62\u76d8")
        self.assertAlmostEqual(away["confidence"], 0.55)

        home = llm.parse_team_read(json.dumps({
            "tendency": "\u4e3b\u961f\u8d62\u76d8",
            "confidence": 0.35,
            "reason": "x",
        }))
        self.assertEqual(home["tendency"], "\u5ba2\u961f\u8d62\u76d8")
        self.assertAlmostEqual(home["confidence"], 0.65)

    def test_reverses_over_direction_when_confidence_is_below_half(self):
        parsed = llm.parse_team_read(json.dumps({
            "tendency": "\u89c2\u671b",
            "confidence": 0.5,
            "over_tendency": "\u5c0f\u7403",
            "over_confidence": 0.45,
            "over_reason": "x",
        }))
        self.assertEqual(parsed["over_tendency"], "\u5927\u7403")
        self.assertAlmostEqual(parsed["over_confidence"], 0.55)


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
        self.assertEqual(result["tendency"], "主队赢盘")
        self.assertAlmostEqual(result["confidence"], 0.6)
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


class StoreAndReportTests(unittest.TestCase):
    def test_legacy_low_confidence_direction_is_reversed_on_render(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "\u5ba2\u961f\u8d62\u76d8",
            "confidence": 0.45,
            "reason": "x",
        })))
        self.assertIn("\u4e3b\u961f\u8d62\u76d8 55%", rendered)
        self.assertNotIn("\u6a21\u578b ", rendered)
        self.assertNotIn("\u5ba2\u961f\u8d62\u76d8 45%", rendered)

    def test_legacy_low_confidence_over_direction_is_reversed_on_render(self):
        rendered = _lean_cell(_analysis(team_read=json.dumps({
            "tendency": "\u89c2\u671b",
            "confidence": 0.5,
            "over_tendency": "\u5c0f\u7403",
            "over_confidence": 0.45,
            "over_reason": "x",
        })))
        self.assertIn("\u5927\u7403 55%", rendered)
        self.assertNotIn("\u6a21\u578b ", rendered)
        self.assertNotIn("\u5c0f\u7403 45%", rendered)

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
            self.assertIn("主场赢盘率高于客队", html)
            self.assertIn('id="ai-pop" role="tooltip" hidden', html)
            self.assertIn('<h4>推介</h4><p>主队状态稳定</p>', html)
            self.assertIn("pop.addEventListener('mouseenter', cancelHide)", html)
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
        self.assertIn('<section class="ai-tip-section"><h4>模型理由</h4><p>模型理由</p></section>', rendered)
        self.assertIn('<section class="ai-tip-section"><h4>推介</h4><p>主队赢盘</p></section>', rendered)
        self.assertNotIn('title="', rendered)
        self.assertIn("对赛成绩", rendered)
        self.assertIn("主队 3胜1平", rendered)
        self.assertIn("分析", rendered)
        self.assertIn("近期状态较好", rendered)

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


if __name__ == "__main__":
    unittest.main()
