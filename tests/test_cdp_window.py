import unittest
from unittest import mock

from football_analysis import cdp, cdp_profile


class LaunchWindowTests(unittest.TestCase):
    def test_cdp_chrome_starts_off_screen(self):
        """CDP Chrome 不能开在用户窗口的位置上，否则查询页看着像被顶掉。"""
        with mock.patch.object(cdp_profile, "copy_profile", return_value=([], [])), \
             mock.patch.object(cdp_profile.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_profile, "_probe", return_value=True):
            ok, _ = cdp_profile.launch_cdp_chrome(9222, wait_seconds=5)

        self.assertTrue(ok)
        argv = popen.call_args[0][0]
        self.assertIn("--window-position=-32000,-32000", argv)
        self.assertIn("--window-size=1200,900", argv)
        self.assertIn("about:blank", argv)


class RevealWindowTests(unittest.TestCase):
    def test_reveal_window_moves_bounds_on_screen(self):
        calls = []

        def fake_command(endpoint, method, params, timeout=5.0):
            calls.append((method, params))
            return {"windowId": 7} if method == "Browser.getWindowForTarget" else {}

        with mock.patch.object(cdp, "_browser_command", side_effect=fake_command):
            self.assertTrue(cdp.reveal_window("http://127.0.0.1:9222", "T1"))

        self.assertEqual(
            [call[0] for call in calls],
            ["Browser.getWindowForTarget", "Browser.setWindowBounds"],
        )
        self.assertEqual(calls[0][1], {"targetId": "T1"})
        self.assertEqual(calls[1][1]["windowId"], 7)
        bounds = calls[1][1]["bounds"]
        self.assertEqual(bounds["windowState"], "normal")
        self.assertGreater(bounds["left"], 0)
        self.assertGreater(bounds["top"], 0)

    def test_reveal_window_failure_is_swallowed(self):
        """挪窗口失败不能把抓取流程带崩。"""
        with mock.patch.object(cdp, "_browser_command", side_effect=cdp.CdpError("boom")):
            self.assertFalse(cdp.reveal_window("http://127.0.0.1:9222", "T1"))


if __name__ == "__main__":
    unittest.main()
