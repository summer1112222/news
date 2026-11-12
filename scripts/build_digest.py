#!/usr/bin/env python3
"""每日新闻摘要 —— 抓取 9 类新闻,生成 tab 页 HTML,推送到微信(Server酱)。

流程:
  1. 调 anysearch 逐类抓取最新新闻(Markdown)
  2. 解析成结构化数据(标题/网址/摘要)
  3. 生成带 9 个分类 tab 的 HTML 页面(每条标题点开跳原文)
  4. 若设了 PAGES_BASE_URL,推一条瘦身版微信消息(链接 + 预告)到 Server酱

环境变量:
  SERVERCHAN_SENDKEY   推送时必填,Server酱 SendKey
  ANYSEARCH_API_KEY    可选,提高 anysearch 速率上限
  MAX_RESULTS          可选,每类抓取条数,默认 5
  PAGES_BASE_URL       可选,页面部署地址(如 https://user.github.io/news)
                      设了才推送微信;不设只本地生成 HTML 供预览

调度:GitHub Actions cron '2 22 * * *'(UTC 22:02 = 北京 06:02)
本地测试:python scripts/build_digest.py  → 生成 public/index.html
"""
import os
import sys
import io
import re
import html
import datetime
from urllib.parse import urlparse

import requests

# Windows 控制台默认 gbk,重配为 UTF-8,避免 emoji/中文输出崩溃
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 让脚本能在仓库根目录或 scripts/ 下运行时都找到 anysearch_search
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anysearch_search import search  # noqa: E402

# (分类标题, 搜索词) —— 搜索词会自动追加当天北京日期,提升时效性
# "头条新闻/发展/政策/项目"等词用于把网站首页、导航页排掉,抓真正的新闻标题
CATEGORIES = [
    ("📰 时政新闻", "时政 头条新闻"),
    ("📚 教育新闻", "教育 头条新闻"),
    ("🌐 全球科技", "全球 科技 新闻"),
    ("💰 财经新闻", "财经 股市 头条新闻"),
    ("🏥 全球医疗", "全球 医疗 健康 新闻"),
    ("🏙 江浙沪皖", "江苏 浙江 上海 安徽 时政"),
    ("🌆 上海发展", "上海 发展 政策 项目 新闻"),
    ("🏖 南通发展", "南通 发展 政策 项目 新闻"),
    ("👨‍👩‍👧 培养孩子", "培养孩子 家庭教育 育儿"),
]

# HTML 输出目录(GitHub Pages 产物根)
OUT_DIR = "public"

# tab 页样式(纯字符串,避免 f-string 与 CSS 花括号冲突)
_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#222;line-height:1.6}
header{background:#fff;border-bottom:1px solid #eaecef;padding:14px 16px}
header h1{margin:0;font-size:18px}
.meta{margin:6px 0 0;color:#888;font-size:13px}
.tabs{display:flex;overflow-x:auto;-webkit-overflow-scrolling:touch;background:#fff;border-bottom:1px solid #eaecef;position:sticky;top:0;z-index:10}
.tab{flex:0 0 auto;padding:11px 14px;border:0;background:transparent;font-size:14px;color:#555;cursor:pointer;white-space:nowrap;border-bottom:2px solid transparent}
.tab.active{color:#1a73e8;border-bottom-color:#1a73e8;font-weight:600}
main{max-width:720px;margin:0 auto;padding:12px 14px 40px}
.item{background:#fff;border-radius:8px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.05)}
.item h3{margin:0 0 6px;font-size:16px;line-height:1.45}
.item h3 a{color:#1a73e8;text-decoration:none}
.item h3 a:visited{color:#7c3aed}
.snippet{margin:0;color:#666;font-size:14px}
.empty{color:#999;font-size:14px;padding:24px 0;text-align:center}
"""


def beijing_date() -> str:
    """GitHub Actions 跑在 UTC,统一用北京日期作为摘要日期。"""
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


def domain_source(url: str) -> str:
    """从 URL 提取域名作为来源标签。"""
    if not url:
        return ""
    try:
        net = urlparse(url).netloc
        return net or url
    except Exception:
        return url


def parse_results(md_text: str) -> list:
    """把 anysearch 返回的 Markdown 解析成 [{title, url, source, snippet}]。

    anysearch 单条搜索输出格式:
        ### 1. 标题
        - **URL**: https://...
        - 摘要文本...
    """
    items = []
    blocks = re.split(r"### \d+\.\s+", md_text)
    for block in blocks[1:]:  # 第 0 块是 "## Search Results..." 前言,跳过
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0].strip()
        url = ""
        snippet_parts = []
        for line in lines[1:]:
            m = re.match(r"- \*\*URL\*\*:\s*(.+)", line)
            if m:
                url = m.group(1).strip()
            elif line.startswith("- "):
                snippet_parts.append(line[2:].strip())
            elif line.startswith("date:"):
                continue
        items.append({
            "title": title,
            "url": url,
            "source": domain_source(url),
            "snippet": " ".join(snippet_parts)[:220],
        })
    return items


def build_html(today: str, categories_data: list) -> str:
    """生成带 9 个分类 tab 的 HTML 页面:点分类切换,每条标题点开跳原文。"""
    total = sum(len(c["items"]) for c in categories_data)
    n = len(categories_data)

    tab_btns = []
    for i, cat in enumerate(categories_data):
        name = html.escape(cat["category"])
        cls = "tab active" if i == 0 else "tab"
        tab_btns.append(
            f'<button type="button" class="{cls}" onclick="showTab({i})">{name}</button>'
        )

    panels = []
    for i, cat in enumerate(categories_data):
        style = ' style="display:block"' if i == 0 else ""
        body_parts = []
        if not cat["items"]:
            body_parts.append('<p class="empty">本类今日抓取失败。</p>')
        for it in cat["items"]:
            title = html.escape(it["title"])
            if it["url"]:
                url = html.escape(it["url"], quote=True)
                title_line = f'<h3><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></h3>'
            else:
                title_line = f"<h3>{title}</h3>"
            if it["snippet"]:
                snippet = html.escape(it["snippet"])
                title_line += f'<p class="snippet">{snippet}</p>'
            body_parts.append(f'<div class="item">{title_line}</div>')
        panels.append(f'<div class="panel" id="panel-{i}"{style}>{"".join(body_parts)}</div>')

    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>每日新闻摘要 {html.escape(today)}</title>\n"
        "<style>\n" + _CSS + "\n</style>\n"
        "</head>\n<body>\n"
        '<header>\n'
        "<h1>📰 每日新闻摘要</h1>\n"
        f'<p class="meta">{html.escape(today)} · {n}类 · 共{total}条</p>\n'
        "</header>\n"
        '<nav class="tabs">' + "".join(tab_btns) + "</nav>\n"
        '<main>\n' + "".join(panels) + "\n</main>\n"
        '<script>\n'
        "function showTab(i){"
        "var p=document.querySelectorAll('.panel');"
        "for(var k=0;k<p.length;k++)p[k].style.display='none';"
        "document.getElementById('panel-'+i).style.display='block';"
        "var t=document.querySelectorAll('.tab');"
        "for(var k=0;k<t.length;k++)t[k].classList.remove('active');"
        "t[i].classList.add('active');"
        "window.scrollTo(0,0);"
        "}\n"
        "</script>\n"
        "</body>\n</html>"
    )


def build_wechat_msg(today: str, categories_data: list, page_url: str) -> tuple:
    """微信消息(A版定稿):按分类分组,标题做超链接、下跟一行摘要。

    无编号、无可见网址、无来源标签 —— 点标题直接跳原文。
    Server酱 渲染 markdown:[text](url) 在微信里显示为可点蓝字。
    """
    total = sum(len(c["items"]) for c in categories_data)
    n = len(categories_data)
    url = page_url.rstrip("/") + "/"

    lines = [
        "# 📰 每日新闻摘要",
        f"{today} · {n}类 · 共{total}条",
        "",
    ]
    for cat in categories_data:
        if not cat["items"]:
            continue
        lines.append(f"## {cat['category']}")
        for it in cat["items"]:
            if it["url"]:
                lines.append(f"[{it['title']}]({it['url']})")
            else:
                lines.append(it["title"])
            if it["snippet"]:
                lines.append(it["snippet"])
        lines.append("")

    lines += ["---", "", f"[📰 查看今日全部新闻]({url})"]
    md = "\n".join(lines)
    return f"每日新闻摘要 {today}", md


def fetch_all() -> tuple:
    """抓取所有分类,返回 (today, categories_data)。"""
    today = beijing_date()
    max_results = int(os.environ.get("MAX_RESULTS", "5"))

    categories_data = []
    for title, q in CATEGORIES:
        full_q = f"{q} {today}"
        sys.stderr.write(f"[抓取] {title} ...\n")
        try:
            body = search(full_q, max_results=max_results)
        except Exception as e:
            body = ""
            sys.stderr.write(f"[失败] {title}: {e}\n")
        items = parse_results(body) if body else []
        categories_data.append({"category": title, "items": items})

    return today, categories_data


def push_serverchan(title: str, md: str) -> dict:
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not key:
        raise SystemExit("❌ 未设置环境变量 SERVERCHAN_SENDKEY")
    url = f"https://sctapi.ftqq.com/{key}.send"
    sys.stderr.write(f"[推送] POST {url}\n")
    resp = requests.post(url, data={"title": title, "desp": md}, timeout=60)
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") not in (0, None):
        raise SystemExit(f"❌ Server酱返回错误: {j}")
    return j


def main():
    today, categories_data = fetch_all()

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_html(today, categories_data))
    sys.stderr.write(f"[生成] {out_path}\n")

    page_url = os.environ.get("PAGES_BASE_URL", "").strip()
    if page_url:
        title, md = build_wechat_msg(today, categories_data, page_url)
        push_serverchan(title, md)
        sys.stderr.write(f"[完成] 已推送到微信,页面 {page_url}\n")
    else:
        sys.stderr.write(f"[跳过推送] 未设 PAGES_BASE_URL,本地预览打开 {out_path}\n")


if __name__ == "__main__":
    main()
