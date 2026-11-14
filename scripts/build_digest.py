#!/usr/bin/env python3
"""每日新闻摘要 —— 抓取 9 类新闻(每类分国内/国外),生成 tab 页 HTML,推送到微信(Server酱)。

流程:
  1. 调 anysearch 逐类抓取最新新闻(每类国内 + 国外各 MAX_RESULTS 条)
  2. 解析成结构化数据(标题/网址/摘要)
  3. 生成带 9 个分类 tab 的 HTML 页面:顶部「国内/国外」切换,每个主题面板显示对应那一半
  4. 若设了 PAGES_BASE_URL,推一条微信消息(国内/国外分段)到 Server酱

环境变量:
  SERVERCHAN_SENDKEY   推送时必填,Server酱 SendKey
  ANYSEARCH_API_KEY    可选,提高 anysearch 速率上限
  MAX_RESULTS          可选,每类每个地域抓取条数,默认 10
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
import time
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

# (分类标题, 国内搜索词, 国外搜索词) —— 搜索词会自动追加当天北京日期
# 每个主题分国内/国外各抓 MAX_RESULTS 条,页面顶部「国内/国外」切换决定看哪一半
# 用"最新"提时效,避免用"头条新闻"这类词(它反而把网站首页/导航页顶上来);
# 地域类主题(江浙沪皖/上海/南通)的"国外"用涉外/国际视角的搜索词
CATEGORIES = [
    ("📰 时政新闻", "最新 时政 要闻", "国际 时政 要闻"),
    ("📚 教育新闻", "最新 教育 新闻", "国际 教育 新闻"),
    ("🌐 全球科技", "国内 最新 科技 新闻", "全球 最新 科技 新闻"),
    ("💰 财经新闻", "最新 财经 股市", "国际 最新 财经 股市"),
    ("🏥 全球医疗", "国内 最新 医疗 健康", "全球 最新 医疗 健康"),
    ("🏙 江浙沪皖", "江苏 浙江 上海 安徽 最新 新闻", "江苏 浙江 上海 安徽 国际 涉外"),
    ("🌆 上海发展", "上海 最新 发展 新闻", "上海 国际 涉外 新闻"),
    ("🏖 南通发展", "南通 最新 发展 新闻", "南通 国际 涉外 新闻"),
    ("👨‍👩‍👧 培养孩子", "上海 南通 家庭教育 育儿 儿童健康 最新 新闻", "parenting child development family policy news"),  # 国内限上海/南通本地育儿新闻;国外用英文搜真实国际育儿(中文搜国外只剩联合国专题页/政府公告)
]

# HTML 输出目录(GitHub Pages 产物根)
OUT_DIR = "public"

# tab 页样式(纯字符串,避免 f-string 与 CSS 花括号冲突)
_CSS = """
:root{
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
.panel{display:none}
.panel-title{margin:20px 0 2px;font-family:var(--serif);font-size:19px;font-weight:700;padding-left:10px;border-left:3px solid var(--accent)}
.panel-count{margin:0 0 6px;padding-left:13px;font-family:var(--serif);font-size:12px;color:var(--ink-2)}
.news{padding:13px 0;border-bottom:1px solid var(--rule)}
.news:last-child{border-bottom:0;padding-bottom:2px}
.news-title{display:block;font-family:var(--serif);font-size:16.5px;font-weight:600;color:var(--ink);text-decoration:none;line-height:1.42;overflow-wrap:anywhere;word-break:break-word}
.news-title:hover{color:var(--accent);text-decoration:underline}
.news-title:active{color:var(--accent)}
.news-title:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:1px}
.news-snippet{margin:6px 0 0;font-size:13.5px;color:var(--ink-2);line-height:1.62;overflow-wrap:anywhere;word-break:break-word}
.empty{margin:0;padding:18px 0;font-family:var(--serif);font-size:14px;color:var(--ink-2)}
footer{max-width:680px;margin:0 auto;padding:22px 16px calc(44px + env(safe-area-inset-bottom));font-family:var(--serif);font-size:12px;color:var(--ink-2);border-top:1px solid var(--rule)}
@media (max-width:360px){.masthead{padding-left:12px;padding-right:12px}main{padding-left:12px;padding-right:12px}footer{padding-left:12px;padding-right:12px}.masthead-title{font-size:23px}.cat{padding:12px 10px}.news-title{font-size:16px}.news-snippet{font-size:13px}}
@media (prefers-reduced-motion: reduce){.cat,.region{transition:none}}
"""

# PC 版样式:宽屏网格,每分类一个卡片,国内/国外同卡上下展示,无需 tab 切换
_CSS_PC = """
:root{
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
.news-snippet{margin:5px 0 0;font-size:13px;color:var(--ink-2);line-height:1.6;overflow-wrap:anywhere;word-break:break-word}
.empty{margin:0;padding:18px 0;font-family:var(--serif);font-size:14px;color:var(--ink-2)}
footer{max-width:860px;margin:24px auto 0;padding:18px 24px;font-family:var(--serif);font-size:12px;color:var(--ink-2);border-top:1px solid var(--rule)}
"""


def beijing_date() -> str:
    """GitHub Actions 跑在 UTC,统一用北京日期作为摘要日期。"""
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


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
        items.append({
            "title": title,
            "url": url,
            "source": domain_source(url),
            "snippet": " ".join(snippet_parts)[:220],
        })
    return items


# 视频站 / 社交平台 / 聚合首页 —— 抓到的几乎不是单条新闻,整域排除
_JUNK_HOSTS = {
    "youtube.com", "youtu.be",
    "facebook.com", "fb.com", "fb.watch",
    "bilibili.com", "b23.tv",
    "douyin.com", "tiktok.com",
    "weibo.com",
    "x.com", "twitter.com",
    "instagram.com",
    "news.google.com",
}

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
                    "新闻直播", "招生录取", "Newsroom")

# 标题(去空格后)完全等于这些通用词的,是栏目名不是单条新闻
_GENERIC_TITLES = {
    "国际新闻", "国内新闻", "时政要闻", "时政新闻", "财经新闻", "财政新闻",
    "教育新闻", "科技新闻", "医疗新闻", "健康新闻", "国际教育日",
    "热点新闻", "重要新闻", "滚动新闻", "最新新闻", "今日新闻",
    "早报", "晚报", "快讯", "播报", "热点", "要闻", "头条", "在线报导", "观察",
    # 裸地名/两三字栏目页残留(“上海”“南通”“经贸新闻”等)
    "上海", "南通", "经贸新闻", "数据发布", "外事日程", "国际会议", "Findings",
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
_PROMO_TITLE_HINTS = ("师资团队", "持证上岗", "复荐率", "硬核专业", "高口碑", "铸就",
                      "辅导效果", "家长反馈", "全解析", "避坑", "合规厂家")

# 通用栏目词后紧跟分隔符(_ - — · | [)的,是栏目页不是新闻(重要新闻_xxx / 时政要闻—xxx)
_GENERIC_PREFIX_RE = re.compile(
    r"^(重要新闻|热点新闻|滚动新闻|时政要闻|时政新闻|国际新闻|国内新闻|"
    r"财经新闻|财政新闻|教育新闻|科技新闻|医疗新闻|健康新闻|头条|要闻|早报|晚报)[_\-—–·|\[［]"
)


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


def clean_items(items: list, today: str, seen: dict = None, max_days: int = 7) -> list:
    """后处理过滤:去首页/导航页/视频站/裸URL标题/旧闻,按 URL+标题去重。

    seen 传入则跨分类共享去重(同一新闻不在多个分类重复出现);
    调用方在分类内传同一 seen 即可做到该分类国内+国外不重复。
    无任何日期线索的条目视为时效不明,保留(不误杀当天新闻)。
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

        # 裸 URL 当标题:标题本身就是 http... 或裸域名
        if not title or re.match(r"^https?://", title, re.I) \
                or re.match(r"^[\w.-]+\.[a-z]{2,}/?$", title, re.I):
            continue
        # 标题含导航/首页字样
        if any(h in title for h in _NAV_TITLE_HINTS):
            continue
        # 标题就是通用栏目名(国际新闻/早报/热点新闻 等)
        if re.sub(r"\s+", "", title) in _GENERIC_TITLES:
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
        # 视频站 / 社交 / 聚合首页(含子域)
        host = _host_base(url)
        if host and any(host == h or host.endswith("." + h) for h in _JUNK_HOSTS):
            continue
        # 站点根首页
        if _is_root_path(url):
            continue
        # 旧闻:日期从 URL、标题、摘要里提(anysearch 不返回 date 字段,
        # 凤凰大风号等站的发布日期常只在摘要里,只看 URL+标题会漏旧闻)
        snippet = it.get("snippet", "")
        if cutoff:
            # 发布日期取文本里 <= 今天的最大日期:标题可能含未来事件日
            # (如“2027年1月1日起施行”),取未来日期会误判为非旧闻而漏过过滤
            dates = _parse_all_dates(url + " " + title + " " + snippet, today_year)
            past = [d for d in dates if d <= today]
            if past:
                try:
                    if datetime.datetime.strptime(max(past), "%Y-%m-%d").date() < cutoff:
                        continue
                except Exception:
                    pass
        # 去重(分类内 + 跨分类共享)
        key_url = url.rstrip("/")
        key_title = re.sub(r"\s+", "", title)
        if key_url and key_url in seen["url"]:
            continue
        if key_title and key_title in seen["title"]:
            continue
        if key_url:
            seen["url"].add(key_url)
        if key_title:
            seen["title"].add(key_title)

        out.append(it)
    return out


def short_cat(name: str) -> str:
    """把分类标题精简为干净的导航名:去掉 emoji 和“新闻/发展”等后缀。

    “📰 时政新闻”→“时政”、“🌆 上海发展”→“上海”、“🏙 江浙沪皖”→“江浙沪皖”。
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


def sort_items_recent(items: list, today: str) -> list:
    """按发布日期降序排(最新在前);无日期线索的排末尾,保持原相对顺序(稳定排序)。

    发布日期取 URL+标题+摘要里 <= 今天的最大日期(标题可能含未来事件日,
    取未来日期会错排到最前)。无任何日期线索的条目 key 为空串,降序时落在所有
    有日期条目之后。字符串比较即可:YYYY-MM-DD 字典序与时间序一致。
    """
    today_year = today[:4] if today else ""

    def _key(it: dict) -> str:
        blob = " ".join((it.get("url", ""), it.get("title", ""), it.get("snippet", "")))
        dates = _parse_all_dates(blob, today_year)
        past = [d for d in dates if today and d <= today]
        return max(past) if past else ""

    return sorted(items, key=_key, reverse=True)


def _render_news_item(it: dict) -> str:
    """渲染单条新闻(标题超链接 + 摘要),供 build_html 复用。"""
    title_text = html.escape(_clip(it["title"]))
    if it["url"]:
        url = html.escape(it["url"], quote=True)
        title_line = f'<a class="news-title" href="{url}" target="_blank" rel="noopener noreferrer">{title_text}</a>'
    else:
        title_line = f'<span class="news-title">{title_text}</span>'
    if it["snippet"]:
        snippet = html.escape(it["snippet"])
        title_line += f'\n<p class="news-snippet">{snippet}</p>'
    return f'<article class="news">{title_line}</article>'


def _render_news_group(items: list, region: str, is_default: bool) -> str:
    """渲染一个地域(国内/国外)的条目组,包裹在 .news-group 里。

    is_default=True 的是默认显示的地域(国内),其余默认 display:none。
    """
    style = "" if is_default else ' style="display:none"'
    if not items:
        return f'<div class="news-group {region}"{style}><p class="empty">此类暂无更新。</p></div>'
    body = "\n".join(_render_news_item(it) for it in items)
    return f'<div class="news-group {region}"{style}>\n{body}\n</div>'


def build_html(today: str, categories_data: list) -> str:
    """生成“每日晨报”版面:刊头 masthead + 国内/国外切换 + sticky 分类导航 + 发丝线分隔的条目流。

    每个主题面板内含国内、国外两组条目,顶部「国内/国外」切换决定显示哪一组;
    分类 tab 切换决定显示哪个主题面板。标题做超链接(点开跳原文),默认墨色、hover 变暗红。
    """
    dom_total = sum(len(c["domestic"]) for c in categories_data)
    for_total = sum(len(c["foreign"]) for c in categories_data)
    n = len(categories_data)

    tab_btns = []
    for i, cat in enumerate(categories_data):
        name = html.escape(short_cat(cat["category"]))
        cls = "cat active" if i == 0 else "cat"
        tab_btns.append(
            f'<button type="button" class="{cls}" onclick="showTab({i})">{name}</button>'
        )

    region_bar = (
        '<div class="regions">\n'
        '<button type="button" class="region region-domestic active" '
        'onclick="showRegion(\'domestic\')">国内</button>\n'
        '<button type="button" class="region region-foreign" '
        'onclick="showRegion(\'foreign\')">国外</button>\n'
        '</div>'
    )

    panels = []
    for i, cat in enumerate(categories_data):
        style = "" if i == 0 else ' style="display:none"'
        title = html.escape(short_cat(cat["category"]))
        d = len(cat["domestic"])
        f = len(cat["foreign"])
        body = (
            _render_news_group(cat["domestic"], "domestic", is_default=True)
            + "\n"
            + _render_news_group(cat["foreign"], "foreign", is_default=False)
        )
        panels.append(
            f'<section class="panel" id="panel-{i}"{style}>\n'
            f'<h2 class="panel-title">{title}</h2>\n'
            f'<p class="panel-count">国内{d}条 · 国外{f}条</p>\n'
            f'{body}\n'
            f'</section>'
        )

    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"<title>每日新闻摘要 {html.escape(today)}</title>\n"
        "<style>\n" + _CSS + "\n</style>\n"
        "</head>\n<body>\n"
        '<header class="masthead">\n'
        '<h1 class="masthead-title">每日新闻摘要</h1>\n'
        f'<p class="masthead-meta">{html.escape(today)} · {n}类 · 国内{dom_total}条 · 国外{for_total}条</p>\n'
        f"{region_bar}\n"
        "</header>\n"
        '<nav class="cats">' + "".join(tab_btns) + "</nav>\n"
        '<main>\n' + "\n".join(panels) + "\n</main>\n"
        '<footer>每日更新 · 点标题可跳转原文 · 顶部切换国内/国外</footer>\n'
        '<script>\n'
        "function showTab(i){"
        "var p=document.querySelectorAll('.panel');"
        "for(var k=0;k<p.length;k++)p[k].style.display='none';"
        "document.getElementById('panel-'+i).style.display='block';"
        "var t=document.querySelectorAll('.cat');"
        "for(var k=0;k<t.length;k++)t[k].classList.remove('active');"
        "t[i].classList.add('active');"
        "t[i].scrollIntoView({block:'nearest',inline:'center'});"
        "window.scrollTo(0,0);"
        "}\n"
        "function showRegion(r){"
        "var g=document.querySelectorAll('.news-group');"
        "for(var k=0;k<g.length;k++){"
        "g[k].style.display=g[k].classList.contains(r)?'block':'none';"
        "}"
        "var b=document.querySelectorAll('.region');"
        "for(var k=0;k<b.length;k++)b[k].classList.remove('active');"
        "document.querySelector('.region-'+r).classList.add('active');"
        "}\n"
        "</script>\n"
        "</body>\n</html>"
    )


def build_html_pc(today: str, categories_data: list) -> str:
    """PC 版宽屏页面:分类 tab 切换 + 国内/国外切换,报纸风 serif 排版。

    与移动版 build_html 共用 _render_news_item / _render_news_group,仅布局与交互不同:
    sticky 分类 tab 横向导航,顶部「国内/国外」全局切换;单分类单列居中阅读(约 860px),
    发丝线分隔条目,点标题跳原文。切换分类时保持当前国内/国外选择。
    """
    dom_total = sum(len(c["domestic"]) for c in categories_data)
    for_total = sum(len(c["foreign"]) for c in categories_data)
    n = len(categories_data)

    tab_btns = []
    for i, cat in enumerate(categories_data):
        name = html.escape(short_cat(cat["category"]))
        cls = "tab-btn active" if i == 0 else "tab-btn"
        tab_btns.append(
            f'<button type="button" class="{cls}" onclick="showTab({i})">{name}</button>'
        )

    region_bar = (
        '<div class="region-bar">\n'
        '<button type="button" class="region-btn domestic active" '
        'onclick="showRegion(\'domestic\')">国内</button>\n'
        '<button type="button" class="region-btn foreign" '
        'onclick="showRegion(\'foreign\')">国外</button>\n'
        '</div>'
    )

    panels = []
    for i, cat in enumerate(categories_data):
        pstyle = "" if i == 0 else ' style="display:none"'
        title = html.escape(short_cat(cat["category"]))
        d = len(cat["domestic"])
        f = len(cat["foreign"])
        body = (
            _render_news_group(cat["domestic"], "domestic", is_default=True)
            + "\n"
            + _render_news_group(cat["foreign"], "foreign", is_default=False)
        )
        panels.append(
            f'<section class="panel" id="pc-panel-{i}"{pstyle}>\n'
            f'<div class="panel-head">\n'
            f'<h2 class="panel-title">{title}</h2>\n'
            f'<p class="panel-count">国内 {d} · 国外 {f}</p>\n'
            f'</div>\n'
            f'{body}\n'
            f'</section>'
        )

    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>每日新闻摘要(PC版) {html.escape(today)}</title>\n"
        "<style>\n" + _CSS_PC + "\n</style>\n"
        "</head>\n<body>\n"
        '<div class="wrap">\n'
        '<header class="masthead">\n'
        '<h1 class="masthead-title">每日新闻摘要</h1>\n'
        f'<p class="masthead-meta">{html.escape(today)} · {n}类 · 国内{dom_total}条 · 国外{for_total}条 · PC版</p>\n'
        f"{region_bar}\n"
        "</header>\n"
        '<nav class="tabs">' + "".join(tab_btns) + "</nav>\n"
        '<main>\n' + "\n".join(panels) + "\n</main>\n"
        "</div>\n"
        '<footer>每日更新 · 点标题可跳转原文 · 顶部切换国内/国外 · 分类 tab 切换主题</footer>\n'
        '<script>\n'
        "function showTab(i){"
        "var p=document.querySelectorAll('.panel');"
        "for(var k=0;k<p.length;k++)p[k].style.display='none';"
        "document.getElementById('pc-panel-'+i).style.display='block';"
        "var t=document.querySelectorAll('.tab-btn');"
        "for(var k=0;k<t.length;k++)t[k].classList.remove('active');"
        "t[i].classList.add('active');"
        "t[i].scrollIntoView({block:'nearest',inline:'center'});"
        "window.scrollTo(0,0);"
        "}\n"
        "function showRegion(r){"
        "var g=document.querySelectorAll('.news-group');"
        "for(var k=0;k<g.length;k++){"
        "g[k].style.display=g[k].classList.contains(r)?'block':'none';"
        "}"
        "var b=document.querySelectorAll('.region-btn');"
        "for(var k=0;k<b.length;k++)b[k].classList.remove('active');"
        "document.querySelector('.region-btn.'+r).classList.add('active');"
        "}\n"
        "</script>\n"
        "</body>\n</html>"
    )


def build_wechat_msg(today: str, categories_data: list, page_url: str) -> tuple:
    """微信消息(A版,适配国内/国外):按地域分两大段,段内按分类分组。

    排版向预览页靠拢(在 Server酱 markdown 能力内):
      - 刊头 + 分割线,地域段标题带 emoji 与总条数;
      - 每条新闻(标题链接 + 摘要)整体用引用块 > 包裹,微信渲染为左侧竖线区块,
        条与条之间空行分隔,近似预览页发丝线分隔的层次感;
      - 每个分类之间一道分割线,避免一长串糊到底。
    [text](url) 在微信里显示为可点蓝字;URL 用尖括号包裹以容忍特殊字符。
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
            for idx, it in enumerate(items):
                # 转义 markdown 链接文本里的特殊字符,避免 ] 或 \ 提前闭合 [text](url)
                t = _clip(it["title"]).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                if it["url"]:
                    # URL 用尖括号包裹,容忍 URL 内含 ) 等特殊字符(Wiki 类链接常见)
                    lines.append(f"> [{t}](<{it['url']}>)")
                else:
                    lines.append(f"> {t}")
                if it["snippet"]:
                    lines.append(f"> {it['snippet']}")
                # 引用块内条目间空行(单独的 > 行),微信渲染为同一竖线区块内的间隔
                if idx < len(items) - 1:
                    lines.append(">")
            lines.append("")  # 分类之间仅空行,不堆分割线

    lines += ["---", "", f"[📰 查看今日全部新闻]({url})"]
    md = "\n".join(lines)
    return f"每日新闻摘要 {today}", md


def fetch_all() -> tuple:
    """抓取所有分类(每类国内 + 国外),返回 (today, categories_data)。"""
    today = beijing_date()
    max_results = int(os.environ.get("MAX_RESULTS", "10"))
    # 旧闻 cutoff 天数:默认 7(宁可少而精),可用 MAX_DAYS 放宽到 30 等
    max_days = int(os.environ.get("MAX_DAYS", "7"))

    categories_data = []
    # 每次搜索间隔(秒):anysearch 连续高频请求会被限流,加间隔避免后半段返回空
    search_interval = float(os.environ.get("SEARCH_INTERVAL", "3"))
    for title, q_dom, q_for in CATEGORIES:
        sys.stderr.write(f"[抓取] {title} 国内 ...\n")
        try:
            dom_body = search(f"{q_dom} {today}", max_results=max_results)
        except Exception as e:
            dom_body = ""
            sys.stderr.write(f"[失败] {title} 国内: {e}\n")
        time.sleep(search_interval)
        sys.stderr.write(f"[抓取] {title} 国外 ...\n")
        try:
            for_body = search(f"{q_for} {today}", max_results=max_results)
        except Exception as e:
            for_body = ""
            sys.stderr.write(f"[失败] {title} 国外: {e}\n")
        time.sleep(search_interval)
        dom_raw = parse_results(dom_body) if dom_body else []
        fore_raw = parse_results(for_body) if for_body else []
        sys.stderr.write(f"[解析] {title}: 国内raw{len(dom_raw)} 国外raw{len(fore_raw)}\n")
        seen = {"url": set(), "title": set()}  # 国内+国外共享,同分类内不重复
        dom = clean_items(dom_raw, today, seen=seen, max_days=max_days)
        fore = clean_items(fore_raw, today, seen=seen, max_days=max_days)
        sys.stderr.write(f"[过滤] {title}: 国内{len(dom)} 国外{len(fore)}\n")
        categories_data.append({
            "category": title,
            "domestic": dom,
            "foreign": fore,
        })

    # 全分类按发布日期降序(最新在前),让 index/digest/微信消息统一呈现最新内容
    for cat in categories_data:
        cat["domestic"] = sort_items_recent(cat["domestic"], today)
        cat["foreign"] = sort_items_recent(cat["foreign"], today)
    return today, categories_data


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

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_html(today, categories_data))
    sys.stderr.write(f"[生成] {out_path}\n")
    # PC 版宽屏页面,文件名含日期,便于按日归档(移动版 index.html 仍作 Pages 首页)
    pc_path = os.path.join(OUT_DIR, f"digest-{today}.html")
    with open(pc_path, "w", encoding="utf-8") as f:
        f.write(build_html_pc(today, categories_data))
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
