# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

每日新闻摘要流水线：抓取 9 类新闻（每类分国内/国外），生成带分类 tab 的移动版 `public/index.html` + 按日归档的 PC 宽屏版 `public/digest-<日期>.html`，部署到 GitHub Pages，并经 Server酱 推一条瘦身消息到微信。纯 Python，唯一依赖 `requests`，无测试框架。

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
| `MAX_RESULTS` / `TOP_N` | 每类每地域抓取条数 / 最终展示条数 | 10 / 10 |
| `MAX_DAYS` | 旧闻 cutoff 天数(CI 里放宽到 30) | 7 |
| `SEARCH_INTERVAL` | 搜索间隔秒数,防限流 | 1 |
| `REFRESH` | `1` 忽略当天缓存强制重抓 | 空 |

## 架构(读多个文件才能看懂的部分)

### 数据流(build_digest.py 为主编排)

1. **取数双层**:每分类每地域(国内 dom / 国外 for)优先走 `RSS_FEEDS` 直抓(中国新闻网官方 RSS = 国内侧主源;Google News RSS 搜索 feed = 国外侧主源),全部失败/为空才降级 anysearch 搜索(`_expand_queries` 跳过有 RSS 源的那一侧,9 类双侧全覆盖时零 anysearch 请求)。anysearch 客户端在 `scripts/anysearch_search.py`(JSON-RPC,`search_many` 按 5 条/批切分并按 query 文本定位段落,单条失败逐条降级)。
2. **过滤**:`clean_items` —— 大量启发式正则(垃圾域名、栏目/专题/导航页、通用标题、活动/营销页、英文混入等),跨国内/国外共享 `seen` 去重。改这里要先读 `_JUNK_HOSTS` 到 `_GENERIC_PREFIX_RE` 一带的注释。
3. **动态时间窗**:`_filter_tiered` —— 先近 2 天,不足逐级放宽 7 天 → `MAX_DAYS`,仍不足 `_topup` 去日期补搜一轮。优先展示最新。
4. **排序截断**:`sort_items_recent` 按发布日期降序 → `TOP_N` 截断。
5. **渲染**:`build_html`(移动 tab 页)/ `build_html_pc`(宽屏);`gen_pc_from_html.py` 反向解析 index.html 复用 `clean_items`/`sort_items_recent`/`build_html_pc` 离线重生成 PC 版。
6. **推送**:`build_wechat_msg` + `push_serverchan`(仅设 `PAGES_BASE_URL` 时)。

### 缓存三层(`.cache/`,均按当天日期命名)

成品 `final-<日期>.json`(命中则零网络)→ 原始 `raw-<日期>.json`(命中则免抓取但重过滤)→ 都没有才真抓。`REFRESH=1` 全部无视。

### CI(.github/workflows/daily-push.yml)

cron UTC 22:02(北京 06:02),装依赖 → 跑 build_digest.py(注入 secrets 与 MAX_RESULTS=10/MAX_DAYS=30/TOP_N=10/SEARCH_INTERVAL=3)→ 部署 `./public` 到 GitHub Pages。Secrets:`SERVERCHAN_SENDKEY`、`PAGES_BASE_URL`、`ANYSEARCH_API_KEY`(可选)。

## 关键约定与陷阱

- **搜索词调优有实测依据**:`CATEGORIES` 里每个搜索词都附注释说明为什么这样措辞(如时政国外用英文、地域词+「本地宝」引流、纯 `site:` 会顶出栏目页等)。改动搜索词前先读注释,别按直觉换词。
- **本地跑看不到 gnews 是正常的**:Google News RSS 本地被墙,`fetch_rss` 失败返回 `[]` 自动回退 anysearch;Actions(美国 IP)才可用。
- **空结果保护**:全部抓取为空时 `main()` 直接报错退出,不覆盖上一次的页面/缓存(疑似限流/额度用尽)。
- **不泄露密钥**:`push_serverchan` 特意不打完整 URL(含 SendKey),异常只打印 `type(e).__name__`;保持这一约定。
- **微信推送必须瘦身**:Server酱 `desp` 字段有上限(实测 ≈64KB,超限报 HTTP 400 code=30001),`build_wechat_msg` 只放每类每地域前 `WECHAT_PREVIEW_N=3` 条,其余靠页面链接兜底——别把全量条目塞回消息。
- **Windows 控制台**:脚本开头把 stdout/stderr 重配为 UTF-8(emoji/中文),新脚本输出中文时同样处理。
- **原子写**:所有落盘走 `_write_text_atomic`(先写 .tmp 再 `os.replace`),避免半截 HTML。
- `public/`、`.cache/`、`*.docx` 均不入库(见 .gitignore);根目录下 `_probe_*.py` 等下划线开头文件是临时探测脚本。
- 页面每天覆盖同一 URL(`index.html`),不留移动版历史;PC 版按日期文件名归档。
