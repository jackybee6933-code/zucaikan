"""run_ai_analyze 的 fixture_id 兜底逻辑：老库 payload 无 fixture_id 时
从 odds_pages 产物的 shuju key 顺序推导，match_no 做索引。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis.query_app import run_ai_analyze


def _make_db(path: Path, issue: str = "26135") -> None:
    from football_analysis.store import connect

    conn = connect(path)  # 自动建表
    try:
        conn.execute(
            "INSERT INTO issue_runs (issue, source_url, captured_at) "
            "VALUES (?, 'u', datetime('now'))",
            (issue,),
        )
        conn.execute(
            "INSERT INTO match_movements "
            "(issue, run_id, captured_at, match_no, payload_json) "
            "VALUES (?, 1, datetime('now'), '1', '{}')",
            (issue,),
        )
        conn.commit()
    finally:
        conn.close()


def _raw_shuju(n: int = 7) -> dict:
    return {f"{7000000 + i}|shuju": {"status": "ok", "text": f"数据页-{i}"} for i in range(n)}


def _analyses(n: int) -> list[dict]:
    return [
        {"match_no": str(i), "home_team": "主", "away_team": "客"}
        for i in range(1, n + 1)
    ]


class FixtureIdFallbackTests(unittest.TestCase):
    """老库 payload 无 fixture_id 时，从产物 shuju key 按 match_no 索引取 fid。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ai.sqlite"
        _make_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _patch_all(self, raw: dict, analyses: list[dict]):
        """run_ai_analyze 里是局部 import，必须 patch 源模块的函数对象。"""
        captured: dict = {}

        def fake_enrich(items, *, reader, page_texts, pace=4.0, progress_path=None, issue=""):
            captured["n_items"] = len(items)
            captured["non_empty"] = sum(1 for t in page_texts if t)
            captured["page_texts"] = list(page_texts)
            for it in items:
                it["team_read"] = {"tendency": "观望", "confidence": 0.5, "reason": "x"}
            return len(items)

        import football_analysis.odds_pages as odds_pages
        import football_analysis.llm as llm_mod
        import football_analysis.store as store_mod

        p1 = mock.patch.object(odds_pages, "pages_kind_age_hours", return_value=1.0)
        p2 = mock.patch.object(odds_pages, "load_odds_pages_raw", return_value=raw)
        p3 = mock.patch.object(store_mod, "rebuild_analyses_from_db", return_value=analyses)
        p4 = mock.patch.object(llm_mod, "enrich_team_reads", side_effect=fake_enrich)
        p5 = mock.patch.object(store_mod, "update_team_reads",
                              side_effect=lambda db, issue, reads: len(reads))
        p6 = mock.patch("football_analysis.query_app._ensure_cdp", return_value=None)
        p7 = mock.patch("football_analysis.query_app._run_odds_scraper", return_value="")
        return [p1, p2, p3, p4, p5, p6, p7], captured

    def test_old_db_no_fixture_id_uses_shuju_key_order(self):
        """payload 无 fixture_id + 产物 7 场 shuju → 按 match_no 索引产物，
        LLM 收到 7 个非空 page_texts。"""
        raw = _raw_shuju(7)
        analyses = _analyses(7)
        patches, captured = self._patch_all(raw, analyses)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = run_ai_analyze("26135", db_path=self.db)

        self.assertIn("AI 分析完成 7/7 场", result, f"实际: {result}")
        self.assertEqual(captured["n_items"], 7)
        self.assertEqual(captured["non_empty"], 7)
        # 验证 page_texts 对应的是产物里 shuju 的顺序
        self.assertEqual(captured["page_texts"][0], "数据页-0")
        self.assertEqual(captured["page_texts"][6], "数据页-6")

    def test_more_analyses_than_shuju_keys_get_empty_text(self):
        """payload 无 fixture_id + 产物只有 3 场 shuju，analyses 有 5 场 →
        第 4、5 场（match_no 4, 5）超出 shuju 索引范围，page_text 为空串。"""
        raw = _raw_shuju(3)
        analyses = _analyses(5)
        patches, captured = self._patch_all(raw, analyses)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = run_ai_analyze("26135", db_path=self.db)

        self.assertEqual(captured["non_empty"], 3, "只有前 3 场能匹配到 shuju")
        self.assertEqual(captured["page_texts"][3], "")
        self.assertEqual(captured["page_texts"][4], "")

    def test_new_db_with_fixture_id_ignores_shuju_key_order(self):
        """payload 有 fixture_id（新库）→ 直接用 payload 里的 fid，不走产物顺序。"""
        raw = {
            "9999999|shuju": {"status": "ok", "text": "不该被用到"},
            "1234567|shuju": {"status": "ok", "text": "正确资料"},
        }
        analyses = [
            {"match_no": "1", "home_team": "主", "away_team": "客", "fixture_id": "1234567"},
        ]
        patches, captured = self._patch_all(raw, analyses)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = run_ai_analyze("26135", db_path=self.db)

        self.assertIn("AI 分析完成 1/1 场", result, f"实际: {result}")
        self.assertEqual(captured["page_texts"], ["正确资料"])


class StaleProgressSnapshotTests(unittest.TestCase):
    """AI 分析入口也要在动手前清掉上一轮留下的进度快照（同样的 issue 匹配）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "ai.sqlite"
        _make_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ai_analyze_clears_previous_snapshot(self):
        cache = self.root / "cache"
        cache.mkdir()
        stale = cache / ".ai_scrape_progress"
        stale.write_text(
            json.dumps({"issue": "26135", "phase": "scrape", "total": 42, "done": 42}),
            encoding="utf-8",
        )
        with mock.patch("football_analysis.main._app_root", return_value=self.root), \
             mock.patch("football_analysis.query_app._ensure_cdp", return_value=None), \
             mock.patch("football_analysis.query_app._run_odds_scraper", return_value=""), \
             mock.patch("football_analysis.store.rebuild_analyses_from_db", return_value=[]), \
             mock.patch("football_analysis.cdp_profile.shutdown_cdp_chrome"):
            run_ai_analyze("26135", db_path=self.db)
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
