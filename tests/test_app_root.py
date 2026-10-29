import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import main


class AppRootTests(unittest.TestCase):
    """_app_root 决定 exe 的 cache/data 挂哪儿；一旦退化成 cwd 会静默写错库。"""

    def test_enable_ctrl_c_restores_windows_handler(self):
        with mock.patch.object(main.sys, "platform", "win32"), \
             mock.patch.object(main.sys, "frozen", True, create=True), \
             mock.patch.dict("os.environ", {main._UPDATE_RELAUNCH_ENV: "1"}), \
             mock.patch("builtins.open", mock.mock_open()), \
             mock.patch.object(main.ctypes, "windll", create=True) as windll:
            main._enable_ctrl_c()
        windll.kernel32.FreeConsole.assert_called_once_with()
        windll.kernel32.AllocConsole.assert_called_once_with()
        windll.kernel32.SetConsoleCtrlHandler.assert_called_once_with(None, False)

    def test_uses_exe_dir_when_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "app" / "tool.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"stub")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(main._app_root(), exe.parent.resolve())

    def test_falls_back_to_cwd_when_not_frozen(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(main._app_root(), Path.cwd())

    def test_cache_dir_sits_under_app_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            with mock.patch.object(main, "_app_root", return_value=root):
                self.assertEqual(main._default_cache_dir(), root / "cache")


class DefaultFlowTests(unittest.TestCase):
    """裸跑流程必须把 cache 与 db 都钉在 app_root 下，而不是交给 cwd。"""

    def _run(self, root: Path):
        captured = {}
        order = []

        def fake_analyze(args, **kwargs):
            order.append(("analyze", args.issue))
            captured["args"] = args
            captured["kwargs"] = kwargs
            return 0

        with mock.patch.object(main, "_app_root", return_value=root), \
             mock.patch.object(main, "_determine_latest_issue", side_effect=lambda _: order.append(("latest", "")) or "26146"), \
             mock.patch.object(main, "_refresh_previous_issue_scores", side_effect=lambda *args: order.append(("scores", args[1]))), \
             mock.patch.object(main, "_run_analyze", side_effect=fake_analyze), \
             mock.patch.object(main, "serve_query") as serve:
            rc = main._run_analyze_then_serve(main.build_parser())
        return rc, captured, serve, order

    def test_passes_app_root_cache_and_db_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            rc, captured, _, _ = self._run(root)

            self.assertEqual(rc, 0)
            args = captured["args"]
            self.assertEqual(Path(args.cache_dir), root / "cache")
            self.assertEqual(
                Path(args.db_path), root / "data" / "football_analysis.sqlite"
            )
            self.assertEqual(Path(args.output_dir), root / "reports")

    def test_creates_db_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            self._run(root)
            self.assertTrue((root / "data").is_dir())

    def test_does_not_open_single_issue_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            _, captured, _, _ = self._run(root)
            self.assertFalse(captured["kwargs"].get("open_report", True))

    def test_serves_the_same_db_it_wrote_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            _, captured, serve, _ = self._run(root)
            self.assertEqual(
                Path(serve.call_args.args[0]), Path(captured["args"].db_path)
            )

    def test_serves_reports_from_app_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            _, captured, serve, _ = self._run(root)
            self.assertEqual(
                Path(serve.call_args.kwargs["reports_dir"]), root / "reports"
            )

    def test_analyze_failure_stops_before_serving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            with mock.patch.object(main, "_app_root", return_value=root), \
                 mock.patch.object(main, "_run_analyze", side_effect=RuntimeError("boom")), \
                 mock.patch.object(main, "serve_query") as serve:
                rc = main._run_analyze_then_serve(main.build_parser())
            self.assertEqual(rc, 1)
            serve.assert_not_called()

    def test_nonzero_analyze_result_returns_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            with mock.patch.object(main, "_app_root", return_value=root), \
                 mock.patch.object(main, "_determine_latest_issue", return_value="26146"), \
                 mock.patch.object(main, "_run_analyze", return_value=3), \
                 mock.patch.object(main, "serve_query") as serve:
                rc = main._run_analyze_then_serve(main.build_parser())
            self.assertEqual(rc, 3)
            serve.assert_not_called()

    def test_startup_order_is_latest_then_previous_scores_then_latest_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            rc, captured, _, order = self._run(root)

            self.assertEqual(rc, 0)
            self.assertEqual(
                order,
                [("latest", ""), ("scores", "26146"), ("analyze", "26146")],
            )
            self.assertEqual(captured["args"].issue, "26146")

    def test_update_relaunch_enters_query_without_running_analysis(self):
        with mock.patch.object(main.sys, "frozen", True, create=True), \
                mock.patch.object(main, "_enable_ctrl_c"), \
                mock.patch.dict("os.environ", {main._UPDATE_RELAUNCH_ENV: "1"}), \
                mock.patch.object(main, "_run_query", return_value=0) as run_query, \
                mock.patch.object(main, "_run_analyze_then_serve") as run_analyze:
            result = main.main([])
        self.assertEqual(result, 0)
        self.assertEqual(run_query.call_args.args[0].command, "query")
        self.assertTrue(run_query.call_args.args[0].serve)
        run_analyze.assert_not_called()

    def test_previous_score_refresh_skips_issue_without_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                main,
                "list_issues",
                return_value=[{"issue": "26145", "match_count": 0}],
            ), mock.patch.object(main, "_refresh_scores") as refresh:
                main._refresh_previous_issue_scores(
                    Path(tmp) / "db.sqlite",
                    "26146",
                    Path(tmp) / "cache",
                )
            refresh.assert_not_called()

    def test_previous_score_refresh_uses_existing_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                main,
                "list_issues",
                return_value=[{"issue": "26145", "match_count": 14}],
            ), mock.patch.object(
                main,
                "_refresh_scores",
                return_value={"filled": 14, "match_count": 14, "updated": 14},
            ) as refresh:
                main._refresh_previous_issue_scores(
                    Path(tmp) / "db.sqlite",
                    "26146",
                    Path(tmp) / "cache",
                )
            self.assertEqual(
                refresh.call_args.args,
                (Path(tmp) / "db.sqlite", "26145"),
            )


if __name__ == "__main__":
    unittest.main()
