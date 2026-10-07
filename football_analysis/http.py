from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def decode_bytes(raw: bytes, kind: str = "html") -> str:
    first_bytes = raw[:200].lower()
    if raw.startswith(b"\xef\xbb\xbf") or b"charset=utf-8" in raw[:4000].lower():
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    if b"<?xml" in first_bytes or b"<matches>" in first_bytes:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gb18030", errors="replace")
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        # 500.com 页面可能混入少量非法 GBK 字节；先用 GB 容错解码，
        # 避免把 GBK 字节误按 UTF-8 解码成乱码。
        return raw.decode("gb18030", errors="replace")


class PageCache:
    """Minimal raw-page cache for repeat runs and debugging."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else None

    def key_path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{digest}.raw"

    def load(self, key: str, kind: str) -> str | None:
        if not self.root:
            return None
        path = self.key_path(key)
        if not path.exists():
            return None
        return decode_bytes(path.read_bytes(), kind)

    def save(self, key: str, raw: bytes) -> None:
        if not self.root:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_path(key).write_bytes(raw)

    def evict(self, key: str) -> None:
        if not self.root:
            return
        self.key_path(key).unlink(missing_ok=True)


def _is_challenge(raw: bytes) -> bool:
    """EdgeOne 等反爬返回 200 但内容是 JS 挑战脚本，不含页面数据。"""
    return b"EO_Bot_Ssid" in raw[:4000]

class Fetcher:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        timeout: int = 30,
        retries: int = 2,
        sleep_base: float = 0.6,
    ):
        self.cache = PageCache(cache_dir)
        self.timeout = timeout
        self.retries = retries
        self.sleep_base = sleep_base

    def _additional_headers(self, url: str) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        if "odds.xml" in url:
            headers["Referer"] = "https://trade.500.com/jczq/"
        elif "liansai.500.com" in url:
            headers["Referer"] = "https://liansai.500.com/"
        elif "trade.500.com" in url:
            headers["Referer"] = "https://trade.500.com/"
        elif "m.nowscore.com" in url:
            headers["Referer"] = "https://m.titan007.com/"
        elif "titan007.com" in url:
            headers["Referer"] = "https://jc.titan007.com/"
        return headers

    def fetch(
        self,
        url: str,
        *,
        kind: str = "html",
        cache_key: str | None = None,
        use_cache: bool = True,
    ) -> str:
        key = cache_key or url
        if use_cache:
            cached = self.cache.load(key, kind)
            if cached is not None:
                return cached

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=self._additional_headers(url),
                    timeout=self.timeout,
                )
                if response.status_code in (429, 403, 503):
                    raise RuntimeError(f"HTTP {response.status_code} for {url}")
                response.raise_for_status()
                raw = response.content
                if _is_challenge(raw):
                    raise RuntimeError(f"反爬 JS 挑战，未拿到页面内容：{url}")
                if use_cache:
                    self.cache.save(key, raw)
                return decode_bytes(raw, kind)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.sleep_base * (attempt + 1))
        raise RuntimeError(f"抓取失败 {url}: {last_error}")


def extract_detail_id(url_or_html: str) -> str:
    match = re.search(r"shuju-(\d+)\.shtml", url_or_html)
    if match:
        return match.group(1)
    match = re.search(r"fid=(\d+)", url_or_html)
    return match.group(1) if match else ""
