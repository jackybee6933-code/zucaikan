import http.cookiejar
import json
import socket
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from football_analysis.query_app import QueryHTTPServer, serve_query
from football_analysis.query_report import render_query_html
from football_analysis.store import save_issue_movements
from football_analysis.web_access import QueryAccess


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PwaServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "test.sqlite"
        save_issue_movements(cls.db, [], "26167", "")
        cls.servers = []
        cls.threads = []
        for password, public_url in (("", ""), ("football-test-123", ""),
                                     ("football-test-123", "https://football.example")):
            ready = threading.Event()

            def create(address, handler):
                server = QueryHTTPServer(address, handler)
                cls.servers.append(server)
                ready.set()
                return server

            with mock.patch("football_analysis.query_app.QueryHTTPServer", side_effect=create):
                thread = threading.Thread(
                    target=serve_query, args=(cls.db,), daemon=True,
                    kwargs={"port": 0, "open_browser": False, "access_password": password,
                            "public_url": public_url},
                )
                thread.start()
                if not ready.wait(5):
                    raise AssertionError("PWA 服务未启动")
            cls.threads.append(thread)
        cls.local, cls.protected, cls.proxy = [
            f"http://127.0.0.1:{s.server_port}" for s in cls.servers
        ]

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
        for thread in cls.threads:
            thread.join(5)
        cls.tmp.cleanup()

    def request(self, base, path, *, data=None, headers=None, opener=None):
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            response = opener.open(req, timeout=5) if opener else urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def login(self, base=None, origin=None):
        base = base or self.protected
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, headers, body = self.request(
            base, "/login", opener=opener,
            data=urllib.parse.urlencode({"password": "football-test-123"}).encode(),
            headers={"Origin": origin or base, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 200)
        return opener

    def test_default_desktop_remains_open(self):
        status, headers, body = self.request(self.local, "/")
        self.assertEqual(status, 200)
        self.assertIn("足彩分析系统", body.decode())
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_manifest_and_icons_are_public(self):
        status, headers, body = self.request(self.protected, "/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("application/manifest+json", headers["Content-Type"])
        manifest = json.loads(body)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        for icon in manifest["icons"]:
            status, headers, body = self.request(self.protected, icon["src"])
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG"))
            size = int(icon["sizes"].split("x")[0])
            self.assertEqual(struct.unpack(">II", body[16:24]), (size, size))
        self.assertEqual({i["sizes"] for i in manifest["icons"]}, {"192x192", "512x512"})

    def test_worker_caches_only_explicit_static_assets(self):
        status, headers, body = self.request(self.local, "/service-worker.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["Service-Worker-Allowed"], "/")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        code = body.decode()
        self.assertIn("cache.addAll(STATIC_ASSETS)", code)
        self.assertIn("STATIC_ASSETS.includes(url.pathname)", code)
        self.assertNotIn("cache.put(event.request", code)
        self.assertNotIn('"/settings"', code)
        self.assertNotIn('"/backup"', code)
        self.assertIn('event.request.method !== "GET"', code)

    def test_offline_page_has_reconnect_but_no_match_data(self):
        status, _, body = self.request(self.local, "/pwa/offline.html")
        self.assertEqual(status, 200)
        self.assertIn("连接已断开", body.decode())
        self.assertIn("重新连接", body.decode())
        self.assertNotIn("26167", body.decode())

    def test_protected_routes_require_login(self):
        for path in ("/settings", "/backup", "/bets.csv", "/health", "/report/test.html"):
            status, headers, body = self.request(self.protected, path)
            self.assertEqual(status, 401, path)
            self.assertEqual(headers["Cache-Control"], "no-store")
        status, _, body = self.request(self.protected, "/")
        self.assertEqual(status, 200)
        self.assertIn('name="password"', body.decode())
        self.assertNotIn("数据库：", body.decode())

    def test_login_session_and_logout(self):
        opener = self.login()
        status, _, body = self.request(self.protected, "/settings", opener=opener)
        self.assertEqual(status, 200)
        self.assertIn("primary", json.loads(body))
        status, _, body = self.request(self.protected, "/logout", data=b"",
                                       headers={"Origin": self.protected}, opener=opener)
        self.assertEqual(status, 200)
        status, _, _ = self.request(self.protected, "/settings", opener=opener)
        self.assertEqual(status, 401)

    def test_invalid_password_never_sets_session(self):
        status, headers, _ = self.request(
            self.protected, "/login", data=b"password=wrong",
            headers={"Origin": self.protected, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)

    def test_post_requires_same_origin_even_after_login(self):
        opener = self.login()
        for origin in ("https://evil.example", "null", ""):
            status, _, _ = self.request(
                self.protected, "/settings", data=b"{}",
                headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})},
                opener=opener,
            )
            self.assertEqual(status, 403)
        status, _, _ = self.request(
            self.protected, "/settings", data=b"{}",
            headers={"Content-Type": "application/json", "Origin": self.protected},
        )
        self.assertEqual(status, 401)

    def test_cookie_security_and_proxy_origin(self):
        status, headers, _ = self.request(
            self.proxy, "/login", data=b"password=football-test-123",
            opener=urllib.request.build_opener(NoRedirect()),
            headers={"Origin": "https://football.example",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("football-test-123", cookie)
        status, _, _ = self.request(
            self.proxy, "/login", data=b"password=football-test-123",
            headers={"Origin": self.proxy},
        )
        self.assertEqual(status, 403)

    def test_remote_listener_without_password_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "密码"):
            serve_query(self.db, host="0.0.0.0", open_browser=False)

    def test_weak_password_and_invalid_public_url_are_rejected(self):
        with self.assertRaises(ValueError):
            serve_query(self.db, access_password="123", open_browser=False)
        for url in ("http://example.com", "https://user:pass@example.com", "https://example.com/path"):
            with self.assertRaises(ValueError):
                serve_query(self.db, access_password="football-test-123", public_url=url, open_browser=False)

    def test_https_proxy_does_not_silently_change_an_occupied_port(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with mock.patch("football_analysis.query_app.open_in_chrome") as browser:
                serve_query(self.db, port=occupied.getsockname()[1], access_password="football-test-123",
                            public_url="https://football.example", open_browser=True)
            browser.assert_not_called()

    def test_page_has_install_and_connection_controls(self):
        html = render_query_html([], issues=[], db_path="test.sqlite")
        self.assertIn('rel="manifest"', html)
        self.assertIn('src="/pwa/app.js"', html)
        self.assertIn('id="pwa-install-btn"', html)
        self.assertIn('id="pwa-connection"', html)
        self.assertIn('id="pwa-logout-btn"', html)


class AccessLimitTests(unittest.TestCase):
    def test_failed_logins_are_limited_and_recover_after_a_minute(self):
        access = QueryAccess("127.0.0.1", "football-test-123")
        handler = mock.Mock()
        handler.path = "/login"
        handler.headers = {"Origin": "http://localhost:8765", "Host": "localhost:8765",
                           "Content-Length": "14"}
        handler.rfile.read.return_value = b"password=wrong"
        with mock.patch("football_analysis.web_access.time.monotonic", return_value=100):
            for _ in range(10):
                access.handle(handler, "POST")
            access.handle(handler, "POST")
            handler.send_response.assert_called_with(429)
        with mock.patch("football_analysis.web_access.time.monotonic", return_value=161):
            access.handle(handler, "POST")
            handler.send_response.assert_called_with(401)

    def test_expired_session_is_not_accepted(self):
        access = QueryAccess("127.0.0.1", "football-test-123")
        access.sessions["expired"] = 100
        handler = mock.Mock()
        handler.headers = {"Cookie": "football_session=expired"}
        with mock.patch("football_analysis.web_access.time.monotonic", return_value=101):
            self.assertFalse(access._authenticated(handler))


if __name__ == "__main__":
    unittest.main()
