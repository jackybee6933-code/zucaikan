"""Optional password access for the mobile/server query service."""
import hashlib
import hmac
import html
import secrets
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlparse


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#16705a"><link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/pwa/icon-192.png"><title>足彩分析系统 · 登录</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; background: #f3f5f7; color: #1d2733; font: 16px "Segoe UI", "Microsoft YaHei", sans-serif; }
main { max-width: 380px; margin: 12vh auto; padding: 24px; }
img { width: 64px; height: 64px; }
h1 { font-size: 24px; margin: 16px 0 24px; }
label { display: block; margin-bottom: 8px; }
input, button { width: 100%; min-height: 44px; font: inherit; border-radius: 6px; }
input { padding: 10px; border: 1px solid #d9dfe6; background: white; }
button { margin-top: 16px; border: 0; background: #16705a; color: white; cursor: pointer; }
.error { color: #b42318; min-height: 24px; overflow-wrap: anywhere; }
</style></head><body><main>
<img src="/pwa/icon-192.png" alt=""><h1>足彩分析系统</h1>
<form method="post" action="/login">
<label for="password">访问密码</label>
<input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512">
<button type="submit">登录</button><p class="error" role="alert">{error}</p>
</form></main></body></html>"""


class QueryAccess:
    COOKIE = "football_session"
    SESSION_SECONDS = 7 * 24 * 3600

    def __init__(self, host: str, password: str = "", public_url: str = ""):
        if host not in ("127.0.0.1", "localhost", "::1") or public_url:
            if not password:
                raise ValueError("远程访问必须配置 FOOTBALL_ACCESS_PASSWORD 访问密码")
        if password and len(password) < 12:
            raise ValueError("访问密码至少需要 12 个字符")
        parsed = urlparse(public_url)
        if public_url and (
            parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        ):
            raise ValueError("--public-url 必须为 HTTPS 根地址，如 https://football.example")
        self.origin = public_url.rstrip("/")
        self.enabled = bool(password)
        self.password_hash = hashlib.sha256(password.encode("utf-8")).digest()
        self.sessions: dict[str, float] = {}
        self.failures: deque[float] = deque(maxlen=10)
        self.lock = threading.Lock()

    def _cookie(self, handler) -> str:
        try:
            cookie = SimpleCookie(handler.headers.get("Cookie", ""))
            return cookie[self.COOKIE].value if self.COOKIE in cookie else ""
        except CookieError:
            return ""

    def _authenticated(self, handler) -> bool:
        token = self._cookie(handler)
        with self.lock:
            return self.sessions.get(token, 0) > time.monotonic()

    def _page(self, handler, status=200, error=""):
        body = LOGIN_HTML.replace("{error}", html.escape(error)).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _session_cookie(self, token: str, max_age: int) -> str:
        secure = "; Secure" if self.origin else ""
        return f"{self.COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

    def handle(self, handler, method: str) -> bool:
        if not self.enabled:
            return False
        path = urlparse(handler.path).path
        if method == "POST":
            expected = self.origin or f"http://{handler.headers.get('Host', '')}"
            if handler.headers.get("Origin") != expected or handler.headers.get("Sec-Fetch-Site") == "cross-site":
                handler._json(403, {"error": "仅允许本站发起操作"})
                return True
            if path == "/login":
                try:
                    length = int(handler.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError()
                    data = parse_qs(handler.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeError):
                    self._page(handler, 400, "登录请求无效")
                    return True
                with self.lock:
                    now = time.monotonic()
                    while self.failures and self.failures[0] <= now - 60:
                        self.failures.popleft()
                    limited = len(self.failures) >= 10
                    valid = hmac.compare_digest(
                        hashlib.sha256(data.get("password", [""])[0].encode("utf-8")).digest(),
                        self.password_hash,
                    )
                    if not limited and not valid:
                        self.failures.append(now)
                    if not limited and valid:
                        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
                        old = self._cookie(handler)
                        self.sessions.pop(old, None)
                        if len(self.sessions) >= 64:
                            self.sessions.pop(next(iter(self.sessions)))
                        token = secrets.token_urlsafe(32)
                        self.sessions[token] = now + self.SESSION_SECONDS
                if limited:
                    self._page(handler, 429, "尝试次数过多，请一分钟后重试")
                elif not valid:
                    self._page(handler, 401, "访问密码错误")
                else:
                    handler.send_response(303)
                    handler.send_header("Location", "/")
                    handler.send_header("Set-Cookie", self._session_cookie(token, self.SESSION_SECONDS))
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                return True
        elif path == "/login":
            self._page(handler)
            return True
        if not self._authenticated(handler):
            if method == "GET" and path == "/":
                handler.send_response(303)
                handler.send_header("Location", "/login")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
            else:
                handler._json(401, {"error": "登录已失效，请重新登录", "login": "/login"})
            return True
        if method == "POST" and path == "/logout":
            with self.lock:
                self.sessions.pop(self._cookie(handler), None)
            handler.send_response(200)
            handler.send_header("Set-Cookie", self._session_cookie("", 0))
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        return False
