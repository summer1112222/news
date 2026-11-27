#!/usr/bin/env python3
"""从 public/index.html 解析当天已抓取的数据,离线生成 PC 版宽屏页面 digest-{today}.html。

不抓取、不推送,纯本地转换,便于即时预览 PC 版布局。
解析后再过一遍 clean_items(去残留栏目/专题/英文资源页杂质,跨分类共享去重)
+ sort_items_for(日期降序;吃喝玩乐国内按地域优先级 上海>沪周边>全国),
内容比移动版 index.html 更精炼。
用法:python scripts/gen_pc_from_html.py
"""
import os
import re
import html
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_digest import build_html_pc, clean_items, sort_items_for  # noqa: E402

INDEX = os.path.join("public", "index.html")
doc = open(INDEX, encoding="utf-8").read()

# 从 <title> 提日期。两代格式都兼容:新分享卡片「{today} · {n}类新闻精选 · {N}条」
# 与旧「每日新闻摘要 {today}」(标题里日期是唯一 YYYY-MM-DD;本工具只解析移动版
# index.html,标题开头就是日期,无 PC 后缀歧义)
m = re.search(r"<title>(?:每日新闻摘要 )?(\d{4}-\d{2}-\d{2})", doc)
today = m.group(1) if m else ""
if not today:
    sys.exit("❌ 未在 public/index.html 找到日期")


def parse_items(block: str) -> list:
    items = []
    for am in re.finditer(r"<article class=\"news\">(.*?)</article>", block, re.S):
        art = am.group(1)
        um = re.search(r'<a class="news-title" href="([^"]*)"', art)
        url = um.group(1) if um else ""
        tm = re.search(r'<a class="news-title"[^>]*>(.*?)</a>', art)
        if not tm:
            tm = re.search(r'<span class="news-title">(.*?)</span>', art)
        title = html.unescape(tm.group(1)) if tm else ""
        sm = re.search(r'<p class="news-snippet">(.*?)</p>', art, re.S)
        snippet = html.unescape(sm.group(1)) if sm else ""
        items.append({"title": title, "url": url, "snippet": snippet, "source": ""})
    return items


cats = []
for pm in re.finditer(r'<section class="panel"[^>]*>(.*?)</section>', doc, re.S):
    p = pm.group(1)
    tm = re.search(r'<h2 class="panel-title">(.*?)</h2>', p)
    cat_name = html.unescape(tm.group(1)) if tm else "?"
    dom, fore = [], []
    # news-group 内部仅 article/p,无嵌套 div,.*? 非贪婪自然停在本组结束 </div>
    for gm in re.finditer(
        r'<div class="news-group (\w+)"[^>]*>(.*?)</div>',
        p, re.S,
    ):
        region, block = gm.group(1), gm.group(2)
        if region == "domestic":
            dom = parse_items(block)
        elif region == "foreign":
            fore = parse_items(block)
    cats.append({"category": cat_name, "domestic": dom, "foreign": fore})

# 再过一遍:去残留栏目/专题/英文资源页杂质,跨分类共享去重,按新鲜度降序
# (吃喝玩乐国内按「上海 > 沪周边 > 全国」地域优先级,与 build_digest 一致)
# 旧闻 cutoff 天数与 build_digest 一致(默认 7,可用 MAX_DAYS 放宽)
max_days = int(os.environ.get("MAX_DAYS", "7"))
seen = {"url": set(), "title": set()}
for cat in cats:
    cat["domestic"] = sort_items_for(cat["category"], clean_items(cat["domestic"], today, seen=seen, max_days=max_days), today)
    cat["foreign"] = sort_items_for(cat["category"], clean_items(cat["foreign"], today, seen=seen, max_days=max_days), today)

pc_html = build_html_pc(today, cats)
out = os.path.join("public", f"digest-{today}.html")
open(out, "w", encoding="utf-8").write(pc_html)
dt = sum(len(c["domestic"]) for c in cats)
ft = sum(len(c["foreign"]) for c in cats)
sys.stderr.write(f"[生成] {out}  分类{len(cats)} 国内{dt} 国外{ft}\n")
