import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_analysis import cdp_profile


def _make_src(tmp: str):
    src = Path(tmp) / "src"
    dst = Path(tmp) / "dst"
    (src / "Default" / "Network").mkdir(parents=True)
    (src / "Local State").write_bytes(b"state")
    (src / "Default" / "Preferences").write_bytes(b"prefs")
    (src / "Default" / "Network" / "Cookies").write_bytes(b"cookies")
    return src, dst


class CopyProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src, self.dst = _make_src(self.tmp.name)
        # SRC 和 DST 必须一起 mock：只 mock SRC 时 copy_profile 会把占位文件写进真实
        # 的 %LOCALAPPDATA%\fb-chrome-profile（2026-10-12 实测被写成 5 字节的
        # "state"/"prefs"，随后的盘口刷新就带着坏 profile 去起 Chrome）。
        self._patch("SRC", self.src)
        self._patch("DST", self.dst)

    def _patch(self, name: str, value):
        patcher = mock.patch.object(cdp_profile, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_profiles_are_never_the_target(self):
        """护栏：真实 profile 目录永远不参与测试。"""
        self.assertEqual(cdp_profile.SRC, self.src)
        self.assertEqual(cdp_profile.DST, self.dst)
        for path in (cdp_profile.SRC, cdp_profile.DST):
            self.assertTrue(str(path).startswith(tempfile.gettempdir()))

    def test_locked_source_is_skipped_not_raised(self):
        """默认 Chrome 运行时独占锁 Network/Cookies，复制应跳过而非抛错。"""
        (self.dst / "Default" / "Network").mkdir(parents=True)

        def fake_copy2(s, d):
            if str(s).endswith("Cookies"):
                raise PermissionError(32, "另一个程序正在使用此文件")
            Path(d).write_bytes(Path(s).read_bytes())

        with mock.patch.object(cdp_profile, "shutil") as m_shutil:
            m_shutil.copy2.side_effect = fake_copy2
            copied, skipped = cdp_profile.copy_profile()

        self.assertEqual(len(copied), 2)
        self.assertEqual(len(skipped), 1)
        self.assertIn("Cookies", skipped[0])
        # 目标 profile 没写过 Cookies：跳过就是不动它
        self.assertFalse((self.dst / "Default" / "Network" / "Cookies").exists())

    def test_normal_copy_copies_all(self):
        copied, skipped = cdp_profile.copy_profile()

        self.assertEqual(skipped, [])
        self.assertEqual(len(copied), 3)
        self.assertTrue((self.dst / "Default" / "Network" / "Cookies").exists())


if __name__ == "__main__":
    unittest.main()
