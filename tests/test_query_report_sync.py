"""query_report.py 里 _FETCH_BLOCK/render_query_html 有多份副本（历史遗留）。

运行时只有最后一份定义生效，但改前端时每份都得同步：26140 那次
「刷新盘口分歧」进度条只加进了第 1 份，实际页面（第 4 份）上根本没有。
"""
import unittest
from pathlib import Path

import football_analysis.query_report as query_report


class FetchBlockSyncTests(unittest.TestCase):
    def test_every_fetch_block_copy_has_refresh_odds_progress(self):
        src = Path(query_report.__file__).read_text(encoding="utf-8")
        copies = src.count("_FETCH_BLOCK = \\")
        self.assertGreater(copies, 0)
        # 进度条容器（<div id="refresh-odds-progress-bar">）与取元素的
        # getElementById 各一处，加渲染函数声明一处
        self.assertEqual(src.count('id="refresh-odds-progress-bar"'), copies)
        self.assertEqual(
            src.count('getElementById("refresh-odds-progress-bar")'), copies
        )
        self.assertEqual(src.count("function _renderRefreshProgress(s)"), copies)


if __name__ == "__main__":
    unittest.main()
