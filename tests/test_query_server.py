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

from football_analysis.query_app import serve_query
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


def _wait_idle(base: str, timeout: float = 30) -> dict:
    """等复盘任务结束再发下一个，避免用例之间抢 running 锁。"""
    deadline = time.time() + timeout
    payload = {}
    while time.time() < deadline:
        _, body = _get(base + "/review-status")
        payload = json.loads(body)
        if not payload["running"]:
            return payload
        time.sleep(0.2)
    raise AssertionError("复盘任务未在超时前结束")


class QueryServerTests(unittest.TestCase):
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
        self.assertIn("变盘异动查询", body)

    def test_unknown_get_path_falls_back_to_query_page(self):
        """do_GET 没有 404 兜底，未知路径按单页应用方式回落到查询页。"""
        status, body = _get(self.base + "/nope")
        self.assertEqual(status, 200)
        self.assertIn("变盘异动查询", body)

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


if __name__ == "__main__":
    unittest.main()
