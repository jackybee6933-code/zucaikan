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


class VerificationPauseTests(unittest.TestCase):
    def test_verification_detection_uses_title_body_and_url_but_not_empty_odds(self):
        mod = _scraper()

        self.assertTrue(mod.is_verification_page("Security Verification"))
        self.assertTrue(mod.is_verification_page("Access check", body="Please verify you are human"))
        self.assertTrue(
            mod.is_verification_page(
                "Checking browser",
                url="https://odds.500.com/captcha/challenge",
            )
        )
        self.assertFalse(
            mod.is_verification_page(
                "Asian Odds",
                url="https://odds.500.com/fenxi/yazhi-111.shtml",
                body="No company rows available",
            )
        )

    def test_changed_title_with_verification_body_pauses_then_recovers(self):
        mod = _scraper()
        page = mock.MagicMock()
        page.title.side_effect = ["Access check", "Odds page"]
        page.evaluate.side_effect = [
            1,
            {"url": "https://odds.500.com/fenxi/yazhi-111.shtml", "text": "Security Verification"},
            {"url": "https://odds.500.com/fenxi/yazhi-111.shtml", "text": "Asian odds"},
            False,
            [["company", "line"]],
        ]
        phases = []

        with mock.patch.object(mod, "POLL_SECONDS", 0.001):
            result = mod.fetch_page(
                page,
                "https://odds.500.com/fenxi/yazhi-111.shtml",
                0.1,
                on_verification=lambda: phases.append("required"),
                on_verification_resolved=lambda: phases.append("resolved"),
            )

        self.assertEqual(result, {"status": mod.STATUS_OK, "rows": [["company", "line"]]})
        self.assertEqual(phases, ["required", "resolved"])
        page.goto.assert_called_once()

    def test_normal_zero_company_page_remains_empty(self):
        mod = _scraper()
        page = mock.MagicMock()
        page.title.return_value = "Asian Odds"
        page.evaluate.side_effect = [
            1,
            {"url": "https://odds.500.com/fenxi/yazhi-111.shtml", "text": "No company rows"},
            False,
            [],
        ]

        with mock.patch.object(mod, "SETTLE_POLLS", 1):
            result = mod.fetch_page(
                page,
                "https://odds.500.com/fenxi/yazhi-111.shtml",
                0.1,
            )

        self.assertEqual(result, {"status": mod.STATUS_EMPTY, "rows": []})

    def test_manual_verification_resumes_extraction_on_current_page(self):
        mod = _scraper()
        page = mock.MagicMock()
        page.title.side_effect = [mod.VERIFY_MARK, "Odds page"]
        page.evaluate.side_effect = [
            1,
            {"url": "https://example.test/odds", "text": "Asian odds"},
            False,
            [["company", "line"]],
        ]
        phases = []

        with mock.patch.object(mod, "POLL_SECONDS", 0.001):
            result = mod.fetch_page(
                page,
                "https://example.test/odds",
                0.1,
                on_verification=lambda: phases.append("required"),
                on_verification_resolved=lambda: phases.append("resolved"),
            )

        self.assertEqual(result, {"status": mod.STATUS_OK, "rows": [["company", "line"]]})
        self.assertEqual(phases, ["required", "resolved"])
        page.goto.assert_called_once()
        self.assertEqual(page.evaluate.call_count, 4)

    def test_fetch_page_reports_verification_and_times_out_as_action_required(self):
        mod = _scraper()
        page = mock.MagicMock()
        page.evaluate.return_value = 1
        page.title.return_value = mod.VERIFY_MARK
        notified = []

        with mock.patch.object(mod, "POLL_SECONDS", 0.001):
            result = mod.fetch_page(
                page,
                "https://example.test/odds",
                0.01,
                on_verification=lambda: notified.append(True),
            )

        self.assertEqual(notified, [True])
        self.assertEqual(result["status"], mod.STATUS_VERIFICATION_REQUIRED)

    def test_verification_timeout_stops_remaining_pages(self):
        mod = _scraper()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            fetched = []

            def fake_fetch(page, url, wait, **kwargs):
                fetched.append(url)
                return {"status": mod.STATUS_VERIFICATION_REQUIRED, "rows": []}

            page = mock.MagicMock()
            with mock.patch.object(
                mod,
                "collect_sfc_matches",
                return_value=(
                    [{"fixture_id": "111"}, {"fixture_id": "222"}],
                    "26144",
                ),
            ), mock.patch.object(mod, "acquire", return_value=page), mock.patch.object(
                mod, "fetch_page", side_effect=fake_fetch
            ):
                code = mod.main(
                    [
                        "--issue",
                        "26144",
                        "--cache-dir",
                        str(cache),
                        "--kinds",
                        "yazhi",
                        "--delay",
                        "0",
                    ]
                )

            self.assertEqual(code, mod.EXIT_VERIFICATION_REQUIRED)
            self.assertEqual(fetched, ["https://odds.500.com/fenxi/yazhi-111.shtml"])
            progress = json.loads((cache / ".ai_scrape_progress").read_text(encoding="utf-8"))
            self.assertEqual(progress["phase"], "aborted")
            self.assertEqual(progress["reason"], "verification_required")
            self.assertFalse((cache / "odds_pages_26144.json").exists())

    def test_retry_after_verification_timeout_resumes_from_saved_page(self):
        mod = _scraper()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            matches = (
                [
                    {"fixture_id": "111"},
                    {"fixture_id": "222"},
                    {"fixture_id": "333"},
                ],
                "26144",
            )
            page = mock.MagicMock()
            first_attempt = []

            def interrupted_fetch(page, url, wait, **kwargs):
                first_attempt.append(url)
                if url.endswith("-111.shtml"):
                    return {"status": "ok", "rows": [["company", "line"]]}
                return {"status": mod.STATUS_VERIFICATION_REQUIRED, "rows": []}

            with mock.patch.object(mod, "collect_sfc_matches", return_value=matches), mock.patch.object(
                mod, "acquire", return_value=page
            ), mock.patch.object(mod, "fetch_page", side_effect=interrupted_fetch), mock.patch.object(
                mod.time, "sleep"
            ):
                code = mod.main(
                    [
                        "--issue",
                        "26144",
                        "--cache-dir",
                        str(cache),
                        "--kinds",
                        "yazhi",
                        "--delay",
                        "0",
                    ]
                )

            self.assertEqual(code, mod.EXIT_VERIFICATION_REQUIRED)
            self.assertEqual(len(json.loads(mod.partial_path(cache, "26144").read_text(encoding="utf-8"))), 1)

            retry_fetches = []

            def successful_fetch(page, url, wait, **kwargs):
                retry_fetches.append(url)
                return {"status": "ok", "rows": [["company", "line"]]}

            with mock.patch.object(mod, "collect_sfc_matches", return_value=matches), mock.patch.object(
                mod, "acquire", return_value=page
            ), mock.patch.object(mod, "fetch_page", side_effect=successful_fetch), mock.patch.object(
                mod.time, "sleep"
            ):
                code = mod.main(
                    [
                        "--issue",
                        "26144",
                        "--cache-dir",
                        str(cache),
                        "--kinds",
                        "yazhi",
                        "--delay",
                        "0",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                retry_fetches,
                [
                    "https://odds.500.com/fenxi/yazhi-222.shtml",
                    "https://odds.500.com/fenxi/yazhi-333.shtml",
                ],
            )


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
