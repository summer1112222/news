"""build_digest.py 埋点注入与分享卡片标题的单元测试(stdlib unittest,零新增依赖)。

约定(见 docs/superpowers/specs/2026-11-26-phase-a-growth-design.md):
- 设 PAGES_BASE_URL 才注入埋点;beacon 目标为绝对 URL(后备 github.io 入口的访问也上报)
- 埋点 JS 全部 try/catch 静默失败,不收集任何个人信息(不含 wx/name/openid 等字段)
- 标题/og:title 变为「{today} · {n}类新闻精选 · {total}条」,PC 版附加「 · PC版」
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import build_digest as b

TODAY = "2026-11-27"


def fake_item(title="测试新闻标题内容", url="https://example.com/a?x=1"):
    return {"title": title, "url": url, "source": "example.com",
            "snippet": "这是摘要内容。", "date": TODAY}


def fake_cats():
    return [{"category": "📰 时政新闻",
             "domestic": [fake_item(url="https://example.com/dom")],
             "foreign": [fake_item(url="https://example.com/for")]}]


class EnvIsolated(unittest.TestCase):
    """每个用例前后清理 PAGES_BASE_URL,避免环境污染。"""

    def setUp(self):
        self._old = os.environ.get("PAGES_BASE_URL")
        os.environ.pop("PAGES_BASE_URL", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PAGES_BASE_URL", None)
        else:
            os.environ["PAGES_BASE_URL"] = self._old


class ShareCardTitleTest(EnvIsolated):
    def test_title_and_og(self):
        out = b.build_html(TODAY, fake_cats())
        expected = "2026-11-27 · 1类新闻精选 · 2条"
        self.assertIn(f"<title>{expected}</title>", out)
        self.assertIn(f'<meta property="og:title" content="{expected}">', out)

    def test_pc_title_suffix(self):
        out = b.build_html_pc(TODAY, fake_cats())
        self.assertIn("<title>2026-11-27 · 1类新闻精选 · 2条 · PC版</title>", out)
        # og:title 不带 PC 版标记(分享卡片统一文案)
        self.assertIn('<meta property="og:title" content="2026-11-27 · 1类新闻精选 · 2条">', out)


class BeaconInjectionTest(EnvIsolated):
    def test_beacon_injected_with_base_url(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com/"
        out = b.build_html(TODAY, fake_cats())
        self.assertIn("sendBeacon", out)
        self.assertIn("localStorage", out)
        self.assertIn("news-title", out)  # 页面主体仍在,注入没破坏渲染

    def test_beacon_absolute_url(self):
        # 绝对 URL(去尾斜杠):后备 github.io 入口的访问同样上报
        os.environ["PAGES_BASE_URL"] = "https://news.example.com/"
        out = b.build_html(TODAY, fake_cats())
        self.assertIn("https://news.example.com/beacon", out)

    def test_pc_also_injected(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com"
        out = b.build_html_pc(TODAY, fake_cats())
        self.assertIn("sendBeacon", out)

    def test_no_beacon_without_base_url(self):
        out = b.build_html(TODAY, fake_cats())
        self.assertNotIn("sendBeacon", out)
        out_pc = b.build_html_pc(TODAY, fake_cats())
        self.assertNotIn("sendBeacon", out_pc)


class BeaconJsSilenceTest(EnvIsolated):
    def _beacon_js(self):
        os.environ["PAGES_BASE_URL"] = "https://news.example.com"
        out = b.build_html(TODAY, fake_cats())
        blocks = re.findall(r"<script>(.*?)</script>", out, re.S)
        hits = [t for t in blocks if "sendBeacon" in t]
        self.assertEqual(len(hits), 1, "应恰好有一个含 sendBeacon 的 script 块")
        return hits[0]

    def test_beacon_js_silent(self):
        js = self._beacon_js()
        self.assertGreaterEqual(js.count("try"), 2, "IIFE 外层与上报函数都应有 try")
        self.assertGreaterEqual(js.count("catch"), 2, "catch 静默失败")
        # 不含个人身份信息字段
        for bad in ("wx", "name", "openid", "nickname", "phone", "email"):
            self.assertNotIn(bad, js, f"埋点 JS 不应包含 PII 字样: {bad}")

    def test_beacon_payload_fields(self):
        # 事件字段仅 vid/type/cat/region/item
        js = self._beacon_js()
        for field in ("type", "cat", "region", "item"):
            self.assertIn(field, js)


if __name__ == "__main__":
    unittest.main()
