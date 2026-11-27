"""昨日统计拉取与展示(_fetch_yesterday_stats / build_* stats 参数)的单元测试。

约定(见 docs/superpowers/specs/2026-11-26-phase-a-growth-design.md):
- GET {PAGES_BASE_URL}/stats?date=<昨日>&token=<STATS_READ_TOKEN>,超时 2 秒
- 任何失败(未设 env / 网络 / 非 200)→ None 且不阻塞主流水线,stderr 只打异常类型名
- 展示条件:stats 非 None 且 unique_vids > 0;页面「昨日 N 人阅读 · 最热:X」,微信「 · 昨日N人阅读」
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import build_digest as b

TODAY = "2026-11-27"


def fake_item(url="https://example.com/a"):
    return {"title": "测试新闻标题内容", "url": url, "source": "example.com",
            "snippet": "这是摘要内容。", "date": TODAY}


def fake_cats():
    return [{"category": "📰 时政新闻",
             "domestic": [fake_item(url="https://example.com/dom")],
             "foreign": [fake_item(url="https://example.com/for")]}]


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FetchStatsTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("PAGES_BASE_URL", "STATS_READ_TOKEN")}
        self._old_get = b.requests.get
        b.requests.get = lambda *a, **kw: FakeResponse({"unique_vids": 8, "views": 10, "by_cat": {}, "click_total": 0, "clicks": []})

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
        b.requests.get = self._old_get

    def test_fetch_no_env_returns_none(self):
        self.assertIsNone(b._fetch_yesterday_stats(TODAY))

    def test_fetch_failure_returns_none(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com"
        os.environ["STATS_READ_TOKEN"] = "tok"

        def boom(*a, **kw):
            raise b.requests.exceptions.Timeout("timed out")

        b.requests.get = boom
        self.assertIsNone(b._fetch_yesterday_stats(TODAY))

    def test_fetch_http_error_returns_none(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com"
        os.environ["STATS_READ_TOKEN"] = "tok"
        b.requests.get = lambda *a, **kw: FakeResponse({}, status=500)
        self.assertIsNone(b._fetch_yesterday_stats(TODAY))

    def test_fetch_ok(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com/"
        os.environ["STATS_READ_TOKEN"] = "tok"
        captured = {}

        def fake_get(url, params=None, **kw):
            captured.update(params or {})
            self.assertIn("/stats", url)
            return FakeResponse({"unique_vids": 8, "views": 10, "by_cat": {}, "click_total": 0, "clicks": []})

        b.requests.get = fake_get
        got = b._fetch_yesterday_stats(TODAY)
        self.assertEqual(got["unique_vids"], 8)
        # 请求参数带昨日日期与 token
        self.assertEqual(captured["date"], "2026-11-26")
        self.assertEqual(captured["token"], "tok")


class DisplayStatsTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("PAGES_BASE_URL", "STATS_READ_TOKEN")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_header_shows_stats(self):
        stats = {"unique_vids": 8, "views": 10, "by_cat": {"时政": 3, "财经": 1},
                 "click_total": 0, "clicks": []}
        out = b.build_html(TODAY, fake_cats(), stats=stats)
        self.assertIn("昨日 8 人阅读", out)
        self.assertIn("最热:时政", out)

    def test_header_hides_when_zero_or_none(self):
        out_none = b.build_html(TODAY, fake_cats(), stats=None)
        self.assertNotIn("昨日", out_none)
        out_zero = b.build_html(TODAY, fake_cats(), stats={"unique_vids": 0, "by_cat": {}})
        self.assertNotIn("昨日", out_zero)

    def test_wechat_msg_stats_line(self):
        stats = {"unique_vids": 8, "views": 10, "by_cat": {}, "click_total": 0, "clicks": []}
        title, md = b.build_wechat_msg(TODAY, fake_cats(), "https://news.example.com", stats=stats)
        self.assertIn("昨日8人阅读", md)
        title2, md2 = b.build_wechat_msg(TODAY, fake_cats(), "https://news.example.com", stats=None)
        self.assertNotIn("昨日", md2)

    def test_pc_and_index_consistent(self):
        stats = {"unique_vids": 5, "views": 6, "by_cat": {"时政": 2}, "click_total": 0, "clicks": []}
        out_pc = b.build_html_pc(TODAY, fake_cats(), stats=stats)
        self.assertIn("昨日 5 人阅读", out_pc)
        out_idx = b.build_html(TODAY, fake_cats(), stats=stats)
        self.assertIn("昨日 5 人阅读", out_idx)


class MalformedStatsTest(unittest.TestCase):
    """评审修复(Important #2):畸形 stats 不得炸掉渲染——统计故障绝不阻塞主流水线。"""

    def test_stats_line_malformed_by_cat_does_not_raise(self):
        # by_cat 值混型:不崩,昨日行仍在,最热取整数值的键
        line = b._stats_line({"unique_vids": 3, "by_cat": {"a": 1, "b": "x"}})
        self.assertIn("昨日 3 人阅读", line)
        # by_cat 是 truthy list:不崩,退化为只显示昨日行
        line2 = b._stats_line({"unique_vids": 3, "by_cat": ["x"]})
        self.assertIn("昨日 3 人阅读", line2)
        self.assertNotIn("最热", line2)

    def test_stats_line_malformed_unique_vids_returns_empty(self):
        self.assertEqual(b._stats_line({"unique_vids": "8x", "by_cat": {}}), "")
        self.assertEqual(b._stats_line({"unique_vids": None, "by_cat": {}}), "")
        self.assertEqual(b._stats_line({"unique_vids": [1], "by_cat": {}}), "")

    def test_build_html_malformed_stats_no_crash(self):
        out = b.build_html(TODAY, fake_cats(),
                           stats={"unique_vids": 3, "by_cat": {"a": 1, "b": "x"}})
        self.assertIn("昨日 3 人阅读", out)


if __name__ == "__main__":
    unittest.main()
