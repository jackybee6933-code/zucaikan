import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from tools.archive_release import archive_current_release


class ArchiveReleaseTests(unittest.TestCase):
    def test_archives_release_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "football_analysis").mkdir()
            (root / "football_analysis" / "__init__.py").write_text(
                '__version__ = "1.2.3"\n', encoding="utf-8"
            )
            dist = root / "dist"
            (dist / "tools").mkdir(parents=True)
            for rel in ("football_analysis.exe", "updater.exe", "latest.json"):
                (dist / rel).write_text(rel, encoding="utf-8")
            (dist / "cache").mkdir()
            (dist / "cache" / "runtime.raw").write_text("do not archive", encoding="utf-8")

            archive = archive_current_release(
                root=root,
                dist=dist,
                history=dist / "history",
                now=datetime(2026, 10, 31, 12, 34, 56),
            )

            self.assertIsNotNone(archive)
            self.assertEqual(archive.name, "football_analysis_v1.2.3_20261031_123456.zip")
            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(
                    set(zf.namelist()),
                    {
                        "football_analysis.exe",
                        "updater.exe",
                        "latest.json",
                        "archive_manifest.json",
                    },
                )
                manifest = json.loads(zf.read("archive_manifest.json"))
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertNotIn("cache/runtime.raw", manifest["files"])

    def test_empty_dist_is_allowed_for_first_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "football_analysis").mkdir()
            (root / "football_analysis" / "__init__.py").write_text(
                '__version__ = "1.0"\n', encoding="utf-8"
            )
            self.assertIsNone(archive_current_release(root=root, dist=root / "dist"))


if __name__ == "__main__":
    unittest.main()
