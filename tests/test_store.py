import tempfile
import unittest
from pathlib import Path

from football_analysis.browser_report import render_browser_html
from football_analysis.query_report import render_query_html
from football_analysis.store import latest_captured_at, list_issues, list_leagues, query_movements, save_issue_movements, update_match_scores
from football_analysis.store import rebuild_analyses_from_db


def _item(match_no: str, home: str, away: str, *, attention: bool, level: str, reason: str, direction: str = "") -> dict:
    return {
        "match_no": match_no,
        "league": "欧冠",
        "match_time": "09-10 00:45",
        "home_team": home,
        "away_team": away,
        "titan_match_id": "1" + match_no,
        "odds_analysis": {
            "asian_handicap": {
                "available": True,
                "recommended_side": "客队赢盘",
                "action": "轻仓",
            }
        },
        "odds_movement": {
            "available": True,
            "attention": attention,
            "level": level,
            "kind": direction,
            "reasons": [reason] if reason else [],
            "asian": {
                "available": True,
                "direction": direction,
                "lean": "客队（下盘/受让方）" if direction == "降盘" else "客队（上盘/让球方）" if direction == "升盘" else "",
                "line_change": -0.5 if direction == "降盘" else 0.5 if direction == "升盘" else 0.0,
                "initial": {
                    "line_display": "主让一球",
                    "home_water": 0.87,
                    "away_water": 1.01,
                },
                "current": {
                    "line_display": "主让半球",
                    "home_water": 1.03,
                    "away_water": 0.85,
                },
            },
        },
    }


class MovementStoreTests(unittest.TestCase):
    def test_kind_filter_returns_only_matching_movement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            rows = [
                _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘", direction="降盘"),
                _item("2", "利物浦", "马竞", attention=True, level="关注", reason="亚盘水位反转"),
            ]
            save_issue_movements(db, rows, "26121", "https://trade.500.com/sfc/")
            drops = query_movements(db, kind="降盘", latest_only=True)
            self.assertEqual(len(drops), 1)
            self.assertEqual(drops[0]["home_team"], "巴萨")
            self.assertEqual(drops[0]["kind"], "降盘")

    def test_legacy_retreat_kind_is_queried_as_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            rows = [
                _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘退盘", direction="退盘"),
            ]
            save_issue_movements(db, rows, "26121", "https://trade.500.com/sfc/")
            drops = query_movements(db, kind="降盘", latest_only=True)
            self.assertEqual(len(drops), 1)
            self.assertEqual(drops[0]["kind"], "降盘")

    def test_save_and_query_latest_issue_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            first = [
                _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘：主让一球 -> 主让半球", direction="降盘"),
                _item("2", "利物浦", "马竞", attention=False, level="稳定", reason=""),
            ]
            save_issue_movements(db, first, "26121", "https://trade.500.com/sfc/")
            second = [
                _item("1", "巴萨", "费耶诺德", attention=True, level="关注", reason="亚盘水位反转：主让一球 -> 主让半球"),
                _item("2", "利物浦", "马竞", attention=True, level="关注", reason="亚盘水位摆动：主让一球 -> 主让半球"),
            ]
            save_issue_movements(db, second, "26121", "https://trade.500.com/sfc/")
            issues = list_issues(db)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["issue"], "26121")
            self.assertEqual(issues[0]["run_count"], 2)
            latest = query_movements(db, issue="26121", latest_only=True)
            self.assertEqual(len(latest), 2)
            self.assertEqual(latest[0]["home_team"], "巴萨")
            self.assertEqual(latest[0]["level"], "关注")
            alerts = query_movements(db, attention_only=True, latest_only=True)
            self.assertEqual(len(alerts), 2)
            barcelona = query_movements(db, team="巴萨", latest_only=True)
            self.assertEqual(len(barcelona), 1)
            all_rows = query_movements(db, latest_only=False)
            self.assertEqual(len(all_rows), 4)
            html = render_query_html(latest, issues=issues, issue="26121", db_path=str(db))
            self.assertIn("巴萨 vs 费耶诺德", html)
            self.assertIn("足彩分析系统1.3.10", html)
            self.assertIn("降盘", html)
            self.assertNotIn("退盘", html)
            self.assertIn("倾向", html)


class DetailTotalDisplayTests(unittest.TestCase):
    def _item(self, *, total: dict | None, has_total: bool = True) -> dict:
        return {
            "match_no": "01",
            "league": "欧冠",
            "match_time": "09-10 00:45",
            "home_team": "主队",
            "away_team": "客队",
            "odds_analysis": {},
            "market": {
                "has_asian": True,
                "asian": {"line_text": "平手", "home_water": 0.90, "away_water": 0.95},
                "has_total": has_total,
                "total": total or {},
            },
            "odds_movement": {},
        }

    def test_detail_uses_market_total_when_available(self):
        html = render_browser_html(
            [
                self._item(
                    total={
                        "line_text": "3/3.5",
                        "over_water": 0.9,
                        "under_water": 0.97,
                        "source": "赛前指数",
                    }
                )
            ],
            "26122",
            "https://trade.500.com/sfc/",
        )
        self.assertIn("3/3.5 0.90/0.97", html)
        self.assertNotIn("3/3.5 0.90/0.97（赛前指数）", html)
        self.assertNotIn("2.5 0.00/0.00", html)

    def test_detail_shows_wu_when_market_total_missing(self):
        html = render_browser_html(
            [self._item(total=None, has_total=False), self._item(total={"available": False}, has_total=False)],
            "26122",
            "https://trade.500.com/sfc/",
        )
        detail_table = html.split('<table id="detail-table">', 1)[1].split("</table>", 1)[0]
        self.assertEqual(detail_table.count("<td>无</td>"), 2)
        self.assertNotIn("2.5", html)


class BrowserReportTests(unittest.TestCase):
    def test_alert_page_uses_upgrade_downgrade_layout(self):
        html = render_browser_html(
            [
                _item("4", "那不勒斯", "阿森纳", attention=True, level="重点关注", reason="让球盘升盘：主受半球 -> 主受一球", direction="升盘"),
                _item("13", "摩雷伦斯", "本菲卡", attention=True, level="关注", reason="让球盘降盘：主让一球 -> 主让半球", direction="降盘"),
            ],
            "26121",
            "https://trade.500.com/sfc/",
        )
        self.assertIn("data-alert-filter=\"升盘\"", html)
        self.assertIn("data-alert-filter=\"降盘\"", html)
        self.assertNotIn("data-alert-filter=\"退盘\"", html)
        self.assertNotIn("退盘", html)
        self.assertIn("上盘=让球方", html)
        self.assertIn(">倾向<", html)
        self.assertIn("那不勒斯 vs 阿森纳", html)
        self.assertIn("row.hidden = !visible", html)




class UpdateMatchScoresTests(unittest.TestCase):
    def test_updates_only_empty_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴萨", "马竞", attention=False, level="无数据", reason="")
            save_issue_movements(db, [item], "26120")
            # first update fills empty
            updated = update_match_scores(db, "26120", {"1": "2 - 1"})
            self.assertEqual(updated, 1)
            # 相同比分重写仍计入行数（覆盖语义），值不变
            updated = update_match_scores(db, "26120", {"1": "2 - 1"})
            self.assertEqual(updated, 1)
            # unknown issue returns 0
            self.assertEqual(update_match_scores(db, "99999", {"1": "0 - 0"}), 0)

    def test_backfills_every_run_that_lacks_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "H", "A", attention=False, level="稳定", reason="")
            save_issue_movements(db, [item], "26120")
            save_issue_movements(db, [item], "26120")
            # one backfill fills both runs, since score is a fact not a per-run snapshot
            updated = update_match_scores(db, "26120", {"1": "2 - 1"})
            self.assertEqual(updated, 2)
            # 覆盖语义下同样计入行数（每行都被重写为相同值）
            updated = update_match_scores(db, "26120", {"1": "2 - 1"})
            self.assertEqual(updated, 2)

    def test_real_score_overwrites_placeholder_zeros(self):
        """假 0-0（赛前占位）必须能被赛后真比分覆盖，这是复盘能修正历史期的关键。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "H", "A", attention=False, level="稳定", reason="")
            item["final_score"] = "0 - 0"
            save_issue_movements(db, [item], "26120")
            # 复盘拿到真比分，覆盖占位 0-0
            updated = update_match_scores(db, "26120", {"1": "0 - 1"})
            self.assertEqual(updated, 1)
            rows = query_movements(db, latest_only=True)
            self.assertEqual(rows[0]["final_score"], "0 - 1")

    def test_new_run_inherits_backfilled_score(self):
        """已回填真比分后，重跑同一场不能把新 run 的比分清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "H", "A", attention=False, level="稳定", reason="")
            save_issue_movements(db, [item], "26120")
            update_match_scores(db, "26120", {"1": "2 - 1"})
            save_issue_movements(db, [item], "26120")
            rows = query_movements(db, issue="26120", latest_only=True)
            self.assertEqual(rows[0]["final_score"], "2 - 1")


class LatestCapturedAtTests(unittest.TestCase):
    def test_returns_none_when_issue_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "H", "A", attention=False, level="稳定", reason="")
            save_issue_movements(db, [item], "26120")
            self.assertIsNone(latest_captured_at(db, "99999"))

    def test_returns_latest_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "H", "A", attention=False, level="稳定", reason="")
            save_issue_movements(db, [item], "26120")
            stamp = latest_captured_at(db, "26120")
            self.assertIsNotNone(stamp)
            self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T")


class IssueHitRatesTests(unittest.TestCase):
    def test_aggregates_per_issue_and_skips_watch_actions(self):
        from football_analysis.main import _issue_hit_rates
        from football_analysis.model import build_model_input, predict_match

        def _item(match_no, action, side, final_score, home_line=0.5):
            market = {"asian": {"home_line": home_line}, "total": {"line": 2.5}}
            odds = {"asian_handicap": {"available": True, "action": action, "recommended_side": side}}
            return {"match_no": match_no, "final_score": final_score, "market": market, "odds_analysis": odds}

        items = [
            # 0.5 盘主让: 主胜 2-1 (净胜 1 > 0.5) → 主队赢盘命中
            _item("1", "主推", "主队赢盘", "2 - 1", home_line=0.5),
            # 主赢 0-2 (净胜 -2) 与 主队赢盘相反 → 未命中
            _item("2", "轻仓", "主队赢盘", "0 - 2", home_line=0.5),
            # 观望不计入
            _item("3", "观望", "主队赢盘", "3 - 0", home_line=0.5),
        ]
        rates = _issue_hit_rates(items)
        self.assertEqual(rates["asian_total"], 2)
        self.assertEqual(rates["asian_hits"], 1)

    def test_total_goals_hit(self):
        from football_analysis.main import _issue_hit_rates

        def _item(match_no, action, side, final_score, total_line=2.5):
            market = {"asian": {"home_line": 0.5}, "total": {"line": total_line}}
            odds = {"asian_handicap": {"available": True, "action": action, "recommended_side": "主队赢盘"},
                    "total_goals": {"available": True, "action": action, "recommended_side": side}}
            return {"match_no": match_no, "final_score": final_score, "market": market, "odds_analysis": odds}

        items = [
            # 2+1=3 > 2.5 大琄 → 大琄命中
            _item("1", "主推", "大", "2 - 1", total_line=2.5),
            # 1+0=1 < 2.5 小琄 → 小琄命中
            _item("2", "轻仓", "小", "1 - 0", total_line=2.5),
            # 2+0=2 < 2.5 大琄 → 大琄未命中
            _item("3", "主推", "大", "2 - 0", total_line=2.5),
        ]
        rates = _issue_hit_rates(items)
        self.assertEqual(rates["total_total"], 3)
        self.assertEqual(rates["total_hits"], 2)


class ListLeaguesTests(unittest.TestCase):
    def test_scoped_to_issue_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            a = _item("1", "巴萨", "皇马", attention=True, level="重点关注", reason="让球盘降盘")
            a["league"] = "西甲"
            b = _item("2", "曼联", "曼城", attention=True, level="重点关注", reason="让球盘升盘")
            b["league"] = "英超"
            save_issue_movements(db, [a], "26120")
            save_issue_movements(db, [b], "26121")
            # 指定期号：只列该期出现过的联赛（下拉只显示当前 14 场的联赛）
            self.assertEqual(list_leagues(db, "26120"), ["西甲"])
            self.assertEqual(list_leagues(db, "26121"), ["英超"])
            # 不指定期号：全部去重联赛
            self.assertEqual(list_leagues(db), ["英超", "西甲"])

    def test_skips_empty_league(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            a = _item("1", "巴萨", "皇马", attention=True, level="重点关注", reason="让球盘降盘")
            a["league"] = ""
            save_issue_movements(db, [a], "26120")
            self.assertEqual(list_leagues(db, "26120"), [])


if __name__ == "__main__":
    unittest.main()


class LeagueColorTests(unittest.TestCase):
    def test_color_persisted_and_rebuilt(self):
        """league_color 入库后，查询页与重建分析都能拿到。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘")
            item["league"] = "英超"
            item["league_color"] = "#FF1717"
            save_issue_movements(db, [item], "26121", "https://trade.500.com/sfc/")

            rows = query_movements(db, latest_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["league"], "英超")
            self.assertEqual(rows[0]["league_color"], "#FF1717")

            rebuilt = rebuild_analyses_from_db(db, "26121")
            self.assertEqual(rebuilt[0]["league_color"], "#FF1717")

    def test_query_page_renders_colored_badge(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘")
            item["league_color"] = "#006633"
            save_issue_movements(db, [item], "26121", "https://trade.500.com/sfc/")
            rows = query_movements(db, latest_only=True)
            html = render_query_html(rows, issues=[], latest_only=True)
            self.assertIn('style="background:#006633;"', html)

    def test_browser_report_renders_colored_badge(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘")
            item["league_color"] = "#006633"
            save_issue_movements(db, [item], "26121", "https://trade.500.com/sfc/")
            analyses = rebuild_analyses_from_db(db, "26121")
            html = render_browser_html(analyses, "26121", "https://trade.500.com/sfc/")
            self.assertIn('style="background:#006633;"', html)

    def test_missing_color_falls_back_to_plain_text(self):
        """没颜色的联赛（如新杯赛）不应输出空标签；AI 按钮固定色不在排查范围。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴萨", "费耶诺德", attention=True, level="重点关注", reason="让球盘降盘")
            item["league"] = "新杯赛"
            save_issue_movements(db, [item], "26121", "https://trade.500.com/sfc/")
            rows = query_movements(db, latest_only=True)
            html = render_query_html(rows, issues=[], latest_only=True)
            self.assertNotIn('<span class="league-badge"', html)
            self.assertIn("新杯赛", html)

    def test_query_table_gives_match_column_more_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            item = _item("1", "巴塞罗那", "巴黎圣日耳曼", attention=True, level="重点关注", reason="让球盘降盘")
            save_issue_movements(db, [item], "26121", "https://trade.500.com/sfc/")
            html = render_query_html(query_movements(db, latest_only=True), issues=[], latest_only=True)
            self.assertIn(".alert-table .issue-cell { width: 6%;", html)
            self.assertIn(".alert-table .match-cell { width: 20%;", html)
