#!/usr/bin/env python3
"""每日新闻摘要 —— 抓取 10 类新闻(每类分国内/国外,吃喝玩乐仅国内),生成 tab 页 HTML,推送到微信(Server酱)。

流程:
  1. 调 anysearch 逐类抓取最新新闻(每类国内 + 国外各 MAX_RESULTS 条)
  2. 解析成结构化数据(标题/网址/摘要)
  3. 生成带 9 个分类 tab 的 HTML 页面:顶部「国内/国外」切换,每个主题面板显示对应那一半
  4. 若设了 PAGES_BASE_URL,推一条微信消息(国内/国外分段)到 Server酱

环境变量:
  SERVERCHAN_SENDKEY   推送时必填,Server酱 SendKey
  ANYSEARCH_API_KEY    可选,提高 anysearch 速率上限
  MAX_RESULTS          可选,每类每个地域抓取条数,默认 10
  MAX_DAYS             可选,旧闻 cutoff 天数,默认 7
  TOP_N                可选,每类每地域最终展示条数,默认 10
  REFRESH              可选,设为 1 忽略当天缓存强制重新抓取
  PAGES_BASE_URL       可选,页面部署地址(如 https://user.github.io/news)
                      设了才推送微信;不设只本地生成 HTML 供预览

调度:GitHub Actions cron '2 17 * * *'(UTC 17:02 = 北京次日 01:02)
本地测试:python scripts/build_digest.py  → 生成 public/index.html
"""
import os
import sys
import io
import re
import html
import json
import time
import datetime
from urllib.parse import quote, urlparse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

# Windows 控制台默认 gbk,重配为 UTF-8,避免 emoji/中文输出崩溃
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 让脚本能在仓库根目录或 scripts/ 下运行时都找到 anysearch_search
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anysearch_search import search, search_many  # noqa: E402

# (分类标题, 国内搜索词, 国外搜索词) —— 搜索词会自动追加当天北京日期
# 每个主题分国内/国外各抓 MAX_RESULTS 条,页面顶部「国内/国外」切换决定看哪一半
# 用"最新"提时效,避免用"头条新闻"这类词(它反而把网站首页/导航页顶上来);
# 地域类主题(浙江/上海/南通)的"国外"用涉外/国际视角的搜索词
# 模块排序:大事在前(时政/财经/科技/教育/医疗),生活类次之(育儿),地域类收尾(浙江/上海/南通)。
CATEGORIES = [
    # 时政的「国外」用英文检索:中文搜国际时政常被栏目页/旧闻占据,
    # 英文词更容易命中当日外媒单篇报道(实测可拿到当天/前一天稿件)。
    # 其余模块国外仍用中文(英文泛词易混入活动页/报告页,反而不如中文出单篇)。
    # 国内词附该领域权威平台名引导:实测「上海 本地宝 最新 新闻」比「上海 最新 发展 新闻」
    # 更拉本地民生单条(凤凰/腾讯/新浪转载);纯 site: 限定会顶出栏目页故不用,靠源名+「最新 新闻」消歧引流。
    ("📰 时政新闻", "最新 时政 要闻 人民网 新华社", "world politics news"),
    ("💰 财经新闻", "最新 财经 股市 第一财经 财新", "国际 最新 财经 股市"),
    ("🌐 科技", "国内 最新 科技 新闻 36氪 虎嗅", "全球 最新 科技 新闻"),
    ("📚 教育新闻", "最新 教育 政策 新闻", "国际 教育 新闻"),  # 教育权威平台名(中国教育报/澎湃教育)偏栏目页,实测改用「政策」措辞出单条(教师法/教育局回应/北师大发布),无小时报混入
    ("🏥 医疗", "国内 最新 医疗 健康 新闻", "全球 最新 医疗 健康"),  # 兜底词不带平台名(健康报/丁香园):实测会搜出官网/百科词条等无日期非新闻页
    ("👨‍👩‍👧 育儿新闻", "最新 育儿 家庭教育 儿童健康 新闻 父母必读", "parenting child development family policy news"),  # 国内改全国育儿(gnews RSS 主源,anysearch 词仅兜底;旧的地域限定因 anysearch 索引滞后已弃);国外用英文搜真实国际育儿(中文搜国外只剩联合国专题页/政府公告)
    # 原「江浙沪皖」按用户决策收窄为「浙江」(上海另有专属模块,江苏/安徽不再重复覆盖)
    ("🏙 浙江", "浙江 最新 新闻 本地宝", "浙江 国际 涉外"),
    ("🌆 上海发展", "上海 本地宝 上海发布 最新 新闻", "上海 国际 涉外 新闻"),
    ("🏖 南通发展", "南通 本地宝 南通发布 最新 新闻", "南通 国际 涉外 新闻"),
    # 吃喝玩乐:全国范围但上海周边优先。国内 gnews feed 双路——沪向词出沪苏浙
    # 单条(美食/景区/周边游),文旅词接全国大热点(节假日出游/消费券/美食节);
    # anysearch 兜底词沿用「地域词+最新 新闻」引流惯例。q_for 留空 = 无国外侧
    # (吃喝玩乐是国内消费场景,方案定稿 2026-11-19):_expand_queries 跳过空词,
    # _region_raw/_filter_tiered 对空词空 feed 直接返回 [],微信国外段自动跳过。
    ("🍜 吃喝玩乐", "上海 周边 美食 出游 最新 新闻", ""),
]

# RSS 源(双侧):各分类全面 RSS 化(吃喝玩乐国外侧刻意留空无 feed),anysearch 降级为纯 fallback(某侧 feeds 为空或全部抓取失败才走)。
# - 中国新闻网官方 RSS:实时当天、标准 RSS 2.0、不反爬、本地+Actions 均可达(时政/教育/财经国内侧)。
#   注意:jk.xml(健康频道)已停更(2026-03-06 之后无新条目,2026-11-16 实测),医疗国内改走 gnews 中文 feed。
# - Google News RSS 搜索 feed:实时当天、带 <source> 标签(真实发布站名);美国 IP 可达 = Actions 可用,
#   本地被墙 fetch_rss 失败返回 [] 自动回退,不影响本地跑。
#   条目链接是 news.google.com/rss/articles/... 跳转链,国内无代理点击打不开(标题/摘要/日期/来源不受影响),
#   解码真实原文 URL 留作后续可选。
def _gnews(q: str, zh: bool = False) -> str:
    if zh:
        return "https://news.google.com/rss/search?q=" + quote(q) + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    return "https://news.google.com/rss/search?q=" + quote(q) + "&hl=en-US&gl=US&ceid=US:en"


# 每分类 {"dom": [...], "for": [...]};某侧列表为空 = 该侧仍走 anysearch(旧查询词)。
# 培养孩子国内侧按用户决策改全国育儿新闻(anysearch 时代限上海/南通是因为地域词搜不出,现在拿当天优先)。
RSS_FEEDS = {
    "📰 时政新闻": {"dom": ["https://www.chinanews.com.cn/rss/china.xml"], "for": [_gnews("world politics")]},
    "💰 财经新闻": {"dom": ["https://www.chinanews.com.cn/rss/finance.xml"], "for": [_gnews("finance stock markets")]},
    "🌐 科技": {"dom": [_gnews("科技", zh=True)], "for": [_gnews("technology news")]},
    "📚 教育新闻": {"dom": ["https://www.chinanews.com.cn/rss/edu.xml"], "for": [_gnews("education policy")]},
    "🏥 医疗": {"dom": [_gnews("医疗 OR 健康", zh=True)], "for": [_gnews("global health")]},
    "👨‍👩‍👧 育儿新闻": {"dom": [_gnews("育儿 OR 家庭教育", zh=True)], "for": [_gnews("parenting child development")]},
    "🏙 浙江": {"dom": [_gnews("浙江", zh=True)], "for": [_gnews("Zhejiang")]},
    "🌆 上海发展": {"dom": [_gnews("上海", zh=True)], "for": [_gnews("Shanghai")]},
    "🏖 南通发展": {"dom": [_gnews("南通", zh=True)], "for": [_gnews("Nantong")]},
    # 吃喝玩乐国内:双 feed 合并——沪向词(上海+美食/景区/周边游)出沪苏浙周边
    # 内容(实测:美食地图/景区半价/商圈带娃,但时效偏旧靠排序混入),全国词
    # 「美食节 OR 逛吃 OR 景区」出消费向热点(景区开园/亲子游/市集;实测纯
    # 「文旅」词会顶出招商/股权等产业新闻,勿改回)。国外侧按用户决策
    # (2026-11-19)留空:无该侧内容,UI 国外 tab 显示 0 条。
    "🍜 吃喝玩乐": {"dom": [_gnews("上海 美食 OR 景区 OR 周边游", zh=True),
                          _gnews("美食节 OR 逛吃 OR 景区", zh=True)], "for": []},
}

# HTML 输出目录(GitHub Pages 产物根)
OUT_DIR = "public"
# 原始搜索缓存目录:同一天重复渲染时免去重新抓取(设 REFRESH=1 强制重抓)
CACHE_DIR = ".cache"


def _env_num(name: str, default, cast, lo=None, hi=None):
    """读环境变量并做类型/范围校验,非法值回退默认,避免脏配置把整条流水线带崩。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = cast(raw)
    except (TypeError, ValueError):
        sys.stderr.write(f"[配置] {name}={raw!r} 非法,改用默认 {default}\n")
        return default
    if lo is not None and val < lo:
        val = lo
    if hi is not None and val > hi:
        val = hi
    return val


def _write_text_atomic(path: str, text: str) -> None:
    """先写临时文件再原子替换,避免中途失败留下半截 HTML。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _cache_file(today: str) -> str:
    return os.path.join(CACHE_DIR, f"raw-{today}.json")


def _load_raw_cache(today: str, queries: list) -> list:
    """读取当天原始抓取结果;查询词不一致或文件损坏则返回空列表。"""
    path = _cache_file(today)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        bodies = data.get("bodies")
        if data.get("queries") == queries and isinstance(bodies, list) \
                and len(bodies) == len(queries):
            return bodies
    except (OSError, ValueError):
        pass
    return []


def _save_raw_cache(today: str, queries: list, bodies: list) -> None:
    try:
        payload = {"queries": queries, "bodies": bodies, "saved_at": int(time.time())}
        _write_text_atomic(_cache_file(today), json.dumps(payload, ensure_ascii=False))
    except OSError as e:
        sys.stderr.write(f"[缓存] 写入失败(忽略): {e}\n")


def _final_cache_file(today: str) -> str:
    return os.path.join(CACHE_DIR, f"final-{today}.json")


def _load_final_cache(today: str):
    """读取当天「已完成过滤/排序/补搜」的成品数据,命中则免去全部网络请求。"""
    try:
        with open(_final_cache_file(today), encoding="utf-8") as f:
            data = json.load(f)
        cats = data.get("categories")
        if isinstance(cats, list) and cats and all(
            isinstance(c, dict) and {"category", "domestic", "foreign"} <= set(c)
            for c in cats
        ):
            return cats
    except (OSError, ValueError, TypeError):
        pass
    return None


def _save_final_cache(today: str, categories_data: list) -> None:
    try:
        payload = {"categories": categories_data, "saved_at": int(time.time())}
        _write_text_atomic(_final_cache_file(today), json.dumps(payload, ensure_ascii=False))
    except OSError as e:
        sys.stderr.write(f"[缓存] 成品写入失败(忽略): {e}\n")

# tab 页样式(纯字符串,避免 f-string 与 CSS 花括号冲突)
_CSS = """
:root{
  color-scheme:light dark;
  --bg:#F9F7F2;
  --ink:#1A1A1A;
  --ink-2:#6B6660;
  --rule:#E3DED2;
  --accent:#8B2A2A;
  --serif:"Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",serif;
  --sans:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#16140F;
    --ink:#EDE8DD;
    --ink-2:#9A948A;
    --rule:#2E2A22;
    --accent:#C75656;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.7;-webkit-text-size-adjust:100%;-webkit-tap-highlight-color:transparent}
.masthead{max-width:680px;margin:0 auto;padding:26px 16px 14px;border-bottom:3px double var(--accent)}
.masthead-title{margin:0;font-family:var(--serif);font-size:25px;font-weight:700;letter-spacing:1px;line-height:1.2}
.masthead-meta{margin:7px 0 0;font-family:var(--serif);font-size:12.5px;color:var(--ink-2)}
.regions{max-width:680px;margin:0 auto;display:flex;gap:0;border:1px solid var(--rule);border-radius:7px;overflow:hidden;margin-top:14px}
.region{flex:1;min-height:44px;border:0;background:transparent;font-family:var(--sans);font-size:15px;color:var(--ink-2);padding:10px 0;cursor:pointer;text-align:center;border-right:1px solid var(--rule);transition:background .15s,color .15s;letter-spacing:2px;user-select:none;-webkit-user-select:none;touch-action:manipulation}
.region:last-child{border-right:0}
.region.active{background:var(--accent);color:#fff;font-weight:600}
.region:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
.cats{position:sticky;top:0;z-index:10;background:var(--bg);max-width:680px;margin:0 auto;display:flex;overflow-x:auto;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain;border-bottom:1px solid var(--rule)}
.cat{flex:0 0 auto;min-height:44px;border:0;background:transparent;font-family:var(--sans);font-size:14px;color:var(--ink-2);padding:12px 13px;cursor:pointer;white-space:nowrap;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;user-select:none;-webkit-user-select:none;touch-action:manipulation}
.cat.active{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.cat:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
main{max-width:680px;margin:0 auto;padding:6px 16px calc(56px + env(safe-area-inset-bottom))}
[hidden]{display:none!important}
.panel-title{margin:20px 0 2px;font-family:var(--serif);font-size:19px;font-weight:700;padding-left:10px;border-left:3px solid var(--accent)}
.panel-count{margin:0 0 6px;padding-left:13px;font-family:var(--serif);font-size:12px;color:var(--ink-2)}
.news{padding:13px 0;border-bottom:1px solid var(--rule)}
.news:last-child{border-bottom:0;padding-bottom:2px}
.news-title{display:block;font-family:var(--serif);font-size:16.5px;font-weight:600;color:var(--ink);text-decoration:none;line-height:1.42;overflow-wrap:anywhere;word-break:break-word}
.news-title:hover{color:var(--accent);text-decoration:underline}
.news-title:active{color:var(--accent)}
.news-title:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:1px}
.news-meta{margin:4px 0 0;font-family:var(--serif);font-size:11.5px;color:var(--ink-2);letter-spacing:.4px}
.news-snippet{margin:6px 0 0;font-size:13.5px;color:var(--ink-2);line-height:1.62;overflow-wrap:anywhere;word-break:break-word}
.news-meta + .news-snippet{margin-top:3px}
.empty{margin:0;padding:18px 0;font-family:var(--serif);font-size:14px;color:var(--ink-2)}
footer{max-width:680px;margin:0 auto;padding:22px 16px calc(44px + env(safe-area-inset-bottom));font-family:var(--serif);font-size:12px;color:var(--ink-2);border-top:1px solid var(--rule)}
footer a{color:var(--accent);text-underline-offset:3px}
.skip-link{position:absolute;left:-9999px;top:8px;z-index:100;background:var(--accent);color:#fff;padding:8px 14px;border-radius:6px;font-family:var(--sans);font-size:14px;text-decoration:none}
.skip-link:focus{left:8px;outline:2px solid var(--ink);outline-offset:2px}
.to-top{position:fixed;right:18px;bottom:calc(18px + env(safe-area-inset-bottom));width:42px;height:42px;border-radius:50%;border:1px solid var(--rule);background:var(--bg);color:var(--ink-2);font-size:18px;line-height:1;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.14);z-index:20}
.to-top:hover,.to-top:focus-visible{color:var(--accent);border-color:var(--accent);outline:none}
@media (prefers-reduced-motion: no-preference){
  .panel:not([hidden]){animation:fade .22s ease both}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
}
@media print{
  .cats,.regions,.region-bar,.to-top,footer{display:none!important}
  .panel[hidden],.news-group[hidden]{display:block!important}
  .news{break-inside:avoid}
  a{color:inherit;text-decoration:none}
}
@media (max-width:360px){.masthead{padding-left:12px;padding-right:12px}main{padding-left:12px;padding-right:12px}footer{padding-left:12px;padding-right:12px}.masthead-title{font-size:23px}.cat{padding:12px 10px}.news-title{font-size:16px}.news-snippet{font-size:13px}}
@media (prefers-reduced-motion: reduce){.cat,.region{transition:none}}
"""

# PC 版样式:宽屏网格,每分类一个卡片,国内/国外同卡上下展示,无需 tab 切换
_CSS_PC = """
:root{
  color-scheme:light dark;
  --bg:#F9F7F2;--ink:#1A1A1A;--ink-2:#6B6660;--rule:#E3DED2;--accent:#8B2A2A;
  --serif:"Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",serif;
  --sans:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
@media (prefers-color-scheme: dark){
  :root{--bg:#16140F;--ink:#EDE8DD;--ink-2:#9A948A;--rule:#2E2A22;--accent:#C75656;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.7}
.wrap{max-width:860px;margin:0 auto;padding:28px 24px 48px}
.masthead{padding-bottom:14px;border-bottom:3px double var(--accent)}
.masthead-title{margin:0;font-family:var(--serif);font-size:32px;font-weight:700;letter-spacing:1px}
.masthead-meta{margin:8px 0 12px;font-family:var(--serif);font-size:13px;color:var(--ink-2)}
.region-bar{display:flex;margin-top:4px}
.region-btn{font-family:var(--serif);font-size:14px;font-weight:600;color:var(--ink-2);background:none;border:1px solid var(--rule);padding:5px 20px;cursor:pointer}
.region-btn:first-child{border-radius:6px 0 0 6px}
.region-btn:last-child{border-radius:0 6px 6px 0;margin-left:-1px}
.region-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.tabs{position:sticky;top:0;z-index:10;display:flex;gap:2px;overflow-x:auto;background:var(--bg);margin:18px -24px 0;padding:12px 24px 0;border-bottom:1px solid var(--rule)}
.tab-btn{flex:0 0 auto;font-family:var(--serif);font-size:15px;font-weight:600;color:var(--ink-2);background:none;border:none;border-bottom:2px solid transparent;padding:8px 14px;cursor:pointer;white-space:nowrap}
.tab-btn:hover{color:var(--ink)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
main{margin-top:18px}
.panel-head{display:flex;align-items:baseline;gap:12px;margin-bottom:10px}
.panel-title{margin:0;font-family:var(--serif);font-size:22px;font-weight:700;padding-left:10px;border-left:3px solid var(--accent)}
.panel-count{margin:0;font-family:var(--serif);font-size:12px;color:var(--ink-2)}
.news{padding:12px 0;border-bottom:1px solid var(--rule)}
.news:last-child{border-bottom:0;padding-bottom:2px}
.news-title{display:block;font-family:var(--serif);font-size:15.5px;font-weight:600;color:var(--ink);text-decoration:none;line-height:1.42;overflow-wrap:anywhere;word-break:break-word}
.news-title:hover{color:var(--accent);text-decoration:underline}
.news-meta{margin:4px 0 0;font-family:var(--serif);font-size:11.5px;color:var(--ink-2);letter-spacing:.4px}
.news-snippet{margin:5px 0 0;font-size:13px;color:var(--ink-2);line-height:1.6;overflow-wrap:anywhere;word-break:break-word}
.news-meta + .news-snippet{margin-top:3px}
.empty{margin:0;padding:18px 0;font-family:var(--serif);font-size:14px;color:var(--ink-2)}
footer{max-width:860px;margin:24px auto 0;padding:18px 24px;font-family:var(--serif);font-size:12px;color:var(--ink-2);border-top:1px solid var(--rule)}
footer a{color:var(--accent);text-underline-offset:3px}
.skip-link{position:absolute;left:-9999px;top:8px;z-index:100;background:var(--accent);color:#fff;padding:8px 14px;border-radius:6px;font-family:var(--sans);font-size:14px;text-decoration:none}
.skip-link:focus{left:8px;outline:2px solid var(--ink);outline-offset:2px}
[hidden]{display:none!important}
.to-top{position:fixed;right:24px;bottom:24px;width:42px;height:42px;border-radius:50%;border:1px solid var(--rule);background:var(--bg);color:var(--ink-2);font-size:18px;line-height:1;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.14);z-index:20}
.to-top:hover,.to-top:focus-visible{color:var(--accent);border-color:var(--accent);outline:none}
@media (prefers-reduced-motion: no-preference){
  .panel:not([hidden]){animation:fade .22s ease both}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
}
@media print{
  .tabs,.region-bar,.to-top,footer{display:none!important}
  .panel[hidden],.news-group[hidden]{display:block!important}
  .news{break-inside:avoid}
  a{color:inherit;text-decoration:none}
}
"""


def beijing_date() -> str:
    """GitHub Actions 跑在 UTC,统一用北京日期作为摘要日期。"""
    return _beijing_now().strftime("%Y-%m-%d")


def _beijing_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def domain_source(url: str) -> str:
    """从 URL 提取域名作为来源标签。"""
    if not url:
        return ""
    try:
        net = urlparse(url).netloc
        if net.startswith("www."):
            net = net[4:]
        return net or url
    except Exception:
        return url


# 摘要里常见的站点导航/元信息噪音;在截断前先清掉,避免一行摘要全是“首页 登录 客户端”
_SNIPPET_NOISE_RE = re.compile(
    r"正在阅读[:：]?|更新时间[:：]?|Updated|发表于|发布于|来源[:：]|作者[:：]|"
    r"编辑[:：]|责任编辑[:：]|阅读原文|打开\s*app[^ ]*|举报|独家原创|"
    r"相关推荐|为您推荐|热点新闻|财经信源|科技精选|微信|微博|QQ空间|朋友圈|"
    r"新浪新闻|凤凰网|腾讯新闻|搜狐|网易|界面新闻|澎湃新闻|客户端|扫码下载|"
    r"网站地图|首页|导航|登录|注册|更多",
    re.I,
)
# 时间戳/“date: 7 hours ago”之类元信息
_SNIPPET_TIME_RE = re.compile(
    r"(?<!\d)20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?!\d)|"
    r"(?<!\d)\d{1,2}月\d{1,2}日|"
    r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)|"
    r"date\s*:\s*[^,，。;；]{0,24}?ago|"
    r"\b\d+\s*(?:hours?|days?|minutes?)\s+ago\b",
    re.I,
)
_SENTENCE_END_RE = re.compile(r"[。！？!?；;]")


def _clean_snippet(text: str, title: str = "", limit: int = 150) -> str:
    """把搜索返回的整页正文压成一句干净的导语。

    搜索结果的 snippet 往往是页面正文全文,开头混着标题重复、站点导航、时间戳。
    这里依次:去 Markdown 标记/URL → 去重复标题 → 去导航与时间戳噪音 →
    在句末标点处截断(至少保留 40 字),得到适合列表展示的一行摘要。
    """
    s = html.unescape(text or "")
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"^[\s#>*\-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    # 开头常见「标题_站点 # 标题 正文」:优先取第一个标题标记之后的正文
    if "#" in s[:200]:
        s = s[s.rfind("#", 0, 200) + 1:].strip()

    if title:
        t = title.strip()
        if t and s.lower().startswith(t.lower()):
            s = s[len(t):].lstrip(" _-|｜—")
    # 标题后常跟一长串 | 分隔的标签(上海市|联合国人居署|…_),标签里没有句末标点,整段丢掉
    m = re.search(r"[_#]", s[:180])
    if m and not _SENTENCE_END_RE.search(s[:m.start()]):
        s = s[m.end():].lstrip(" _-|｜—#")

    s = re.sub(r"(?<!\d)\d{7,}(?!\d)", " ", s)  # 长数字 id
    s = re.sub(r"/?enpproperty\s*--?>?", " ", s, flags=re.I)
    s = _SNIPPET_TIME_RE.sub(" ", s)
    s = _SNIPPET_NOISE_RE.sub(" ", s)
    s = re.sub(r"[\]\[【】<>《》|｜]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" _-|｜—,，、;；:：#")

    if len(s) <= limit:
        return s
    window = s[:limit]
    last = None
    for m in _SENTENCE_END_RE.finditer(window):
        last = m
    if last and last.end() >= 40:
        return window[:last.end()]
    return window.rstrip("，,、；;：: ") + "…"


def parse_results(md_text: str) -> list:
    """把 anysearch 返回的 Markdown 解析成 [{title, url, source, snippet}]。

    anysearch 单条搜索输出格式:
        ### 1. 标题
        - **URL**: https://...
        - 正文/摘要(整页内容,需要清洗)
    """
    items = []
    blocks = re.split(r"### \d+\.\s+", md_text)
    for block in blocks[1:]:  # 第 0 块是 "## Search Results..." 前言,跳过
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = re.sub(r"^#+\s*", "", lines[0]).strip()
        url = ""
        snippet_parts = []
        for line in lines[1:]:
            m = re.match(r"- \*\*URL\*\*:\s*(.+)", line)
            if m:
                url = m.group(1).strip()
            elif line.startswith("- "):
                snippet_parts.append(line[2:].strip())
        items.append({
            "title": title,
            "url": url,
            "source": domain_source(url),
            # 存原始摘要(保留日期):clean_items 的旧闻过滤、pub_date、sort_items_recent
            # 都从 url+title+snippet 提日期(凤凰大风号等站发布日期常只在摘要里);
            # 显示时再由 _clean_snippet 压成干净导语,避免过滤与显示共用一个被剥光的字段
            "snippet": " ".join(snippet_parts)[:220],
        })
    return items


def _parse_rss_date(pub: str) -> str:
    """RFC822 'Sun, 15 Nov 2026 08:19:22 +0800' → 'YYYY-MM-DD',失败返回 ''。

    RSS pubDate 是 RFC822 格式,_parse_all_dates 不支持;RSS item 单独存 date 字段,
    pub_date/clean_items 优先用它判日期,无需从 url/title/snippet 提。
    """
    try:
        dt = parsedate_to_datetime(pub)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, AttributeError):
        return ""


def parse_rss(xml_text: str) -> list:
    """解析 RSS 2.0 XML → [{title, url, source, snippet, date}]。

    item 结构与 parse_results 一致(复用 clean_items/sort/pub_date),额外带 date 字段
    (pubDate 解析的 YYYY-MM-DD)。description 先去 HTML 标签再 _clean_snippet 压成导语;
    title 不在此裁剪(显示时由 _render_news_item 统一 _clip)。
    """
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        desc = it.findtext("description") or ""
        desc = re.sub(r"<[^>]+>", "", desc)          # 去 HTML 标签
        pub = it.findtext("pubDate") or ""
        # Google News RSS 每个 item 带 <source url="...">发布站名</source>,
        # 用站名作 source(比 domain_source(link) 得到的 "Google News" 有用);
        # gnews 的 title 尾部习惯带 " - {站名}",恰好等于站名时剥掉。
        src_name = (it.findtext("source") or "").strip()
        if src_name:
            title = re.sub(r"\s+-\s+" + re.escape(src_name) + r"$", "", title)
        items.append({
            "title": title,
            "url": link,
            "source": src_name or domain_source(link),
            "snippet": _clean_snippet(desc, title=title, limit=150),
            "date": _parse_rss_date(pub),
        })
    return items


def fetch_rss(url: str, timeout: int = 20) -> list:
    """抓取一个 RSS feed → item 列表(失败返回 [],调用方回退 anysearch)。"""
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "news-digest/1.0"})
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return parse_rss(resp.text)
    except Exception as e:
        sys.stderr.write(f"[RSS失败] {url}: {type(e).__name__}\n")
        return []


# 视频站 / 社交平台 / 聚合首页 —— 抓到的几乎不是单条新闻,整域排除
_JUNK_HOSTS = {
    "youtube.com", "youtu.be",
    "facebook.com", "fb.com", "fb.watch",
    "bilibili.com", "b23.tv",
    "douyin.com", "tiktok.com",
    "weibo.com",
    "x.com", "twitter.com",
    "instagram.com",
    # 百科/导航站:永远是词条/目录页,不是新闻(医疗兜底实测渗入「丁香园」词条、「医疗保健」栏目)
    "baike.baidu.com", "265.com",
}

# 英文栏目名标题(用于无日期条目收紧):如 "Politics & Diplomacy"、
# "World Politics, Diplomacy and Trade"(cnbc/atlanticcouncil 栏目页)。
# 判据见 _is_en_column_name:真实英文新闻标题多为普通大小写、含数字或动词句式;
# 为避免误杀 title-case 真标题,还要求命中 News/&/,/: 之一作为栏目标记。
_EN_COLUMN_STOPWORDS = {"and", "of", "the", "for", "in", "on", "to"}

# 无日期英文条目的资源/导览页开头词(仅 undated 时生效,不误伤带日期真新闻)
_EN_RESOURCE_PREFIXES = ("Knowledge of", "Understanding", "Introduction", "Overview",
                         "Guide to", "Resources", "About ", "Basics of", "Tips",
                         "Facts about", "What is", "How to")


def _is_en_column_name(title: str) -> bool:
    """英文栏目名判断:全首字母大写名词短语、无数字、词数 2-9、且含栏目标记。

    连接小词(and/of/the/for/in/on/to)与 &、逗号、冒号视为分隔符。
    要求含 News/&/,/: 之一:纯 title-case 短语(如 "Trump Signs Executive Order")
    可能是真新闻标题,无标记时不敢只凭大小写判死。
    """
    t = title.strip()
    if re.search(r"\d", t):
        return False
    words = [w.strip(",;:·") for w in t.split()]
    if not (2 <= len(words) <= 12):
        return False
    has_marker = any(m in t for m in ("News", "&", ",", ":"))
    if not has_marker:
        return False
    for w in words:
        if not w:
            return False
        if w.lower() in _EN_COLUMN_STOPWORDS or w == "&":
            continue
        if not w[0].isupper() or not all(ch.isalpha() for ch in w[1:]):
            return False
    return True

# 标题里出现这些词的多半是栏目首页/导航页/订阅页/常年专题页,不是单条新闻
_NAV_TITLE_HINTS = ("首页", "导航", "主页", "官网", "网站地图", "全球資訊網",
                    "常人故事", "Subscription", "PressReader", "Group Co., Ltd.",
                    # 国际/世界 XX 日:常年专题页或营销活动页,非当日新闻
                    "国际家庭日", "國際家庭日", "国际儿童日", "世界儿童日",
                    "国际教育日", "世界儿童节", "国际母语日",
                    # 英文栏目/专题/资源/报告页:in the News / Guide / Roundup / Admissions 等
                    " in the News", "Resource Roundup", "Parenting Guide", "Focus on Kids",
                    "International Admissions", "International Day of Education",
                    "State of Kids", "State of Families", "Cornerstone", "International Import Expo",
                    # 中文栏目页残留:概況/国际会议列表/外事日程/数据发布/经贸新闻/图片频道
                    "概況", "国际会议", "外事日程", "数据发布", "经贸新闻", "图片频道", "公寓式酒店",
                    "新闻直播", "招生录取", "Newsroom", "时政快报",
                    # 聚合索引/日报/资讯周报/小时报/封面报道等栏目页
                    "所有关于", "产业日报", "热点速递", "资讯周报", "要闻速览",
                    "小时报", "封面报道", "企业专辑", "享誉",
                    # 英文聚合页/周刊「版次」页
                    "News Headlines", " Edition")

# 标题(去空格后)完全等于这些通用词的,是栏目名不是单条新闻
_GENERIC_TITLES = {
    "国际新闻", "国内新闻", "时政要闻", "时政新闻", "财经新闻", "财政新闻",
    "教育新闻", "科技新闻", "医疗新闻", "健康新闻", "国际教育日",
    "热点新闻", "重要新闻", "滚动新闻", "最新新闻", "今日新闻",
    "早报", "晚报", "快讯", "播报", "热点", "要闻", "头条", "在线报导", "观察",
    # 裸地名/两三字栏目页残留(“上海”“南通”“经贸新闻”等)
    "上海", "南通", "经贸新闻", "数据发布", "外事日程", "国际会议", "Findings",
    # 栏目页/频道名:“新闻发布”(商务部发布栏)、“News”(英文栏目页)
    "新闻发布", "News",
    "国际要闻", "国内要闻", "最新封面报道", "国际财经与时政要闻",
}

# 栏目_站点/机构 页:前半是通用栏目或地名,后半是机构/政府/媒体站点
#   外事日程_中华人民共和国外交部 / 出国展览_中国国际贸易促进委员会南京市分会 / 南通_中国江苏网
_ORG_TAIL_RE = re.compile(r"(委员会|政府|大学|中心|分会|促进会|外交部|中国江苏网|日报|报社|信息网|门户)")
# 报纸版面目录:光明日报_第08版:国际新闻 / 第08版
_NEWSPAPER_PAGE_RE = re.compile(r"第\d+版|版[：:]\s*[国内国际]*新闻")

# 以这些前缀开头的标题多半是视频/直播多媒体,不是文字新闻
_MEDIA_PREFIXES = ("视频丨", "微视频", "视频：", "【视频】", "欢迎观看直播", "直播丨", "直播：")

# 家庭教育服务机构的营销自夸文(师资/团队/服务/口碑),不是新闻;
# 这些词在真正育儿新闻标题里几乎不出现,单独命中即可安全过滤
# 英文结果里的聚合/栏目/活动/报告页:latest news、News Headlines、Department of、
# Conference/Summit/Expo/Outlook、含“|”的频道页。只对英文标题生效,避免误伤中文。
_EN_NAV_RE = re.compile(
    r"\b(latest|breaking)\b.*\bnews\b|\bnews\s*headlines?\b|"
    r"\b(department|ministry)\s+of\b|\bnewsroom\b|\bsubscribe\b|\|",
    re.I,
)
_EN_EVENT_RE = re.compile(
    r"\b(conference|summit|expo|forum|outlook|webinar|symposium|"
    r"week\s+20\d{2}|20\d{2}\s*[|–-])\b",
    re.I,
)


def _is_english_title(title: str) -> bool:
    letters = len(re.findall(r"[A-Za-z]", title))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", title))
    return letters >= 6 and letters > cjk


_PROMO_TITLE_HINTS = ("师资团队", "持证上岗", "复荐率", "硬核专业", "高口碑", "铸就",
                      "辅导效果", "家长反馈", "全解析", "避坑", "合规厂家",
                      "成功代理")  # 律所自夸文(德恒成功代理…典型案例),非新闻

# 攻略/清单页(点评站页脚、Top N 目的地盘点、"这N个地方"自媒体清单、《…指南》
# 图书/攻略页):吃喝玩乐类 anysearch 补搜实测(2026-11-19)混入的非新闻页。
# 注意「10大推介」这类媒体盘点措辞与真新闻难分,不拦;只拦确定性特征。
_GUIDE_LISTICLE_RE = re.compile(
    r"真实点评|完整指南|《[^》]{2,12}(指南|攻略)》|这\s*\d+\s*个(地方|去处|打卡)"
    r"|\d+\s*大(美食|景点|游览|餐厅)"
)

# 繁体专用字符(简体文本不使用的码位):标题里 ≥2 个 = 港台媒体攻略/盘点页
# (如「樂游上海•新晉美食打卡地,竟是這些潮流街區」),非简体新闻源。取 2 个
# 阈值防个别字出现在机构名/人名里误杀。
_TRAD_ONLY_CHARS = set("樂遊這裡們歲飲廠學區醫兒後點擊臺閱讀")

# 通用栏目词后紧跟分隔符(_ - — · | [)的,是栏目页不是新闻(重要新闻_xxx / 时政要闻—xxx)
_GENERIC_PREFIX_RE = re.compile(
    r"^(重要新闻|热点新闻|滚动新闻|时政要闻|时政新闻|国际新闻|国内新闻|"
    r"财经新闻|财政新闻|教育新闻|科技新闻|医疗新闻|健康新闻|头条|要闻|早报|晚报)[_\-—–·|\[［]"
)

# 自媒体/栏目名作前缀(时政要闻 赵孝…、财报揭秘室｜…、科技精选 …):剥掉后才是真正的标题
_COLUMN_PREFIX_RE = re.compile(
    r"^(时政要闻|热点新闻|今日头条|科技精选|财经信源|财报揭秘室|城市速递|"
    r"环球拾见|凤凰国际财闻汇|凤凰AI研究院|南方观察|澎湃新闻)\s*[|｜:：\-—]?\s+"
)

# 标题尾部的发布日期(常见于大风号:"…会议 2026-11-03"),展示前剥掉
_TRAILING_DATE_RE = re.compile(
    r"[（(]?20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?[)）]?\s*$"
)


def _dedupe_title_key(title: str) -> str:
    """生成更稳的去重键:去空白/尾部日期/尾部年份/尾部标点。

    同一篇稿子常以“…博览会开幕”与“…博览会开幕 2026”两种形态重复出现,
    仅去空白无法合并;这里再去掉尾部年份与标点,降低重复率。
    """
    s = re.sub(r"\s+", "", title)
    s = re.sub(r"[（(]?20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?[)）]?$", "", s)
    s = re.sub(r"(?<=[^\d])20\d{2}$", "", s)  # 尾部年份(要求前面不是数字)
    return s.strip("，,。.！!？?；;：:-—_|｜、·")


# 近重复标题阈值:字符 bigram Jaccard ≥ 此值视为同一篇(措辞微调的同稿多版本)。
# 实测(2026-11-19 真实数据):精确键挡不住「中国雪乡景区正式开园迎客/风景区开园
# 迎客/11月19日正式开园/开园 新增冰雪DIY体验」这类簇;当天全部 ≥0.40 的相似对
# 均为真同稿(教育 0.82 / APEC 展望 0.45 / 冰球联赛开赛vs落幕 0.49),零误杀,
# 故取 0.40(留出安全边际,勿降到 0.3x——会把仅同题材的不同新闻并掉)。
_TITLE_SIM_THRESHOLD = 0.40


def _title_bigrams(title: str) -> frozenset:
    """标题归一化后的相邻双字集合,用于近重复相似度。"""
    s = _dedupe_title_key(title)
    return frozenset({s[i:i + 2] for i in range(len(s) - 1)}) if len(s) > 1 else frozenset()


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter) if inter else 0.0


def _host_base(url: str) -> str:
    """从 URL 取净域名(去 www. 前缀),失败返回空串。"""
    try:
        net = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""
    if net.startswith("www."):
        net = net[4:]
    return net


def _is_root_path(url: str) -> bool:
    """URL 是否指向站点根(首页):path 为空、/、或 index.html/home 这类。"""
    try:
        p = urlparse(url).path
    except Exception:
        return False
    if p in ("", "/"):
        return True
    return p.strip("/").lower() in ("index.html", "index.htm", "index.php", "home")


def _is_section_page(url: str) -> bool:
    """URL 是否像栏目/频道首页而非单篇文章。

    站点根(由 _is_root_path 认);或 path 末段是 index/default/home 等首页文件;
    或 path 仅 1-2 段且末段是纯字母短词(如 /news /xwfb /en ——真实文章页末段一般含
    日期或长数字 id);或话题聚合页 /topics/。拦掉「新闻发布」/en/news、「駐洛杉磯…」
    /uslax/index.html、「国际-BBC」/zhongwen/topics/… 这类栏目/机构首页。
    """
    if _is_root_path(url):
        return True
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    if "/topics/" in path or "/topic/" in path:
        return True
    segs = [s for s in path.split("/") if s]
    if not segs:
        return True
    last = segs[-1]
    if last in ("index.html", "index.htm", "index.php", "index.shtml",
                "default.html", "default.htm", "home", "main.html"):
        return True
    # 仅单段纯字母短词判为栏目入口(如 /news /xwfb /en);2 段纯字母(如 /opinion/column)
    # 既可能是栏目也可能是文章分页,不再靠此规则误杀,改由标题 NAV/通用栏目规则兜底。
    if len(segs) == 1 and re.fullmatch(r"[a-z]{1,12}", last):
        return True
    return False


def _title_substance(title: str) -> str:
    """去掉标题里的日期/时间/星期/括注/标点/空白/数字,返回实质文字。

    实质过短(<3 字)说明是裸日期(「2026年11月2日」)、纯数字(「0」)、或纯栏目符号,
    不是有效新闻标题。剥完后用于判空过滤,不影响显示用的原标题。
    """
    s = title
    s = re.sub(r"20\d{2}年\d{1,2}月\d{1,2}日", "", s)
    s = re.sub(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", "", s)
    s = re.sub(r"(?<!\d)20\d{6}(?!\d)", "", s)  # 连写式 20261113
    s = re.sub(r"\d{1,2}月\d{1,2}日", "", s)
    s = re.sub(r"\d{1,2}:\d{2}", "", s)
    s = re.sub(r"[周星期]+[一二三四五六日天]", "", s)
    s = re.sub(r"[\[【(（][^】\])）]*[】\])）]", "", s)
    s = re.sub(r"[\s\d\W_]+", "", s, flags=re.UNICODE)
    return s


def _parse_all_dates(s: str, today_year: str = "") -> list:
    """从文本里提取所有 YYYY-MM-DD 日期,返回升序列表。

    支持分隔式 / 中文式 / 连写式,以及无年份的 M月D日、M-D HH:MM(补 today_year)。
    旧闻判断用:文本里可能同时含发布日期和未来事件日(施行日/活动日),
    由调用方取 <= 今天的最大日期作为发布日期,避免把标题里的未来日期误判为发布日。
    """
    found = set()

    def _add(y, mo, d):
        try:
            if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                found.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
        except ValueError:
            pass

    # 分隔式 2026-11-13 / 2026.11.13 / 2026/10/29
    for m in re.finditer(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", s):
        _add(m.group(1), m.group(2), m.group(3))
    # 中文式 2026年11月3日
    for m in re.finditer(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", s):
        _add(m.group(1), m.group(2), m.group(3))
    # 连写式 20261113(URL 路径常见),要求两侧非数字避免误匹配长数字串
    for m in re.finditer(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", s):
        _add(m.group(1), m.group(2), m.group(3))
    # 无年份补当年:M月D日,或 M-D/M-D HH:MM 发布时间戳(凤凰大风号等);
    # M月D日 要求前面既非“年”也非数字:避免把“2027年1月1日”里的“1月1日”
    # 误补成当年,或把“2026年11月14日”里的“1月14日”拆出来误补
    if today_year:
        for m in re.finditer(r"(?<!年)(?<!\d)(\d{1,2})月(\d{1,2})日", s):
            _add(today_year, m.group(1), m.group(2))
        for m in re.finditer(r"(?<!\d)(\d{1,2})[-/](\d{1,2})\s+\d{1,2}:\d{2}", s):
            _add(today_year, m.group(1), m.group(2))
    return sorted(found)


def pub_date(it: dict, today: str) -> str:
    """条目可考的最新发布日期(<= 今天),没有则返回空串。

    优先用 item 自带的 date 字段(RSS item 由 _parse_rss_date 填,权威当天日期);
    无 date(anysearch item)才从 url+title+snippet 提(_parse_all_dates 不认 RFC822)。
    """
    d = it.get("date", "")
    if d:
        return d if (not today or d <= today) else ""
    blob = " ".join((it.get("url", ""), it.get("title", ""), it.get("snippet", "")))
    dates = _parse_all_dates(blob, today[:4] if today else "")
    past = [d for d in dates if today and d <= today]
    return max(past) if past else ""


def clean_items(items: list, today: str, seen: dict = None, max_days: int = 7) -> list:
    """后处理过滤:去首页/导航页/视频站/裸URL标题/旧闻,按 URL+标题去重。

    seen 传入则跨分类共享去重(同一新闻不在多个分类重复出现);
    调用方在分类内传同一 seen 即可做到该分类国内+国外不重复。
    无任何日期线索的条目视为时效不明:RSS 条目均带 date 字段不受影响;
    时效不明且再命中栏目页特征的(anysearch 兜底渗入的百科/栏目/拼接页)会被收紧丢弃。
    """
    if seen is None:
        seen = {"url": set(), "title": set()}
    cutoff = None
    today_year = ""
    if today:
        try:
            t = datetime.datetime.strptime(today, "%Y-%m-%d").date()
            cutoff = t - datetime.timedelta(days=max_days)
            today_year = today[:4]
        except Exception:
            cutoff = None

    out = []
    for it in items:
        url = it.get("url", "")
        title = it.get("title", "").strip()
        # 注意:不在此剥标题开头的发布时间戳前缀——它正是旧闻过滤与排序的日期来源之一
        # (URL 无日期时,标题前缀「2026-11-02 14:30」可能是唯一线索)。前缀剥离只在
        # 渲染显示时做(_render_news_item / build_wechat_msg),过滤判断用原始标题。
        # _title_substance 自身会去日期,故裸日期标题「2026年11月2日」仍会被下面的实质<3过滤拦掉。

        # 裸 URL 当标题:标题本身就是 http... 或裸域名
        if not title or re.match(r"^https?://", title, re.I) \
                or re.match(r"^[\w.-]+\.[a-z]{2,}/?$", title, re.I):
            continue
        # 标题实质内容过短:裸日期(2026年11月2日)/纯数字(0)/纯栏目符号,非有效新闻
        if len(_title_substance(title)) < 3:
            continue
        # 标题含导航/首页字样
        if any(h in title for h in _NAV_TITLE_HINTS):
            continue
        # 标题就是通用栏目名(国际新闻/早报/热点新闻 等)
        if re.sub(r"\s+", "", title) in _GENERIC_TITLES:
            continue
        # 「国际要闻| 大纪元」之类:左半边是栏目名,右半边是站点,整行是栏目页
        mb = re.match(r"^(.{1,10})\s*[|｜]\s*\S+$", title)
        if mb and re.sub(r"\s+", "", mb.group(1)) in _GENERIC_TITLES:
            continue
        # 连载片段标题(之一：/之二：…),脱离上下文无意义
        if re.match(r"^之[一二三四五六七八九十]+[：:]", title):
            continue
        # [午夜新闻]时政要闻 这类:括号内是栏目名,去掉括号后剩的也是栏目词
        mb = re.match(r"^[\[【][^】\]]*[】\]]\s*(.*)$", title)
        if mb and (not mb.group(1) or re.sub(r"\s+", "", mb.group(1)) in _GENERIC_TITLES):
            continue
        # 通用栏目词 + 分隔符的栏目页(重要新闻_xxx / 时政要闻—xxx)
        if _GENERIC_PREFIX_RE.match(title):
            continue
        # 栏目_机构/站点 页:前半通用栏目/地名,后半机构站点(南通_中国江苏网 等)
        if "_" in title:
            munder = re.match(r"^([^_]+)_(.+)$", title)
            if munder and (
                re.sub(r"\s+", "", munder.group(1)) in _GENERIC_TITLES
                or _ORG_TAIL_RE.search(munder.group(2))
            ):
                continue
        # 报纸版面目录(光明日报_第08版:国际新闻)
        if _NEWSPAPER_PAGE_RE.search(title):
            continue
        # 视频/直播前缀
        if any(title.startswith(p) for p in _MEDIA_PREFIXES):
            continue
        # 机构营销软文:家庭教育服务机构的师资/服务自夸文,非新闻
        if any(h in title for h in _PROMO_TITLE_HINTS):
            continue
        # 攻略/清单页:点评站页脚、Top N 盘点、《…指南》
        if _GUIDE_LISTICLE_RE.search(title):
            continue
        # 繁体标题:港台媒体攻略/盘点页
        if sum(1 for ch in title if ch in _TRAD_ONLY_CHARS) >= 2:
            continue
        # 英文聚合/栏目/活动/报告页(latest news、Department of、Conference、Outlook、含“|”)
        if _is_english_title(title) and (_EN_NAV_RE.search(title) or _EN_EVENT_RE.search(title)):
            continue
        # 视频站 / 社交 / 聚合首页(含子域)
        host = _host_base(url)
        if host and any(host == h or host.endswith("." + h) for h in _JUNK_HOSTS):
            continue
        # Google News:只放行 /rss/articles/ 文章跳转链(gnews RSS 条目);
        # 其余 news.google.com 路径(首页/订阅页等)仍按聚合页拦
        if host == "news.google.com" and not urlparse(url).path.startswith("/rss/articles/"):
            continue
        # 栏目/频道首页(站点根、index.html、短字母路径、/topics/ 话题聚合页)
        if _is_section_page(url):
            continue
        # 旧闻:RSS item 优先用自带 date 字段(权威,无需从文本提);无 date 的 anysearch
        # item 从 URL+标题+摘要提(凤凰大风号等发布日期常只在摘要里,只看 URL+标题会漏)
        snippet = it.get("snippet", "")
        undated = False
        if cutoff:
            d = it.get("date", "")
            if d and d <= today:
                # 有权威 date 且未过期:直接用它判旧闻,跳过 _parse_all_dates
                try:
                    if datetime.datetime.strptime(d, "%Y-%m-%d").date() < cutoff:
                        continue
                except ValueError:
                    pass
                # date 在 cutoff 内:非旧闻,落到去重段
            else:
                # 无 date:发布日期取文本里 <= 今天的最大日期(标题可能含未来事件日
                # 如 2027年1月1日起施行,取未来日期会误判为非旧闻而漏过过滤)
                dates = _parse_all_dates(url + " " + title + " " + snippet, today_year)
                past = [d for d in dates if d <= today]
                if past:
                    try:
                        if datetime.datetime.strptime(max(past), "%Y-%m-%d").date() < cutoff:
                            continue
                    except Exception:
                        pass
                else:
                    undated = True
            if undated:
                # 无日期条目收紧:RSS 条目均带 date 字段不受影响,此规则主要拦
                # anysearch 兜底里渗入的栏目页/拼接页(2026-11-16 实测 12 条)。
                # 时效不明 + 非新闻特征 = 丢弃:
                # - 英文栏目名:全首字母大写名词短语(如 "Politics & Diplomacy")
                # - 中文标签拼接串:多个 | 分隔的「栏目|栏目|来源-媒体」页面标题
                if _is_english_title(title):
                    if _is_en_column_name(title):
                        continue
                    # 资源/导览页开头词:文档/课程/指南首页,非新闻(undated 兜底里常见)
                    if title.startswith(_EN_RESOURCE_PREFIXES):
                        continue
                else:
                    # 中文栏目频道/专题页:「高职高专(职业院校)招聘信息频道」
                    if "频道" in title or title.endswith("专题"):
                        continue
                # 中文标签拼接串:多个 | 分隔的「栏目|栏目|来源-媒体」页面标题
                if title.count("|") + title.count("｜") >= 2:
                    continue
        # 去重(分类内 + 跨分类共享)
        key_url = url.rstrip("/")
        key_title = _dedupe_title_key(title)
        if key_url and key_url in seen["url"]:
            continue
        if key_title and key_title in seen["title"]:
            continue
        # 近重复:bigram Jaccard ≥ 阈值的同稿多版本(措辞微调)。先记录再判断——
        # 被模糊去重丢掉的标题也进 bigrams 列表,后续同簇变体才能都对上
        # (如 A/B/C/D 四连:B 与 A 相似被丢,D 与 B 相似但与 A 不够,仍要能丢)。
        # 注意 sims[:-1] 是排除自身的既有标题。
        bg = _title_bigrams(title)
        sims = seen.setdefault("bigrams", [])
        if bg:
            sims.append(bg)
            if len(sims) > 1 and any(
                _jaccard(bg, other) >= _TITLE_SIM_THRESHOLD for other in sims[:-1]
            ):
                continue
        if key_url:
            seen["url"].add(key_url)
        if key_title:
            seen["title"].add(key_title)

        out.append(it)
    return out


def short_cat(name: str) -> str:
    """把分类标题精简为干净的导航名:去掉 emoji 和“新闻/发展”等后缀。

    “📰 时政新闻”→“时政”、“🌆 上海发展”→“上海”、“🏙 浙江”→“浙江”。
    """
    s = re.sub(r"[\U0001F000-\U0001FAFF☀-➿‍️]", "", name).strip()
    for suf in ("新闻", "发展"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
    return s.strip()


def _clip(text: str, n: int = 50) -> str:
    """标题超长截断(常见于 Facebook 帖文/长标题),避免单条撑爆版面。"""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


# 标题开头的发布时间戳前缀(2026-11-02 14:30 / 2026年11月3日):过滤/排序时保留作日期线索,
# 仅在显示时剥掉,避免用户看到裸日期标题。
_TITLE_PREFIX_RE = re.compile(
    r"^20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:\s+\d{1,2}:\d{2})?\s*"
)


def _strip_title_prefix(title: str) -> str:
    """剥掉显示用的标题噪音:开头时间戳、栏目名前缀、尾部日期。

    过滤与排序仍用原始标题(时间戳是旧闻判断的日期线索),只有展示时清洗,
    这样既不会漏判旧闻,用户也看不到「时政要闻 … 2026-11-03」这类脏标题。
    """
    s = _TITLE_PREFIX_RE.sub("", title.strip()).strip()
    s = _COLUMN_PREFIX_RE.sub("", s)
    s = _TRAILING_DATE_RE.sub("", s).strip()
    return s or title.strip()


def sort_items_recent(items: list, today: str) -> list:
    """按发布日期降序排(最新在前);无日期线索的排末尾,保持原相对顺序(稳定排序)。

    发布日期取 URL+标题+摘要里 <= 今天的最大日期(标题可能含未来事件日,
    取未来日期会错排到最前)。无任何日期线索的条目 key 为空串,降序时落在所有
    有日期条目之后。字符串比较即可:YYYY-MM-DD 字典序与时间序一致。
    """
    return sorted(items, key=lambda it: pub_date(it, today), reverse=True)


# 吃喝玩乐地域优先级(用户 2026-11-19 定):上海 > 沪周边 > 全国。
# 检测只用标题(消费向新闻标题几乎都带地名;snippet 常罗列多地会误判)。
# 普陀不进上海档(普陀山在舟山,属周边);单字「沪」命中沪上/沪苏等。
_GL_TITLE = "🍜 吃喝玩乐"
_GL_SH_RE = re.compile(
    r"上海|沪|申城|浦东|浦西|外滩|迪士尼|虹桥|临港|崇明|嘉定|松江|青浦|"
    r"奉贤|金山|闵行|宝山|徐汇|静安|杨浦|虹口|长宁|黄浦|新天地|南京路|豫园"
)
_GL_NEARBY_RE = re.compile(
    r"苏州|杭州|嘉兴|南通|无锡|宁波|南京|常州|湖州|绍兴|舟山|昆山|太仓|"
    r"张家港|江浙|长三角|江苏|浙江|周边"
)


def _gl_region_rank(it: dict) -> int:
    t = it.get("title", "")
    if _GL_SH_RE.search(t):
        return 0
    if _GL_NEARBY_RE.search(t):
        return 1
    return 2


def sort_items_for(cat_title: str, items: list, today: str) -> list:
    """分类感知的排序:吃喝玩乐按「上海 > 沪周边 > 全国」地域优先级,
    同档内最新在前;其余分类仍是纯日期倒序。

    实现:先 sort_items_recent(日期倒序),再按档位稳定排序——稳定排序
    保证同档内日期倒序不被打乱。fetch_all 与 gen_pc_from_html 共用此函数,
    保证移动/PC/微信预览三端顺序一致。
    """
    ordered = sort_items_recent(items, today)
    if cat_title == _GL_TITLE:
        ordered = sorted(ordered, key=_gl_region_rank)
    return ordered


def _render_news_item(it: dict, today: str = "") -> str:
    """渲染单条新闻:标题超链接 + 来源/日期元信息 + 一句摘要。"""
    title_text = html.escape(_clip(_strip_title_prefix(it["title"])))
    if it["url"]:
        url = html.escape(it["url"], quote=True)
        title_line = f'<a class="news-title" href="{url}" target="_blank" rel="noopener noreferrer">{title_text}</a>'
    else:
        title_line = f'<span class="news-title">{title_text}</span>'

    meta_bits = []
    if it.get("source"):
        meta_bits.append(html.escape(it["source"]))
    d = pub_date(it, today)
    if d:
        meta_bits.append(d[5:])  # YYYY-MM-DD -> MM-DD
    meta_html = f'<p class="news-meta">{" · ".join(meta_bits)}</p>' if meta_bits else ""

    snippet_html = ""
    snip = _clean_snippet(it.get("snippet", ""), _strip_title_prefix(it.get("title", "")))
    if snip:
        snippet_html = f'<p class="news-snippet">{html.escape(snip)}</p>'

    return "\n".join(p for p in (
        '<article class="news">', title_line, meta_html, snippet_html, "</article>"
    ) if p)


def _render_news_group(items: list, region: str, is_default: bool, today: str = "") -> str:
    """渲染一个地域(国内/国外)的条目组,包裹在 .news-group 里。

    is_default=True 的是默认显示的地域(国内),其余用 hidden 属性默认隐藏
    (无 JS 时由 noscript 样式兜底展开)。
    """
    hidden = "" if is_default else " hidden"
    if not items:
        return f'<div class="news-group {region}"{hidden}><p class="empty">此类暂无更新。</p></div>'
    body = "\n".join(_render_news_item(it, today) for it in items)
    return f'<div class="news-group {region}"{hidden}>\n{body}\n</div>'


_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='12' fill='%238B2A2A'/%3E"
    "%3Ctext x='32' y='45' font-size='34' text-anchor='middle' "
    "fill='%23F9F7F2' font-family='serif'%3E报%3C/text%3E%3C/svg%3E"
)

# 移动版/PC 版共用交互:分类 tab、国内/国外切换、URL hash 深链、键盘导航、回到顶部。
# 走 data-* 属性定位元素,两版 DOM 结构不同也能复用同一段脚本。
_PAGE_JS = """
(function(){
  var tabs=[].slice.call(document.querySelectorAll('[data-cat]'));
  var panels=[].slice.call(document.querySelectorAll('[data-panel]'));
  var regionBtns=[].slice.call(document.querySelectorAll('[data-region]'));
  var groups=[].slice.call(document.querySelectorAll('.news-group'));
  var state={cat:0,region:'domestic'};

  function clampCat(v){v=parseInt(v,10);return (isNaN(v)||v<0||v>=panels.length)?0:v;}
  function render(scroll){
    panels.forEach(function(p){p.hidden=parseInt(p.dataset.panel,10)!==state.cat;});
    groups.forEach(function(g){g.hidden=!g.classList.contains(state.region);});
    tabs.forEach(function(t){
      var on=parseInt(t.dataset.cat,10)===state.cat;
      t.classList.toggle('active',on);
      t.setAttribute('aria-selected',on?'true':'false');
      t.tabIndex=on?0:-1;
    });
    regionBtns.forEach(function(b){
      var on=b.dataset.region===state.region;
      b.classList.toggle('active',on);
      b.setAttribute('aria-pressed',on?'true':'false');
    });
    if(scroll){
      var a=tabs[state.cat];
      if(a&&a.scrollIntoView)a.scrollIntoView({block:'nearest',inline:'center'});
      window.scrollTo({top:0,behavior:'smooth'});
    }
  }
  function parseHash(){
    var h=location.hash.replace(/^#/,'');
    if(!h)return;
    try{
      var p=new URLSearchParams(h);
      if(p.has('cat'))state.cat=clampCat(p.get('cat'));
      var r=p.get('region');
      if(r==='domestic'||r==='foreign')state.region=r;
    }catch(e){}
  }
  var STORE_KEY='news-digest-state';
  function saveState(){
    try{localStorage.setItem(STORE_KEY,JSON.stringify(state));}catch(e){}
  }
  function loadSaved(){
    try{
      var s=JSON.parse(localStorage.getItem(STORE_KEY)||'null');
      if(s&&(s.region==='domestic'||s.region==='foreign')){
        state.cat=clampCat(s.cat);state.region=s.region;return true;
      }
    }catch(e){}
    return false;
  }
  function writeHash(){
    var hash='#cat='+state.cat+'&region='+state.region;
    if(location.hash!==hash&&history.replaceState)history.replaceState(null,'',hash);
    saveState();
  }
  function selectCat(i,scroll){state.cat=i;render(scroll);writeHash();}

  tabs.forEach(function(t){
    t.addEventListener('click',function(){selectCat(parseInt(t.dataset.cat,10)||0,true);});
    t.addEventListener('keydown',function(e){
      var k=e.key,i=state.cat;
      if(k==='ArrowRight')i=(i+1)%tabs.length;
      else if(k==='ArrowLeft')i=(i-1+tabs.length)%tabs.length;
      else if(k==='Home')i=0;
      else if(k==='End')i=tabs.length-1;
      else return;
      e.preventDefault();selectCat(i,true);tabs[i].focus();
    });
  });
  regionBtns.forEach(function(b){
    b.addEventListener('click',function(){state.region=b.dataset.region;render(false);writeHash();});
  });

  var topBtn=document.getElementById('toTop');
  if(topBtn){
    var sync=function(){topBtn.hidden=window.scrollY<600;};
    window.addEventListener('scroll',sync,{passive:true});
    topBtn.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
    sync();
  }
  window.addEventListener('hashchange',function(){parseHash();render(false);});
  var hadHash=location.hash.replace(/^#/,'').length>0;
  parseHash();
  if(!hadHash)loadSaved();  // 无深链时恢复上次浏览的分类/地域
  render(false);
})();
"""


def _render_panels(today: str, categories_data: list, pc: bool) -> str:
    """渲染全部分类面板,国内/国外两组条目共用同一套 item/group 渲染。"""
    prefix = "pc-panel-" if pc else "panel-"
    panels = []
    for i, cat in enumerate(categories_data):
        hidden = "" if i == 0 else " hidden"
        title = html.escape(short_cat(cat["category"]))
        d, f = len(cat["domestic"]), len(cat["foreign"])
        body = (
            _render_news_group(cat["domestic"], "domestic", True, today)
            + "\n"
            + _render_news_group(cat["foreign"], "foreign", False, today)
        )
        if pc:
            head = (
                '<div class="panel-head">\n'
                f'<h2 class="panel-title">{title}</h2>\n'
                f'<p class="panel-count">国内 {d} · 国外 {f}</p>\n'
                '</div>'
            )
        else:
            head = (
                f'<h2 class="panel-title">{title}</h2>\n'
                f'<p class="panel-count">国内{d}条 · 国外{f}条</p>'
            )
        panels.append(
            f'<section class="panel" id="{prefix}{i}" role="tabpanel" '
            f'aria-labelledby="{prefix}tab-{i}" data-panel="{i}"{hidden}>\n'
            f'{head}\n{body}\n</section>'
        )
    return "\n".join(panels)


def _render_page(today: str, categories_data: list, pc: bool) -> str:
    """生成完整页面。PC/移动两版共用头部结构、交互脚本与条目渲染,仅布局不同。"""
    dom_total = sum(len(c["domestic"]) for c in categories_data)
    for_total = sum(len(c["foreign"]) for c in categories_data)
    n = len(categories_data)
    updated = _beijing_now().strftime("%H:%M")
    prefix = "pc-panel-" if pc else "panel-"
    tab_class = "tab-btn" if pc else "cat"
    nav_class = "tabs" if pc else "cats"
    region_class = "region-btn" if pc else "region"
    region_wrap_class = "region-bar" if pc else "regions"
    css = _CSS_PC if pc else _CSS

    tab_btns = []
    for i, cat in enumerate(categories_data):
        name = html.escape(short_cat(cat["category"]))
        cls = f"{tab_class} active" if i == 0 else tab_class
        selected = "true" if i == 0 else "false"
        tabindex = "0" if i == 0 else "-1"
        tab_btns.append(
            f'<button type="button" class="{cls}" role="tab" id="{prefix}tab-{i}" '
            f'aria-controls="{prefix}{i}" aria-selected="{selected}" tabindex="{tabindex}" '
            f'data-cat="{i}">{name}</button>'
        )

    region_bar = (
        f'<div class="{region_wrap_class}" role="group" aria-label="地域切换">\n'
        f'<button type="button" class="{region_class} domestic active" data-region="domestic" '
        'aria-pressed="true">国内</button>\n'
        f'<button type="button" class="{region_class} foreign" data-region="foreign" '
        'aria-pressed="false">国外</button>\n'
        '</div>'
    )

    archive_href = "index.html" if pc else f"digest-{today}.html"
    archive_label = "移动版" if pc else "PC 宽屏版"
    meta_label = " · PC版" if pc else ""
    desc = (
        f"每日新闻摘要 {today}:时政、财经、科技、教育、医疗、育儿、浙江、"
        "上海、南通、吃喝玩乐 10 类新闻,国内/国外双视角(吃喝玩乐仅国内)。"
    )

    head = (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f'<title>每日新闻摘要{"(PC版) " if pc else " "}{html.escape(today)}</title>\n'
        f'<meta name="description" content="{html.escape(desc)}">\n'
        f'<meta property="og:title" content="每日新闻摘要 {html.escape(today)}">\n'
        f'<meta property="og:description" content="{html.escape(desc)}">\n'
        '<meta property="og:type" content="website">\n'
        '<meta name="twitter:card" content="summary">\n'
        '<meta name="theme-color" content="#F9F7F2" media="(prefers-color-scheme: light)">\n'
        '<meta name="theme-color" content="#16140F" media="(prefers-color-scheme: dark)">\n'
        f'<link rel="icon" href="{_FAVICON}">\n'
        f'<style>\n{css}\n</style>\n'
        '<noscript><style>.panel[hidden],.news-group[hidden]{display:block!important}</style></noscript>\n'
        '</head>\n'
    )

    header = (
        '<header class="masthead">\n'
        '<h1 class="masthead-title">每日新闻摘要</h1>\n'
        f'<p class="masthead-meta">{html.escape(today)} · 更新 {updated} · {n}类 · '
        f'国内{dom_total}条 · 国外{for_total}条{meta_label}</p>\n'
        f'{region_bar}\n'
        '</header>'
    )
    nav = f'<nav class="{nav_class}" role="tablist" aria-label="新闻分类">' + "".join(tab_btns) + '</nav>'
    main = '<main id="main" tabindex="-1">\n' + _render_panels(today, categories_data, pc) + '\n</main>'
    footer = (
        '<footer>每日 01:00 自动更新 · 点标题跳转原文 · '
        f'<a href="{archive_href}">{archive_label}</a></footer>'
    )
    body_inner = header + "\n" + nav + "\n" + main
    if pc:
        body_inner = '<div class="wrap">\n' + body_inner + '\n</div>'

    return (
        head
        + '<body id="top">\n'
        + '<a class="skip-link" href="#main">跳到正文</a>\n'
        + body_inner + "\n"
        + footer + "\n"
        + '<button type="button" id="toTop" class="to-top" aria-label="回到顶部" hidden>↑</button>\n'
        + '<script>\n' + _PAGE_JS + '</script>\n'
        + '</body>\n</html>'
    )


def build_html(today: str, categories_data: list) -> str:
    """移动端页面:刊头 + 国内/国外切换 + sticky 分类导航 + 发丝线分隔的条目流。"""
    return _render_page(today, categories_data, pc=False)


def build_html_pc(today: str, categories_data: list) -> str:
    """PC 宽屏页面:报纸风 serif 排版,分类 tab + 国内/国外切换,单列居中阅读。"""
    return _render_page(today, categories_data, pc=True)


# 微信消息每类每地域仅预览前 N 条(标题+摘要),其余靠「查看今日全部新闻」链接兜底。
# 全量推送(各类×2地域×10条含摘要,约160+条)会超 Server酱 desp 字段上限,
# 报 HTTP 400 code=30001 "Data too long for column 'desp'"(2026-11-16 实测)。
WECHAT_PREVIEW_N = 3


def build_wechat_msg(today: str, categories_data: list, page_url: str) -> tuple:
    """微信消息(A版,适配国内/国外):按地域分两大段,段内按分类分组。

    排版向预览页靠拢(在 Server酱 markdown 能力内):
      - 刊头 + 分割线,地域段标题带 emoji 与总条数;
      - 每条新闻(标题链接 + 摘要)整体用引用块 > 包裹,微信渲染为左侧竖线区块,
        条与条之间空行分隔,近似预览页发丝线分隔的层次感;
      - 每个分类之间一道分割线,避免一长串糊到底。
    [text](url) 在微信里显示为可点蓝字;URL 用尖括号包裹以容忍特殊字符。
    每类每地域只预览 WECHAT_PREVIEW_N 条,超出部分以「其余 N 条见今日页面」提示,
    回归 README 设计的「瘦身消息:链接 + 今日提要」。
    """
    dom_total = sum(len(c["domestic"]) for c in categories_data)
    for_total = sum(len(c["foreign"]) for c in categories_data)
    n = len(categories_data)
    url = page_url.rstrip("/") + "/"

    lines = [
        "# 📰 每日新闻摘要",
        f"{today} · {n}类 · 国内{dom_total}条 · 国外{for_total}条",
        "",
        "---",
        "",
    ]
    region_specs = (("国内", "domestic", "🇨🇳"), ("国外", "foreign", "🌍"))
    first_region = True
    for region_label, region_key, emoji in region_specs:
        total = sum(len(c[region_key]) for c in categories_data)
        if total == 0:
            continue
        if not first_region:
            lines += ["---", ""]  # 地域段之间一道分割线
        first_region = False
        lines += [f"## {emoji} {region_label}（{total}条）", ""]
        for cat in categories_data:
            items = cat[region_key]
            if not items:
                continue
            lines += [f"### {cat['category']}", ""]
            shown = items[:WECHAT_PREVIEW_N]
            for idx, it in enumerate(shown):
                # 转义 markdown 链接文本里的特殊字符,避免 ] 或 \ 提前闭合 [text](url);
                # 显示标题剥掉发布时间戳前缀(过滤/排序时保留作日期线索)
                t = _clip(_strip_title_prefix(it["title"])).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                if it["url"]:
                    # URL 用尖括号包裹,容忍 URL 内含 ) 等特殊字符(Wiki 类链接常见)
                    lines.append(f"> [{t}](<{it['url']}>)")
                else:
                    lines.append(f"> {t}")
                snip = _clean_snippet(it.get("snippet", ""), _strip_title_prefix(it.get("title", "")))
                if snip:
                    lines.append(f"> {snip}")
                # 引用块内条目间空行(单独的 > 行),微信渲染为同一竖线区块内的间隔
                if idx < len(shown) - 1:
                    lines.append(">")
            # 预览之外的条数提示,引导点底部链接看全部
            rest = len(items) - len(shown)
            if rest > 0:
                lines.append(f"> ……其余 {rest} 条见今日页面")
            lines.append("")  # 分类之间仅空行,不堆分割线

    lines += [
        "---", "",
        f"[📰 查看今日全部新闻]({url})",
        f"[💻 电脑宽屏版]({page_url.rstrip('/')}/digest-{today}.html)",
    ]
    md = "\n".join(lines)
    # 体积日志:desp 超限报 HTTP 400(30001),提前打印字节数便于诊断
    sys.stderr.write(f"[微信消息] desp {len(md.encode('utf-8'))} bytes\n")
    return f"每日新闻摘要 {today}", md


def _expand_queries(today: str) -> list:
    """展开查询任务 → [(cat_idx, region, query)],跳过有 RSS 源的那一侧。

    RSS_FEEDS 里该侧 feeds 非空的分类走 RSS 直抓,不查 anysearch;
    各分类双侧全覆盖时 tasks 为空(零 anysearch 请求,限流问题消失;空 q 的一侧不生成查询),
    仅某侧 feeds 为空或 RSS 全部抓取失败才需要对应查询兜底。
    """
    tasks = []
    for i, (title, q_dom, q_for) in enumerate(CATEGORIES):
        feeds = RSS_FEEDS.get(title, {})
        # q 为空串 = 该侧刻意关闭(如吃喝玩乐无国外侧):不生成查询
        if not feeds.get("dom") and q_dom:
            tasks.append((i, "dom", f"{q_dom} {today}"))
        if not feeds.get("for") and q_for:
            tasks.append((i, "for", f"{q_for} {today}"))
    return tasks


def _fallback_search(query: str, max_results: int, today: str) -> list:
    """主查询为空时兜底:去掉日期再搜一次,尽量别让分类开天窗。"""
    try:
        body = search(query.replace(f" {today}", ""), max_results=max_results)
        return parse_results(body) if body else []
    except Exception as e:
        sys.stderr.write(f"[兜底失败] {query}: {e}\n")
        return []


def _topup(items: list, seen: dict, today: str, max_days: int,
           q_base: str, max_results: int, top_n: int,
           interval: float = 0.0) -> list:
    """过滤后不足 top_n 时,用原始措辞(无日期)补搜一轮,合并去重。

    anysearch 单次最多返回 10 条,无法靠提高 max_results 超过 10;
    故用「去日期换措辞」补搜一次,拿额外候选。seen 与主流程共享,
    clean_items 内部基于 seen 去重,所以两次过滤结果相加不会重复。
    interval>0 时在补搜前 sleep,避免与主 batch 或前后补搜连发触发限流。
    返回未排序的合并列表(调用方负责 sort_items_recent + 截断)。
    """
    if len(items) >= top_n:
        return items
    if interval > 0:
        time.sleep(interval)
    extra_raw = _fallback_search(q_base, max_results, today)
    if not extra_raw:
        return items
    extra = clean_items(extra_raw, today, seen=seen, max_days=max_days)
    if not extra:
        return items
    return items + extra


def _filter_tiered(raw, seen, today, top_n, max_days_cap,
                   q_base, max_results, interval):
    """动态窗口过滤:先近 2 天,不足逐级放宽到 7 天、max_days_cap,仍不足才补搜。

    优先展示最新——旧闻只在 raw 里近 2 天/7 天都不够 top_n 时才出现。
    各级 clean_items 共享 seen(已加入的不会重复),结果累加,调用方负责 sort+截断。
    raw 与 q_base 双空 = 该侧刻意关闭(如无国外侧的分类),返回空不补搜。
    """
    if not raw and not q_base:
        return []
    tiers = [t for t in sorted({2, 7, max_days_cap}) if t <= max_days_cap] or [max_days_cap]
    items = []
    for tier in tiers:
        items += clean_items(raw, today, seen=seen, max_days=tier)
        if len(items) >= top_n:
            break
    else:
        # 各级都不足 top_n:补搜兜底(去日期措辞,max_days_cap 窗口)
        before = len(items)
        items = _topup(items, seen, today, max_days_cap, q_base, max_results, top_n, interval)
        sys.stderr.write(f"[补搜] {q_base[:14]} {before}→{len(items)}\n")
    return items


def fetch_all() -> tuple:
    """抓取所有分类(每类国内 + 国外),返回 (today, categories_data)。

    18 条查询按每 5 条一批走 batch_search,4 次请求即可完成;
    单批失败自动降级逐条,某条查询解析为空时再去掉日期兜底重搜一次。
    """
    today = beijing_date()
    refresh = os.environ.get("REFRESH", "").strip() not in ("", "0", "false", "False")
    # 成品缓存优先:同一天重复渲染(改样式/改前端)零网络、零抖动
    if not refresh:
        cached = _load_final_cache(today)
        if cached:
            total_d = sum(len(c["domestic"]) for c in cached)
            total_f = sum(len(c["foreign"]) for c in cached)
            sys.stderr.write(
                f"[缓存] 命中当天成品 {_final_cache_file(today)},直接复用"
                f"(国内{total_d} · 国外{total_f});REFRESH=1 可重抓\n"
            )
            return today, cached
    max_results = _env_num("MAX_RESULTS", 10, int, 1, 10)
    # 旧闻 cutoff 天数:默认 7(宁可少而精),可用 MAX_DAYS 放宽到 30 等
    max_days = _env_num("MAX_DAYS", 7, int, 1, 365)
    # batch 批次之间的间隔(秒):避免触发 anysearch 限流
    search_interval = _env_num("SEARCH_INTERVAL", 1.0, float, 0.0, 60.0)
    # 每个地域(国内/国外)最终展示条数:过滤+排序后截断到 top_n
    top_n = _env_num("TOP_N", 10, int, 1, 30)

    tasks = _expand_queries(today)
    queries = [t[2] for t in tasks]
    bodies = [] if refresh else _load_raw_cache(today, queries)
    if bodies:
        sys.stderr.write(f"[缓存] 命中当天原始结果 {_cache_file(today)},跳过抓取\n")
    elif queries:
        rss_cats = sum(1 for t in CATEGORIES if RSS_FEEDS.get(t[0], {}).get("dom"))
        sys.stderr.write(
            f"[抓取] {len(CATEGORIES)} 类 / {len(queries)} 条 anysearch 查询"
            f"({rss_cats} 类国内走 RSS),batch 模式(每批 5 条)...\n"
        )
        bodies = search_many(queries, max_results=max_results, interval=search_interval)
        _save_raw_cache(today, queries, bodies)
    else:
        # 各分类双侧全部有 RSS feeds(或该侧刻意关闭):零 anysearch 主查询,gnews+chinanews 直抓
        gnews_feeds = sum(len(f.get("dom", [])) + len(f.get("for", [])) for f in RSS_FEEDS.values())
        sys.stderr.write(
            f"[抓取] {len(CATEGORIES)} 类全部走 RSS({gnews_feeds} 个 feed,含 Google News)...\n"
        )
        bodies = []

    # 按 (cat_idx, region) 聚合 anysearch raw(仅 feeds 为空的一侧才有查询;RSS 失败兜底用)
    raw_by_cat = {}
    for (cat_idx, region, _), body in zip(tasks, bodies):
        parsed = parse_results(body) if body else []
        raw_by_cat.setdefault(cat_idx, {"dom": [], "for": []})[region] = parsed

    def _region_raw(idx: int, title: str, region: str, q_base: str) -> list:
        """一侧的取数:该侧所有 RSS feed 逐个抓取合并;全失败/空才回退
        anysearch raw(feeds 为空的一侧),再空才去日期兜底重搜。
        q_base 为空且无 feeds = 该侧刻意关闭(无国外侧的分类),直接返回空。"""
        feeds = RSS_FEEDS.get(title, {}).get(region) or []
        if not feeds and not q_base:
            return []
        items = []
        for u in feeds:
            got = fetch_rss(u)
            if got:
                short = u.rsplit("/", 1)[-1] if "chinanews" in u else "gnews:" + u.split("q=")[-1][:24]
                sys.stderr.write(f"[RSS] {title} {'国内' if region == 'dom' else '国外'} {short} {len(got)}条\n")
            items.extend(got)
        if items:
            return items
        raw = raw_by_cat.get(idx, {}).get(region, [])
        if raw:
            return raw
        # 兜底重搜(去日期措辞)——连发易触发限流,前 sleep
        sys.stderr.write(f"[兜底] {title} {'国内' if region == 'dom' else '国外'} ...\n")
        if search_interval > 0:
            time.sleep(search_interval)
        return _fallback_search(q_base, max_results, today)

    categories_data = []
    for idx, (title, q_dom, q_for) in enumerate(CATEGORIES):
        dom_raw = _region_raw(idx, title, "dom", q_dom)
        fore_raw = _region_raw(idx, title, "for", q_for)

        sys.stderr.write(f"[解析] {title}: 国内raw{len(dom_raw)} 国外raw{len(fore_raw)}\n")
        seen = {"url": set(), "title": set()}  # 国内+国外共享,同分类内不重复
        # 动态窗口:先近2天,不足逐级放宽到7天、max_days,仍不足才补搜——优先展示最新
        dom = _filter_tiered(dom_raw, seen, today, top_n, max_days, q_dom, max_results, search_interval)
        fore = _filter_tiered(fore_raw, seen, today, top_n, max_days, q_for, max_results, search_interval)
        sys.stderr.write(f"[窗口] {title}: 国内{len(dom)} 国外{len(fore)}\n")
        categories_data.append({
            "category": title,
            "domestic": sort_items_for(title, dom, today)[:top_n],
            "foreign": sort_items_for(title, fore, today)[:top_n],
        })

    total_d = sum(len(c["domestic"]) for c in categories_data)
    total_f = sum(len(c["foreign"]) for c in categories_data)
    sys.stderr.write(f"[汇总] 国内{total_d}条 · 国外{total_f}条\n")
    if total_d + total_f:
        _save_final_cache(today, categories_data)
    return today, categories_data


def _total_items(categories_data: list) -> int:
    return sum(len(c.get("domestic", [])) + len(c.get("foreign", []))
               for c in categories_data)


def push_serverchan(title: str, md: str) -> dict:
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not key:
        raise SystemExit("❌ 未设置环境变量 SERVERCHAN_SENDKEY")
    # 诊断:只打印长度和前缀布尔,不泄露密钥(GitHub 会给真实值打码)
    sys.stderr.write(f"[推送] key 长度={len(key)} SCT前缀={key.startswith('SCT')}\n")
    url = f"https://sctapi.ftqq.com/{key}.send"
    # 日志里对 key 打码,避免本地跑推送时密钥落到终端
    sys.stderr.write(f"[推送] POST https://sctapi.ftqq.com/{key[:3]}***.send\n")
    try:
        resp = requests.post(url, data={"title": title, "desp": md}, timeout=60)
    except requests.RequestException as e:
        # 网络异常的消息会带完整 URL(含 key),用 type 名代替,不打印 e 本身
        raise SystemExit(f"❌ 推送请求异常: {type(e).__name__}")
    if not resp.ok:
        # 不用 resp.raise_for_status()——它的 HTTPError 消息里含完整 URL(含 key)
        sys.stderr.write(f"[推送失败] HTTP {resp.status_code} 响应体: {resp.text[:1000]}\n")
        raise SystemExit(f"❌ 推送失败 HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        j = resp.json()
    except ValueError:
        raise SystemExit(f"❌ Server酱返回非 JSON: {resp.text[:500]}")
    if j.get("code") not in (0, None):
        raise SystemExit(f"❌ Server酱返回错误: {j}")
    return j


def main():
    today, categories_data = fetch_all()
    if _total_items(categories_data) == 0:
        # 全空多半是限流/额度用尽:保留上一次的页面与缓存,不用空页覆盖
        raise SystemExit(
            "❌ 全部模块抓取为空(疑似 anysearch 限流/额度用尽),"
            "已保留原有页面不覆盖;请设置 ANYSEARCH_API_KEY 或稍后重试"
        )

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "index.html")
    _write_text_atomic(out_path, build_html(today, categories_data))
    sys.stderr.write(f"[生成] {out_path}\n")
    # PC 版宽屏页面,文件名含日期,便于按日归档(移动版 index.html 仍作 Pages 首页)
    pc_path = os.path.join(OUT_DIR, f"digest-{today}.html")
    _write_text_atomic(pc_path, build_html_pc(today, categories_data))
    sys.stderr.write(f"[生成] {pc_path} (PC版)\n")

    page_url = os.environ.get("PAGES_BASE_URL", "").strip()
    if page_url:
        title, md = build_wechat_msg(today, categories_data, page_url)
        push_serverchan(title, md)
        sys.stderr.write(f"[完成] 已推送到微信,页面 {page_url}\n")
    else:
        sys.stderr.write(f"[跳过推送] 未设 PAGES_BASE_URL,本地预览打开 {out_path}\n")


if __name__ == "__main__":
    main()
