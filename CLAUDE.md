# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

每日新闻摘要流水线：抓取 10 类新闻（每类分国内/国外；吃喝玩乐仅国内、q_for 留空），生成带分类 tab 的移动版 `public/index.html` + 按日归档的 PC 宽屏版 `public/digest-<日期>.html`，部署到 GitHub Pages 与 Cloudflare Pages（主分享域名），并经 Server酱 推一条瘦身消息到微信。纯 Python，唯一依赖 `requests`；测试用 stdlib unittest（`tests/` 目录）+ `node --test`（`worker/test/`）。

## 常用命令

```powershell
pip install -r requirements.txt
python scripts/build_digest.py          # 主流程:抓取 → 过滤 → 生成 HTML →(设了 PAGES_BASE_URL 才)推微信
python scripts/gen_pc_from_html.py      # 离线:从 public/index.html 重生成 PC 版(不抓取,改 PC 样式时用)
python -m http.server -d public         # 本地预览 http://localhost:8000

# 当天缓存:同日重跑直接复用成品零网络;想强制重抓
$env:REFRESH='1'; python scripts/build_digest.py
# 只改过滤/排序逻辑、想离线重滤缓存数据:删 .cache/final-<日期>.json 保留 raw-<日期>.json 再跑

# 远程手动触发 CI(GitHub Actions)
gh workflow run "每日新闻推送" --ref main
```

## 环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `PAGES_BASE_URL` | Pages 地址;**不设则只生成 HTML 不推送** | 空 |
| `SERVERCHAN_SENDKEY` | 推送必填(Server酱) | — |
| `ANYSEARCH_API_KEY` | 可选,提高 anysearch 速率;匿名有限流 | 空 |
| `STATS_READ_TOKEN` | 可选,拉昨日阅读统计(Cloudflare Worker `/stats`);不设则页面/微信不显示「昨日 N 人阅读」 | 空 |
| `MAX_RESULTS` / `TOP_N` | 每类每地域抓取条数 / 最终展示条数 | 10 / 10 |
| `MAX_DAYS` | 旧闻 cutoff 天数(CI 里放宽到 30) | 7 |
| `SEARCH_INTERVAL` | 搜索间隔秒数,防限流 | 1 |
| `REFRESH` | `1` 忽略当天缓存强制重抓 | 空 |

## 架构(读多个文件才能看懂的部分)

### 数据流(build_digest.py 为主编排)

1. **取数双层**:每分类每地域(国内 dom / 国外 for)优先走 `RSS_FEEDS` 直抓(时政/教育/财经的国内侧用中国新闻网官方 RSS;吃喝玩乐国内侧 gnews 双 feed 沪向+文旅;其余国内侧与全部国外侧用 Google News RSS 搜索 feed;医疗原 chinanews jk.xml 已停更 2026-03,勿改回),全部失败/为空才降级 anysearch 搜索(`_expand_queries` 跳过有 RSS 源的一侧及空 q 的关闭侧,各分类双侧全覆盖时主查询零 anysearch 请求)。anysearch 客户端在 `scripts/anysearch_search.py`(JSON-RPC,`search_many` 按 5 条/批切分并按 query 文本定位段落,单条失败逐条降级)。
2. **过滤**:`clean_items` —— 大量启发式正则(垃圾域名、栏目/专题/导航页、通用标题、活动/营销页、英文混入、攻略/清单页、繁体港台页等),跨国内/国外共享 `seen` 去重;同稿多版本(措辞微调的近重复)另按标题 bigram Jaccard ≥ 0.40 模糊去重(阈值有实测依据,勿随意下调,见 `_TITLE_SIM_THRESHOLD` 注释)。改这里要先读 `_JUNK_HOSTS` 到 `_GENERIC_PREFIX_RE` 一带的注释。旧闻判断的日期来源分两条路:RSS 条目用自带 `date` 字段(RFC822 pubDate,权威),anysearch 条目从 url+标题+摘要里提(取 ≤ 今天的最大日期,标题可能含未来事件日);`_render_news_item` 显示前才剥标题的时间戳/栏目前缀——过滤和排序依赖原始标题里的日期,别提前剥。
3. **动态时间窗**:`_filter_tiered` —— 先近 2 天,不足逐级放宽 7 天 → `MAX_DAYS`,仍不足 `_topup` 去日期补搜一轮。优先展示最新。
4. **排序截断**:`sort_items_for`(= `sort_items_recent` 纯日期倒序;吃喝玩乐国内特例:先日期倒序再按「上海 > 沪周边 > 全国」地域档稳定排序,同档内最新在前)→ `TOP_N` 截断;`gen_pc_from_html.py` 复用同一函数保证 PC 版顺序一致。
5. **渲染**:`build_html`(移动 tab 页)/ `build_html_pc`(宽屏);`gen_pc_from_html.py` 反向解析 index.html 复用 `clean_items`/`sort_items_recent`/`build_html_pc` 离线重生成 PC 版。
6. **推送**:`build_wechat_msg` + `push_serverchan`(仅设 `PAGES_BASE_URL` 时)。
7. **埋点与回流(Phase A 增长,2026-11-27)**:页面注入匿名埋点 JS(`_beacon_js`,仅设 `PAGES_BASE_URL` 时;beacon 目标是其绝对 URL,github.io 后备入口的访问同样上报)→ `POST {域名}/beacon` 记 view/tab/click(Cloudflare Worker + D1,代码在 `worker/`,一次性手动部署不进 CI);`_fetch_yesterday_stats` 构建前拉昨日统计 → 页面 masthead「昨日 N 人阅读 · 最热:X」与微信 meta 行,失败/未配 token 静默不显示,**绝不阻塞主流程**;北极星指标(次日回访率)靠手动 `GET /summary?days=N&token=...`。

### 缓存三层(`.cache/`,均按当天日期命名)

成品 `final-<日期>.json`(命中则零网络)→ 原始 `raw-<日期>.json`(命中则免抓取但重过滤)→ 都没有才真抓。`REFRESH=1` 全部无视。

### CI(.github/workflows/daily-push.yml)

cron 每天一轮(北京 04:02,UTC 分钟 :02 避抖动;实测 GitHub schedule 队列可延迟数小时,故从 05:02 提前),装依赖 → 跑 build_digest.py(注入 secrets 与 MAX_RESULTS=10/MAX_DAYS=30/TOP_N=10/SEARCH_INTERVAL=3)→ 部署 `./public` 到 GitHub Pages → 部署 `./public` 到 Cloudflare Pages(主分享域名,`continue-on-error`:CF 挂了 GitHub Pages 仍是兜底入口)→ 小程序数据打包上传体验版 → 滚动数据 commit 回 main。**北京 04:02 那轮(UTC cron `'2 20 * * *'`)和手动 dispatch 都设 `PAGES_BASE_URL` 推微信**——每天只推一条。Secrets:`SERVERCHAN_SENDKEY`、`PAGES_BASE_URL`(指向自定义域名)、`ANYSEARCH_API_KEY`(可选)、`STATS_READ_TOKEN`(可选)、`CF_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`、`MP_APPID`、`MP_UPLOAD_PRIVATE_KEY`。

## 关键约定与陷阱

- **搜索词调优有实测依据**:`CATEGORIES` 里每个搜索词都附注释说明为什么这样措辞(如时政国外用英文、地域词+「本地宝」引流、纯 `site:` 会顶出栏目页等)。改动搜索词前先读注释,别按直觉换词。**Phase A 验证期(2026-11-27 起 3~4 周)不动 CATEGORIES**——让 tab/click 埋点数据说话,达标与否见 `docs/superpowers/specs/2026-11-26-phase-a-growth-design.md` 第 7 节。
- **埋点无个人身份信息**:事件仅 date/vid(前端随机 hex)/type/cat/region/item;Worker 端 date 按 **UTC+8 北京日界**(与 `beijing_date()`/CI 04:02 拉昨日口径一致,勿改 UTC);埋点 JS 与统计拉取的所有故障都必须静默降级,不得影响日报主流程。
- **本地跑看不到 gnews 是正常的**:Google News RSS 本地被墙,`fetch_rss` 失败返回 `[]` 自动回退 anysearch;Actions(美国 IP)才可用。注意本地首跑会较慢:多数 gnews 侧全部失败,逐个触发「去日期」anysearch 兜底搜(每次前 sleep `SEARCH_INTERVAL`),且该兜底结果不进 raw 缓存;成品缓存写好后同日重跑仍零网络。
- **gnews 条目链接是跳转链**:`news.google.com/rss/articles/...` 在国内无代理时点不开,但标题/摘要/日期/来源(`<source>` 标签给的发布站名)不受影响;别按「链接打不开」误判为坏数据去过滤它们。
- **空结果保护**:全部抓取为空时 `main()` 直接报错退出,不覆盖上一次的页面/缓存(疑似限流/额度用尽)。
- **不泄露密钥**:`push_serverchan` 特意不打完整 URL(含 SendKey),异常只打印 `type(e).__name__`;保持这一约定。
- **微信推送必须瘦身**:Server酱 `desp` 字段有上限(实测 ≈64KB,超限报 HTTP 400 code=30001),`build_wechat_msg` 只放每类每地域前 `WECHAT_PREVIEW_N=3` 条,其余靠页面链接兜底——别把全量条目塞回消息。
- **Windows 控制台**:脚本开头把 stdout/stderr 重配为 UTF-8(emoji/中文),新脚本输出中文时同样处理。
- **原子写**:所有落盘走 `_write_text_atomic`(先写 .tmp 再 `os.replace`),避免半截 HTML。
- `public/`、`.cache/`、`*.docx` 均不入库(见 .gitignore);根目录下 `_probe_*.py` 等下划线开头文件是临时探测脚本。
- **埋点统计 Worker 不进每日 CI**:`worker/` 目录(wrangler.toml 注释里有完整部署步骤:D1 建库 → schema.sql → secret → deploy → 绑 zone 级 route 到 `/beacon*` `/stats*` `/summary*`,route 优先于 Pages custom domain),改版才手动重新部署。
- 页面每天覆盖同一 URL(`index.html`),不留移动版历史;PC 版按日期文件名归档。
