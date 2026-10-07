import tempfile
import unittest
from unittest import mock

from football_analysis.http import Fetcher, PageCache, _is_challenge


CHALLENGE_BODY = (
    b"<script>function a(a){function n(){for(var a={wQzOV:_0x649a(\"0x4\")},"
    b"e=0;;){switch(n[e++]){case\"0\":t+=\"EO_Bot_Ssid=\";continue;ca"
)


class ChallengeGuardTests(unittest.TestCase):
    def test_detects_edgeone_challenge(self):
        self.assertTrue(_is_challenge(CHALLENGE_BODY))

    def test_ignores_real_content(self):
        self.assertFalse(_is_challenge(b"<?xml version=\"1.0\"?><matches><match/></matches>"))
        self.assertFalse(_is_challenge(b"<html><body>normal page</body></html>"))
        self.assertFalse(_is_challenge(b""))

    def test_challenge_page_not_cached_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = Fetcher(cache_dir=tmp, retries=0)
            with mock.patch("football_analysis.http.requests.get") as mock_get:
                mock_get.return_value = mock.Mock(status_code=200, content=CHALLENGE_BODY)
                with self.assertRaises(RuntimeError):
                    fetcher.fetch("https://odds.500.com/fenxi/daxiao-1.shtml", cache_key="k1")
            # 关键：挑战脚本绝不能进缓存，否则后续重跑会一直读到无效内容
            self.assertIsNone(fetcher.cache.load("k1", "html"))

    def test_normal_page_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = Fetcher(cache_dir=tmp, retries=0)
            with mock.patch("football_analysis.http.requests.get") as mock_get:
                mock_get.return_value = mock.Mock(status_code=200, content=b"<html>ok</html>")
                result = fetcher.fetch("https://example.com/page", cache_key="k2")
            self.assertEqual(result, "<html>ok</html>")
            self.assertEqual(fetcher.cache.load("k2", "html"), "<html>ok</html>")




class PageCacheEvictTests(unittest.TestCase):
    def test_evict_removes_cached_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = PageCache(tmp)
            cache.save("detail:1", b"<html>old</html>")
            cache.evict("detail:1")
            self.assertIsNone(cache.load("detail:1", "html"))

    def test_evict_missing_key_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = PageCache(tmp)
            cache.evict("never:saved")  # 不存在也不报错
            self.assertIsNone(cache.load("never:saved", "html"))
if __name__ == "__main__":
    unittest.main()
