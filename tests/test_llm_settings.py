import os
import requests
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from football_analysis.llm import endpoint_config, fallback_config, test_endpoint
from football_analysis.llm_settings import public_settings, save_settings


class LlmSettingsTests(unittest.TestCase):
    def test_save_and_public_settings_do_not_expose_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "llm_settings.json"
            save_settings(
                {
                    "primary": {
                        "api_key": "primary-secret",
                        "base_url": "https://primary.example/v1",
                        "model": "primary-model",
                    },
                    "fallback": {
                        "api_key": "backup-secret",
                        "base_url": "https://backup.example/v1",
                        "model": "backup-model",
                    },
                },
                path,
            )
            public = public_settings(path)
            self.assertNotIn("api_key", public["primary"])
            self.assertEqual(public["primary"]["api_key_hint"], "••••cret")
            self.assertTrue(public["fallback"]["api_key_configured"])

    def test_endpoint_config_prefers_saved_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "llm_settings.json"
            save_settings(
                {"primary": {"api_key": "saved-key", "base_url": "https://saved/v1", "model": "saved-model"}},
                path,
            )
            old = os.getcwd()
            try:
                os.chdir(tmp)
                base, key, model = endpoint_config()
            finally:
                os.chdir(old)
            self.assertEqual((base, key, model), ("https://saved/v1", "saved-key", "saved-model"))

    def test_fallback_config_accepts_explicit_args(self):
        self.assertEqual(
            fallback_config(
                base_url="https://backup/v1/", api_key="key", model="backup-model"
            ),
            ("https://backup/v1", "key", "backup-model"),
        )

    def test_test_endpoint_uses_saved_key_without_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "llm_settings.json"
            save_settings(
                {
                    "primary": {
                        "api_key": "saved-key",
                        "base_url": "https://primary.example/v1",
                        "model": "primary-model",
                    }
                },
                path,
            )
            response = mock.Mock(status_code=200)
            with mock.patch("football_analysis.llm.requests.post", return_value=response) as post:
                result = test_endpoint("primary", settings_path=path)
            self.assertTrue(result["ok"])
            self.assertIn("连接成功", result["message"])
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer saved-key")
            self.assertEqual(
                post.call_args.args[0],
                "https://primary.example/v1/chat/completions",
            )

    def test_test_endpoint_reports_common_http_failures_without_response_body(self):
        for code, expected in (
            (401, "API Key 无效"),
            (403, "没有访问权限"),
            (404, "URL 或模型不存在"),
            (429, "请求过于频繁"),
        ):
            with self.subTest(code=code):
                response = mock.Mock(status_code=code)
                with mock.patch("football_analysis.llm.requests.post", return_value=response):
                    result = test_endpoint(
                        "fallback",
                        base_url="https://example.test/v1",
                        api_key="key",
                        model="model",
                    )
                self.assertFalse(result["ok"])
                self.assertIn(expected, result["message"])

    def test_test_endpoint_respects_clear_key_and_timeout(self):
        result = test_endpoint(
            "primary",
            base_url="https://example.test/v1",
            api_key="",
            model="model",
            use_saved_key=False,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "未配置 API Key")

        with mock.patch("football_analysis.llm.requests.post", side_effect=requests.Timeout):
            result = test_endpoint(
                "primary",
                base_url="https://example.test/v1",
                api_key="key",
                model="model",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "连接超时")


if __name__ == "__main__":
    unittest.main()
