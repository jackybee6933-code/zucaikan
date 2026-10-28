import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _scraper():
    from football_analysis import scrape_odds_pages
    return scrape_odds_pages


class ScraperPackagingTests(unittest.TestCase):
    def test_compatibility_script_forwards_to_packaged_module(self):
        source = (ROOT / "tools" / "scrape_odds_pages.py").read_text(encoding="utf-8-sig")
        self.assertIn("from football_analysis.scrape_odds_pages import main", source)
        self.assertNotIn("CdpPage", source)


class PartialFileTests(unittest.TestCase):
    """断点文件只保存 ok/empty，过期或损坏一律当没有。"""

    def setUp(self):
        self.mod = _scraper()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.mod.partial_path(self.dir, "26144")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_keeps_only_confirmed_pages(self):
        self.mod.save_partial(
            self.path,
            {
                "1|yazhi": {"status": "ok", "rows": [[1]]},
                "1|daxiao": {"status": "timeout", "rows": []},
                "1|shuju": {"status": "empty", "text": ""},
            },
        )
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"1|yazhi", "1|shuju"})
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_load_filters_wanted_and_failed_pages(self):
        self.path.write_text(
            json.dumps(
                {
                    "1|yazhi": {"status": "ok"},
                    "1|daxiao": {"status": "error"},
                    "2|yazhi": {"status": "ok"},
                }
            ),
            encoding="utf-8",
        )
        got = self.mod.load_partial(self.path, {"1|yazhi", "1|daxiao"})
        self.assertEqual(set(got), {"1|yazhi"})

    def test_expired_partial_is_removed(self):
        self.path.write_text(json.dumps({"1|yazhi": {"status": "ok"}}), encoding="utf-8")
        old = time.time() - (self.mod.PARTIAL_TTL_HOURS + 1) * 3600
        os.utime(self.path, (old, old))
        self.assertEqual(self.mod.load_partial(self.path, {"1|yazhi"}), {})
        self.assertFalse(self.path.exists())

    def test_broken_partial_is_ignored(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.mod.load_partial(self.path, {"1|yazhi"}), {})


class ResumeMainTests(unittest.TestCase):
    """上次中断存下的页不再重抓，只补缺的。"""

    def test_skips_pages_already_in_partial(self):
        mod = _scraper()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            mod.partial_path(cache, "26144").write_text(
                json.dumps(
                    {"111|yazhi": {"status": "ok", "rows": [[293, "0.9", "平手", "0.9"]]}}
                ),
                encoding="utf-8",
            )
            fetched = []

            def fake_fetch(page, url, wait, **kwargs):
                fetched.append(url)
                return {"status": "ok", "rows": [[293, "0.8", "平手", "1.0", "0.9"]]}

            page = mock.MagicMock()
            with mock.patch.object(
                mod, "collect_sfc_matches", return_value=([{"fixture_id": "111"}, {"fixture_id": "222"}], "26144")
            ), mock.patch.object(mod, "acquire", return_value=page), mock.patch.object(
                mod, "fetch_page", side_effect=fake_fetch
            ), mock.patch.object(mod.time, "sleep"):
                code = mod.main(
                    ["--issue", "26144", "--cache-dir", str(cache), "--kinds", "yazhi", "--delay", "0"]
                )

            self.assertEqual(code, 0)
            self.assertEqual(fetched, ["https://odds.500.com/fenxi/yazhi-222.shtml"])
            saved = json.loads((cache / "odds_pages_26144.json").read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {"111|yazhi", "222|yazhi"})
            metadata = json.loads(
                (cache / "odds_pages_26144.meta.json").read_text(encoding="utf-8")
            )
            self.assertIn("yazhi", metadata)
            self.assertNotIn("daxiao", metadata)
            self.assertFalse(mod.partial_path(cache, "26144").exists())

    def test_empty_shuju_stops_remaining_pages(self):
        mod = _scraper()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            fetched = []

            def fake_fetch(page, url, wait, **kwargs):
                fetched.append(url)
                return {"status": "empty", "text": ""}

            page = mock.MagicMock()
            with mock.patch.object(
                mod,
                "collect_sfc_matches",
                return_value=(
                    [{"fixture_id": "111"}, {"fixture_id": "222"}, {"fixture_id": "333"}],
                    "26144",
                ),
            ), mock.patch.object(mod, "acquire", return_value=page), mock.patch.object(
                mod, "fetch_page", side_effect=fake_fetch
            ), mock.patch.object(mod.time, "sleep"):
                code = mod.main(
                    [
                        "--issue",
                        "26144",
                        "--cache-dir",
                        str(cache),
                        "--kinds",
                        "shuju",
                        "--delay",
                        "0",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(fetched, ["https://odds.500.com/fenxi/shuju-111.shtml"])
            saved = json.loads((cache / "odds_pages_26144.json").read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {"111|shuju"})
            metadata = json.loads(
                (cache / "odds_pages_26144.meta.json").read_text(encoding="utf-8")
            )
            self.assertIn("shuju", metadata)


if __name__ == "__main__":
    unittest.main()
