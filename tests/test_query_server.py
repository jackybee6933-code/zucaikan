"""HTTP 层集成测试：真起 serve_query，用真请求打各端点。

服务跑在 daemon 线程里，靠进程退出回收——因此不需要给 serve_query 加
shutdown 钩子，也不用为测试改生产代码。端口先自己挑一个空闲的，
_pick_port 会原样绑定。
"""
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import updater
from football_analysis import __version__
from football_analysis.query_app import QueryHTTPServer, serve_query
from football_analysis.store import save_issue_movements

ISSUE = "26135"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _analysis(match_no: str = "1") -> dict:
    return {
        "match_no": match_no,
        "league": "欧冠",
        "match_time": "10-07 02:45",
        "home_team": f"H{match_no}",
        "away_team": f"A{match_no}",
        "final_score": "3-1",
        "market": {"asian": {"home_line": -1.0}, "total": {"line": 2.5}},
        "odds_analysis": {
            "asian_handicap": {"available": True, "recommended_side": "主队赢盘", "action": "轻仓"},
            "total_goals": {"available": True, "recommended_side": "大球", "action": "轻仓"},
        },
        "odds_movement": {"available": False, "attention": False, "level": "稳定"},
    }


def _get(url: str, timeout: float = 10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _post(url: str, payload: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # 调用方统一拿 (code, body)，需要断言抛出的用例改用 _get
        return exc.code, exc.read().decode("utf-8", "replace")


def _wait_idle(base: str, path: str = "/review-status", timeout: float = 30) -> dict:
    """等后台任务结束再发下一个，避免用例之间抢 running 锁。"""
    deadline = time.time() + timeout
    payload = {}
    while time.time() < deadline:
        _, body = _get(base + path)
        payload = json.loads(body)
        if not payload["running"]:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"{path} 任务未在超时前结束")


class QueryServerTests(unittest.TestCase):
    def test_server_does_not_wait_for_request_threads_on_close(self):
        self.assertTrue(QueryHTTPServer.daemon_threads)
        self.assertFalse(QueryHTTPServer.block_on_close)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db = root / "test.sqlite"
        save_issue_movements(cls.db, [_analysis()], ISSUE, "https://example.test")

        cls.reports = root / "reports"
        cls.reports.mkdir()
        (cls.reports / f"issue_{ISSUE}_20260101_000000.html").write_text(
            "<html>ok</html>", encoding="utf-8"
        )

        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(
            target=serve_query,
            args=(cls.db,),
            kwargs={"port": cls.port, "open_browser": False, "reports_dir": cls.reports},
            daemon=True,
        )
        cls.thread.start()

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/review-status", timeout=1):
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError(f"服务未在 15s 内就绪（端口 {cls.port}）")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # --- 基础页面 ---

    def test_root_serves_query_page(self):
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("足彩分析系统1.3.22", body)
        self.assertIn(">更新比分</button>", body)
        self.assertIn("data-freshness", body)
        self.assertIn("选择期号后查看", body)
        self.assertIn('"26135": "issue_26135_20260101_000000.html"', body)
        self.assertIn('btn.classList.toggle("action-primary", Boolean(file));', body)
        self.assertIn('btn.classList.toggle("action-neutral", !file);', body)

    def test_unknown_get_path_falls_back_to_query_page(self):
        """do_GET 没有 404 兜底，未知路径按单页应用方式回落到查询页。"""
        status, body = _get(self.base + "/nope")
        self.assertEqual(status, 200)
        self.assertIn("足彩分析系统1.3.22", body)

    def test_root_defaults_to_latest_issue_when_default_issue_given(self):
        """根页（无 query 参数）+ default_issue：只列当前期比赛、下拉框预选；?issue=（空）走全部期号分支。"""
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(
            target=serve_query,
            args=(self.db,),
            kwargs={
                "port": port,
                "open_browser": False,
                "reports_dir": self.reports,
                "default_issue": ISSUE,
            },
            daemon=True,
        )
        thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/review-status", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError(f"server did not start on port {port}")

        # 无 query 参数：根页按 default_issue 过滤，走「指定期号」渲染分支，该期被预选
        status, body = _get(base + "/")
        self.assertEqual(status, 200)
        self.assertIn(f'<option value="{ISSUE}" selected>', body)
        # 显式指定 ?issue=<非默认值>：走该期号分支，下拉框预选该期，不受 default_issue 影响
        status, body = _get(base + f"/?issue={ISSUE}")
        self.assertEqual(status, 200)
        self.assertIn(f'<option value="{ISSUE}" selected>', body)

    # --- 状态端点 ---

    def test_review_status_is_json_and_idle(self):
        status, body = _get(self.base + "/review-status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("running", payload)
        self.assertIn("result", payload)

    def test_fetch_status_is_json(self):
        status, body = _get(self.base + "/fetch-status")
        self.assertEqual(status, 200)
        self.assertIn("running", json.loads(body))

    def test_refresh_odds_status_is_json(self):
        status, body = _get(self.base + "/refresh-odds-status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("running", payload)
        self.assertFalse(payload["running"])

    def test_refresh_odds_status_includes_progress_when_file_present(self):
        """/refresh-odds-status 和 /ai-analyze-status 一样，把抓取进度文件快照
        附在响应里（期号不匹配或缺文件时不带 progress 字段），前端好画进度条。"""
        from football_analysis.main import _default_cache_dir

        cache_dir = _default_cache_dir()
        progress_file = cache_dir / ".ai_scrape_progress"
        try:
            progress_file.write_text(
                json.dumps({"issue": ISSUE, "phase": "scrape", "total": 42, "done": 14}),
                encoding="utf-8",
            )
            status, body = _get(self.base + "/refresh-odds-status")
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual(payload.get("progress", {}).get("total"), 42)

            # 期号不匹配：不带 progress
            progress_file.write_text(
                json.dumps({"issue": "99999", "phase": "scrape", "total": 42, "done": 14}),
                encoding="utf-8",
            )
            _, body = _get(self.base + "/refresh-odds-status")
            self.assertNotIn("progress", json.loads(body))
        finally:
            progress_file.unlink(missing_ok=True)

    def test_post_refresh_odds_requires_issue(self):
        status, _ = _post(self.base + "/refresh-odds", {})
        self.assertEqual(status, 400)

    def test_second_refresh_click_returns_busy_without_clobbering(self):
        """进行中的任务，重复点击返回 busy，且不写脏 error 字段。"""
        import threading as _t

        release = _t.Event()

        def slow_refresh(issue, db_path=""):
            release.wait(10)
            return f"{issue} 刷新完成"

        _wait_idle(self.base, "/refresh-odds-status")
        try:
            with mock.patch("football_analysis.query_app.run_refresh_odds", side_effect=slow_refresh):
                status1, _ = _post(self.base + "/refresh-odds", {"issue": ISSUE})
                self.assertEqual(status1, 202)
                # running 在 POST 返回前已置位：立刻查状态必为进行中
                _, body = _get(self.base + "/refresh-odds-status")
                self.assertTrue(json.loads(body)["running"])
                # 第二次点击：busy，不会触发第二次抓取
                status2, body2 = _post(self.base + "/refresh-odds", {"issue": ISSUE})
                self.assertEqual(status2, 200)
                busy = json.loads(body2)
                self.assertEqual(busy["status"], "busy")
                # busy 响应不碰 error，正在跑的任务完成时结果干净
                _, body3 = _get(self.base + "/refresh-odds-status")
                self.assertEqual(json.loads(body3)["error"], "")
                release.set()
                payload = _wait_idle(self.base, "/refresh-odds-status")
                self.assertEqual(payload["error"], "")
                self.assertIn("刷新完成", payload["result"])
        finally:
            release.set()

    def test_refresh_odds_failure_clears_running_and_reports_error(self):
        """抓取失败时 error 有值、running 复位，前端据此恢复按钮。"""

        def boom(issue, db_path=""):
            raise RuntimeError("连接中断")

        _wait_idle(self.base, "/refresh-odds-status")
        with mock.patch("football_analysis.query_app.run_refresh_odds", side_effect=boom):
            status, _ = _post(self.base + "/refresh-odds", {"issue": ISSUE})
            self.assertEqual(status, 202)
            payload = _wait_idle(self.base, "/refresh-odds-status")
        self.assertFalse(payload["running"])
        self.assertIn("连接中断", payload["error"])
        self.assertEqual(payload["result"], "")

    def test_query_page_has_refresh_odds_button(self):
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("refresh-odds-btn", body)
        self.assertIn("action-icon", body)
        self.assertIn('id="icon-refresh"', body)
        self.assertIn('use href="#icon-refresh"', body)
        self.assertIn("/refresh-odds-status", body)
        self.assertIn('id="refresh-odds-progress-bar"', body)
        self.assertIn("_renderRefreshProgress", body)
        # 工具条是 flex，进度条容器必须独占一行，否则宽度被压成 0、条形不可见
        self.assertEqual(body.count("flex:1 0 100%"), 2)

    def test_query_page_has_ai_analyze_button(self):
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("ai-analyze-btn", body)
        self.assertIn("/ai-analyze-status", body)
        refresh_at = body.find('id="refresh-odds-btn"')
        ai_at = body.find('id="ai-analyze-btn"')
        self.assertGreater(ai_at, refresh_at)
        ai_tag = body[ai_at:body.find(">", ai_at)]
        self.assertNotIn("margin-left", ai_tag)

    def test_query_page_has_settings_button_without_filter_checkboxes(self):
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn('id="query-form"', body)
        self.assertIn('sessionStorage.setItem("query-scroll-top", "1")', body)
        self.assertIn('history.scrollRestoration = "manual"', body)
        self.assertIn('id="settings-btn"', body)
        self.assertIn("settings-dialog", body)
        self.assertIn(f"当前版本：v{__version__}", body)
        self.assertNotIn("{_esc(__version__)}", body)
        self.assertIn("无法连接本地更新服务，请刷新页面后重试。", body)
        self.assertIn('data-name="primary"', body)
        self.assertIn('data-name="fallback"', body)
        self.assertIn("/settings/test", body)
        self.assertIn('id="update-details"', body)
        self.assertIn('id="update-release"', body)
        self.assertIn('id="update-progress-wrap"', body)
        self.assertIn('id="update-progress"', body)
        self.assertIn("/update/install-status", body)
        self.assertIn("formatElapsedSeconds", body)
        self.assertIn("检查耗时 ${formatElapsedSeconds(data.elapsed_ms)}", body)
        self.assertIn('toFixed(2) + " 秒"', body)
        self.assertIn("state.downloaded * 100 / state.total", body)
        self.assertIn("function formatVersionLabel(value)", body)
        self.assertIn("function formatKilobytes(value)", body)
        self.assertIn("formatKilobytes(state.downloaded)", body)
        self.assertIn("formatKilobytes(state.total)", body)
        self.assertNotIn("正在下载 v${targetVersion}", body)
        self.assertIn("window.confirm", body)
        self.assertIn('use href="#icon-plug"', body)
        self.assertIn('use href="#icon-download"', body)
        self.assertIn('use href="#icon-arrow-up-circle"', body)
        self.assertIn('id="settings-save" class="settings-button settings-button-primary"', body)
        self.assertNotIn("抓取间隔", body)
        self.assertNotIn("scrape_delay_seconds", body)
        self.assertNotIn('name="attention"', body)
        self.assertNotIn('name="latest"', body)

    def test_update_check_falls_back_to_last_manifest_cache(self):
        payload = {
            "version": "9.9",
            "url": "https://example.test/package.zip",
            "sha256": "a" * 64,
            "notes": "cached notes",
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(updater, "_cache_path", return_value=Path(tmp) / "cache.json"), \
                mock.patch.object(updater, "_http_json", return_value=payload):
            updater.check_update("https://example.test/latest.json", current_version="1.0")
            with mock.patch("football_analysis.query_app._load_updater", return_value=updater), \
                    mock.patch.object(
                        updater, "_http_json",
                        side_effect=updater.UpdateError("GitHub timeout"),
                    ):
                status, body = _get(self.base + "/update/check")
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(result["cached"])
        self.assertIn("GitHub timeout", result["offline_error"])
        self.assertEqual(result["latest_version"], "9.9")

    def test_update_install_runs_in_background_and_reports_download_progress(self):
        from football_analysis import query_app

        entered = threading.Event()
        release = threading.Event()
        class FakeUpdater:
            class UpdateError(Exception):
                pass

            @staticmethod
            def check_update():
                return {"update_available": True}

            @staticmethod
            def download_package(info, *, progress, phase):
                progress(25, 100)
                entered.set()
                release.wait(2)
                phase("verify")
                progress(100, 100)
                phase("ready")
                return Path("setup.exe"), "inno-setup"

            @staticmethod
            def install_downloaded(archive, *, package_type="zip"):
                return {"wait_seconds": 10}

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "update.sqlite"
            save_issue_movements(db, [_analysis()], ISSUE, "https://example.test")
            port = _free_port()
            base = f"http://127.0.0.1:{port}"
            original_loader = query_app._UPDATER_LOADER
            query_app._UPDATER_LOADER = lambda: FakeUpdater
            try:
                thread = threading.Thread(
                    target=serve_query,
                    args=(db,),
                    kwargs={"port": port, "open_browser": False},
                    daemon=True,
                )
                thread.start()
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        _get(base + "/update/install-status")
                        break
                    except OSError:
                        time.sleep(0.05)
                status, body = _post(base + "/update/install", {})
                self.assertEqual(status, 202)
                self.assertTrue(json.loads(body)["started"])
                self.assertTrue(entered.wait(2))
                status, body = _get(base + "/update/install-status")
                self.assertEqual(status, 200)
                state = json.loads(body)
                self.assertTrue(state["running"])
                self.assertEqual(state["downloaded"], 25)
                self.assertEqual(state["total"], 100)
                self.assertEqual(state["phase"], "download")
                release.set()
                state = _wait_idle(base, "/update/install-status")
                self.assertEqual(state["phase"], "complete")
            finally:
                query_app._UPDATER_LOADER = original_loader

    def test_settings_endpoint_masks_api_keys_and_saves_endpoints(self):
        status, body = _get(self.base + "/settings")
        self.assertEqual(status, 200)
        initial = json.loads(body)
        self.assertNotIn("api_key", initial["primary"])
        payload = {
            "primary": {
                "api_key": "primary-secret",
                "base_url": "https://primary.example/v1",
                "model": "primary-model",
            },
            "fallback": {
                "api_key": "fallback-secret",
                "base_url": "https://fallback.example/v1",
                "model": "fallback-model",
            },
        }
        status, body = _post(self.base + "/settings", payload)
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertTrue(saved["primary"]["api_key_configured"])
        self.assertEqual(saved["primary"]["api_key_hint"], "••••cret")
        self.assertNotIn("primary-secret", body)
        self.assertNotIn("fallback-secret", body)
        status, body = _get(self.base + "/settings")
        self.assertEqual(status, 200)
        loaded = json.loads(body)
        self.assertEqual(loaded["fallback"]["model"], "fallback-model")

    def test_settings_test_endpoint_uses_form_values_without_saving(self):
        with mock.patch(
            "football_analysis.query_app.test_endpoint",
            return_value={"ok": True, "message": "连接成功（test-model）"},
        ) as test:
            status, body = _post(
                self.base + "/settings/test",
                {
                    "name": "fallback",
                    "fallback": {
                        "api_key": "temporary-secret",
                        "base_url": "https://backup.example/v1",
                        "model": "test-model",
                    },
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        self.assertNotIn("temporary-secret", body)
        self.assertEqual(test.call_args.kwargs["base_url"], "https://backup.example/v1")
        self.assertEqual(test.call_args.kwargs["api_key"], "temporary-secret")
        self.assertEqual(test.call_args.kwargs["model"], "test-model")
        self.assertEqual(test.call_args.kwargs["use_saved_key"], True)

    def test_settings_test_endpoint_rejects_invalid_name(self):
        status, body = _post(
            self.base + "/settings/test",
            {"name": "other", "other": {"api_key": "key"}},
        )
        self.assertEqual(status, 400)
        self.assertIn("接入点参数无效", body)

    def test_backup_endpoint_returns_archive_and_restore_rejects_bad_data(self):
        status, body = _get(self.base + "/backup")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["data"])
        self.assertTrue(payload["filename"].endswith(".zip"))
        status, body = _post(self.base + "/backup/restore", {"data": "bad"})
        self.assertEqual(status, 400)
        self.assertIn("恢复失败", body)

    # --- /ai-analyze ---

    def test_post_ai_analyze_requires_issue(self):
        status, _ = _post(self.base + "/ai-analyze", {})
        self.assertEqual(status, 400)

    def test_ai_analyze_status_is_json(self):
        status, body = _get(self.base + "/ai-analyze-status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("running", payload)
        self.assertFalse(payload["running"])

    def test_ai_analyze_unknown_issue_reports_hint(self):
        from football_analysis.store import connect, default_db_path

        def fake_run(issue, db_path=""):
            return f"{issue} 未入库，请先「抓取并入库」再做 AI 分析"

        with mock.patch("football_analysis.query_app.run_ai_analyze", side_effect=fake_run):
            status, _ = _post(self.base + "/ai-analyze", {"issue": "99999"})
            self.assertEqual(status, 202)
            payload = _wait_idle(self.base, "/ai-analyze-status")
        self.assertFalse(payload["running"])
        self.assertIn("未入库", payload["result"])

    def test_second_ai_click_returns_busy(self):
        import threading as _t

        release = _t.Event()

        def slow_ai(issue, db_path=""):
            release.wait(10)
            return f"{issue} AI 分析完成"

        _wait_idle(self.base, "/ai-analyze-status")
        try:
            with mock.patch("football_analysis.query_app.run_ai_analyze", side_effect=slow_ai):
                status1, _ = _post(self.base + "/ai-analyze", {"issue": ISSUE})
                self.assertEqual(status1, 202)
                _, body = _get(self.base + "/ai-analyze-status")
                self.assertTrue(json.loads(body)["running"])
                status2, body2 = _post(self.base + "/ai-analyze", {"issue": ISSUE})
                self.assertEqual(status2, 200)
                self.assertEqual(json.loads(body2)["status"], "busy")
                release.set()
                payload = _wait_idle(self.base, "/ai-analyze-status")
                self.assertIn("AI 分析完成", payload["result"])
        finally:
            release.set()

    # --- /review ---

    def test_post_review_requires_issue(self):
        status, _ = _post(self.base + "/review", {})
        self.assertEqual(status, 400)

    def test_post_review_runs_and_reports_result(self):
        _wait_idle(self.base)
        status, _ = _post(self.base + "/review", {"issue": ISSUE})
        self.assertEqual(status, 202)
        payload = _wait_idle(self.base)
        self.assertEqual(payload["error"], "")
        self.assertIn(ISSUE, payload["result"])
        self.assertIn("亚盘", payload["result"])

    def test_post_review_rejects_unknown_issue_without_crashing(self):
        _wait_idle(self.base)
        status, _ = _post(self.base + "/review", {"issue": "99999"})
        self.assertEqual(status, 202)
        payload = _wait_idle(self.base)
        self.assertEqual(payload["error"], "")
        self.assertIn("99999", payload["result"])
        self.assertIn("无推荐", payload["result"])

    # --- /report/ ---

    def test_report_file_is_served(self):
        status, body = _get(f"{self.base}/report/issue_{ISSUE}_20260101_000000.html")
        self.assertEqual(status, 200)
        self.assertIn("ok", body)

    def test_missing_report_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _get(self.base + "/report/nope.html")
        self.assertEqual(ctx.exception.code, 404)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _get(self.base + "/report/..%2F..%2Ftest.sqlite")
        self.assertIn(ctx.exception.code, (400, 404))

    def test_unknown_post_path_returns_404(self):
        status, _ = _post(self.base + "/nope", {})
        self.assertEqual(status, 404)

    def test_fetch_dedup_finishes_instead_of_hanging(self):
        status, _ = _post(self.base + "/fetch", {"issue": ISSUE})
        self.assertEqual(status, 202)
        payload = _wait_idle(self.base, "/fetch-status")
        self.assertEqual(payload["error"], "")
        self.assertFalse(payload["running"])
        self.assertIn("不重复入库", payload["result"])


class BetServerTests(unittest.TestCase):
    BET_ISSUE = "99145"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db = root / "bets.sqlite"
        item = {
            "match_no": "1",
            "league": "英超",
            "match_time": "12-31 23:30",
            "home_team": "主队",
            "away_team": "客队",
            "final_score": "",
            "market": {
                "has_asian": True,
                "has_total": True,
                "asian": {
                    "home_line": 0.75,
                    "line_text": "受半球/一球",
                    "home_water": 1.04,
                    "away_water": 0.80,
                    "source": "澳门",
                },
                "total": {
                    "line": 2.5,
                    "line_text": "2.5",
                    "over_water": 0.90,
                    "under_water": 0.90,
                    "source": "澳门",
                },
            },
            "odds_analysis": {
                "asian_handicap": {
                    "available": True,
                    "recommended_side": "客队赢盘",
                    "action": "轻仓",
                    "confidence": 0.60,
                    "margin": 0.12,
                },
                "total_goals": {
                    "available": True,
                    "recommended_side": "小球",
                    "action": "观望",
                    "confidence": 0.52,
                    "margin": 0.03,
                },
            },
            "odds_movement": {"available": False, "attention": False, "level": "稳定"},
        }
        save_issue_movements(cls.db, [item], cls.BET_ISSUE, "https://example.test")
        cls.reports = root / "reports"
        cls.reports.mkdir()
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(
            target=serve_query,
            args=(cls.db,),
            kwargs={"port": cls.port, "open_browser": False, "reports_dir": cls.reports},
            daemon=True,
        )
        cls.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/bet-options?issue=1", timeout=1):
                    return
            except urllib.error.HTTPError:
                return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("投注服务未在 15s 内启动")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from football_analysis.bets import _connect

        conn = _connect(self.db)
        try:
            conn.execute("DELETE FROM manual_bets")
            conn.commit()
        finally:
            conn.close()

    def _create(self, market="asian", selection="away", stake=10):
        return _post(
            self.base + "/bets",
            {
                "issue": self.BET_ISSUE,
                "match_no": "1",
                "market": market,
                "selection": selection,
                "stake": stake,
            },
        )

    def test_page_has_bet_dialog_and_panel(self):
        status, body = _get(self.base + "/?issue=" + self.BET_ISSUE)
        self.assertEqual(status, 200)
        self.assertIn('id="bet-dialog"', body)
        self.assertIn("本期投注", body)
        self.assertIn("bet-open", body)
        self.assertIn("此分析工具纯属游戏娱乐，不构成投资建议，风险自担！", body)

    def test_options_and_full_lifecycle(self):
        status, body = _get(
            self.base + "/bet-options?issue=" + self.BET_ISSUE + "&match_no=1"
        )
        self.assertEqual(status, 200)
        options = json.loads(body)
        self.assertEqual(set(options["markets"]), {"asian", "total"})
        self.assertFalse(options["locked"])

        status, body = self._create()
        self.assertEqual(status, 201)
        created = json.loads(body)
        self.assertEqual(created["selection"], "away")
        self.assertEqual(created["stake"], 10.0)

        status, body = _post(
            self.base + "/bets/update",
            {"id": created["id"], "selection": "home", "stake": 20},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["stake"], 20.0)

        status, body = _post(self.base + "/bets/delete", {"id": created["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["deleted"], created["id"])

    def test_duplicate_market_returns_409(self):
        status, _ = self._create()
        self.assertEqual(status, 201)
        status, body = self._create()
        self.assertEqual(status, 409)
        self.assertIn("已有注单", json.loads(body)["error"])

    def test_csv_export(self):
        status, _ = self._create()
        self.assertEqual(status, 201)
        with urllib.request.urlopen(
            self.base + "/bets.csv?issue=" + self.BET_ISSUE, timeout=5
        ) as resp:
            text = resp.read().decode("utf-8-sig")
            content_type = resp.headers.get("Content-Type", "")
        self.assertIn("text/csv", content_type)
        self.assertIn("期号,场次", text)
        self.assertIn("亚盘", text)


if __name__ == "__main__":
    unittest.main()

