import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from football_analysis.backup import build_backup_bytes, restore_backup, write_backup


class BackupTests(unittest.TestCase):
    def test_round_trip_and_safety_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "data" / "football.sqlite"
            settings = root / "data" / "llm_settings.json"
            db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db)
            try:
                conn.execute("create table t (value text)")
                conn.execute("insert into t values ('before')")
                conn.commit()
            finally:
                conn.close()
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"primary": {"model": "old"}}), encoding="utf-8")
            archive = root / "first.zip"
            write_backup(db, archive, settings)

            conn = sqlite3.connect(db)
            try:
                conn.execute("delete from t")
                conn.execute("insert into t values ('changed')")
                conn.commit()
            finally:
                conn.close()
            settings.write_text(json.dumps({"primary": {"model": "changed"}}), encoding="utf-8")

            safety = restore_backup(archive, db, settings, root / "backups")
            self.assertTrue(safety and safety.is_file())
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("select value from t").fetchone()[0], "before")
            finally:
                conn.close()
            self.assertIn('"old"', settings.read_text(encoding="utf-8"))

    def test_invalid_archive_does_not_replace_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            conn = sqlite3.connect(db)
            try:
                conn.execute("create table t (value text)")
                conn.execute("insert into t values ('keep')")
                conn.commit()
            finally:
                conn.close()
            bad = root / "bad.zip"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                restore_backup(bad, db)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("select value from t").fetchone()[0], "keep")
            finally:
                conn.close()

    def test_manifest_is_present(self):
        data = build_backup_bytes(Path("missing.sqlite"))
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            self.assertIn("manifest.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
