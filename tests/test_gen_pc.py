"""gen_pc_from_html.py 的回归测试(评审修复 Critical #1)。

分享卡片标题改为「{today} · {n}类新闻精选 · {N}条」后,该工具的日期提取正则
曾不再匹配 → sys.exit(1)。本测试用子进程 + 临时目录跑真实脚本,守护两代标题格式。
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPT = os.path.join(REPO, "scripts", "gen_pc_from_html.py")

INDEX_FIXTURE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<section class="panel" data-panel="0">
<h2 class="panel-title">时政</h2>
<div class="news-group domestic">
<article class="news"><a class="news-title" href="https://e.com/2026/11-27/a">国内测试标题</a>
<p class="news-snippet">国内摘要内容。</p></article>
</div>
<div class="news-group foreign">
<article class="news"><a class="news-title" href="https://e.com/2026/11-27/b">国外测试标题</a></article>
</div>
</section>
</body></html>
"""


class GenPcFromHtmlTest(unittest.TestCase):
    def _run_with_title(self, title):
        # 注意:TemporaryDirectory 在 with 退出时清理,产物存在性必须在块内判定
        with tempfile.TemporaryDirectory() as tmp:
            pub = os.path.join(tmp, "public")
            os.makedirs(pub)
            with open(os.path.join(pub, "index.html"), "w", encoding="utf-8") as f:
                f.write(INDEX_FIXTURE.format(title=title))
            r = subprocess.run(
                [sys.executable, SCRIPT], cwd=tmp,
                capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
            digest_ok = os.path.exists(os.path.join(pub, "digest-2026-11-27.html"))
            return r, digest_ok

    def test_new_sharecard_title(self):
        r, digest_ok = self._run_with_title("2026-11-27 · 1类新闻精选 · 2条")
        self.assertEqual(r.returncode, 0, f"脚本应成功退出:{r.stderr}")
        self.assertTrue(digest_ok, "应生成 digest-2026-11-27.html")

    def test_legacy_title(self):
        # 兼容旧格式(历史 index.html 或旧缓存重生成场景)
        r, digest_ok = self._run_with_title("每日新闻摘要 2026-11-27")
        self.assertEqual(r.returncode, 0, f"旧标题也应成功:{r.stderr}")
        self.assertTrue(digest_ok)


if __name__ == "__main__":
    unittest.main()
