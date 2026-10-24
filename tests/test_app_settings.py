import json
import tempfile
import unittest
from pathlib import Path

from football_analysis.app_settings import load_scrape_delay, save_scrape_delay


class AppSettingsTests(unittest.TestCase):
    def test_missing_file_is_created_with_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "app_settings.json"
            self.assertEqual(load_scrape_delay(path), 5.0)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"scrape_delay_seconds": 5.0},
            )

    def test_saved_delay_is_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app_settings.json"
            self.assertEqual(save_scrape_delay(7.5, path), 7.5)
            self.assertEqual(load_scrape_delay(path), 7.5)

    def test_invalid_delay_falls_back_and_negative_is_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app_settings.json"
            path.write_text('{"scrape_delay_seconds": "bad"}', encoding="utf-8")
            self.assertEqual(load_scrape_delay(path), 5.0)
            self.assertEqual(save_scrape_delay(-3, path), 0.0)
            self.assertEqual(load_scrape_delay(path), 0.0)


if __name__ == "__main__":
    unittest.main()
