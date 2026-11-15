import unittest
from unittest.mock import Mock, patch

from football_analysis.console import task_log


class ConsoleTests(unittest.TestCase):
    def test_flushes_and_replaces_unencodable_characters(self):
        with patch("football_analysis.console.sys.stdout", Mock(encoding="gbk")), patch(
            "football_analysis.console.print"
        ) as output:
            task_log("刷新直播", "主播 \U0001f600")
        self.assertIn("[刷新直播]", output.call_args.args[0])
        output.assert_called_once_with(output.call_args.args[0], flush=True)

    def test_closed_console_does_not_stop_work(self):
        with patch("football_analysis.console.print", side_effect=OSError("closed")):
            task_log("AI 分析", "开始")

    def test_stream_without_real_encoding_uses_utf8(self):
        with patch("football_analysis.console.sys.stdout", Mock()), patch(
            "football_analysis.console.print"
        ) as output:
            task_log("AI 分析", "开始")
        self.assertIn("开始", output.call_args.args[0])
