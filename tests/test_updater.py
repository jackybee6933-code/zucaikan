import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "project_updater", Path(__file__).parents[1] / "updater.py"
)
updater = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    def test_check_update_compares_versions(self):
        payload = {
            "version": "1.3",
            "url": "https://example.test/package.zip",
            "sha256": "a" * 64,
            "notes": "new",
        }
        with mock.patch.object(updater, "_http_json", return_value=payload):
            result = updater.check_update("https://example.test/latest.json", current_version="1.2")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "1.3")

        with mock.patch.object(updater, "_http_json", return_value={**payload, "version": "1.2"}):
            self.assertFalse(updater.check_update(current_version="1.2")["update_available"])

    def test_check_update_rejects_invalid_manifest(self):
        with mock.patch.object(updater, "_http_json", return_value={"version": "1.3"}):
            with self.assertRaises(updater.UpdateError):
                updater.check_update(current_version="1.2")

    def test_download_package_checks_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "package.zip"
            source.write_bytes(b"package")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()

            class Response:
                headers = {"Content-Length": "7"}

                def __init__(self):
                    self._sent = False

                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, size=-1):
                    if self._sent:
                        return b""
                    self._sent = True
                    return b"package"

            info = {"url": "https://example.test/package.zip", "sha256": digest}
            with mock.patch("urllib.request.urlopen", return_value=Response()):
                result = updater.download_package(info)
            self.assertEqual(result.read_bytes(), b"package")

    def test_install_downloaded_passes_current_pid_to_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / updater.APP_EXE_NAME).write_bytes(b"app")
            (root / updater.UPDATER_EXE_NAME).write_bytes(b"updater")
            archive = root / "package.zip"
            archive.write_bytes(b"zip")
            with mock.patch.object(updater, "app_root", return_value=root), \
                    mock.patch("subprocess.Popen") as popen:
                popen.return_value.pid = 4321
                result = updater.install_downloaded(archive)
        args = popen.call_args[0][0]
        self.assertIn("--kill-pid", args)
        self.assertEqual(args[args.index("--kill-pid") + 1], str(os.getpid()))
        self.assertIn("--restart", args)
        self.assertEqual(result["updater_pid"], 4321)

    def test_install_downloaded_accepts_legacy_updater_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / updater.APP_EXE_NAME).write_bytes(b"app")
            (root / updater.LEGACY_UPDATER_EXE_NAME).write_bytes(b"legacy")
            archive = root / "package.zip"
            archive.write_bytes(b"zip")
            with mock.patch.object(updater, "app_root", return_value=root), \
                    mock.patch("subprocess.Popen") as popen:
                popen.return_value.pid = 4322
                updater.install_downloaded(archive)
        self.assertEqual(popen.call_args[0][0][0], str(root / updater.LEGACY_UPDATER_EXE_NAME))

    def test_installer_archive_contains_target_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "package.zip"
            target = root / "足球胜负彩分析工具.exe"
            target.write_bytes(b"old")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("足球胜负彩分析工具.exe", b"new")
                zf.writestr("updater.exe", b"new updater")
            updater_script = Path(__file__).parents[1] / "update_installer.py"
            result = __import__("subprocess").run(
                [__import__("sys").executable, str(updater_script),
                 "--archive", str(archive), "--target", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual((root / "updater.exe").read_bytes(), b"new updater")


if __name__ == "__main__":
    unittest.main()
