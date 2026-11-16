# 每日新闻摘要 · 微信推送 + GitHub Pages 页面

每天 01:02 自动抓取 9 类新闻,生成一个带分类 tab 的网页部署到 GitHub Pages,再推一条带链接的瘦身消息到微信。点微信里的链接进网页,顶部 9 个分类标签切换,每条标题点开看原文。

## 抓取分类
时政 · 教育 · 全球科技 · 财经 · 全球医疗 · 江浙沪皖 · 上海发展 · 南通发展 · 培养孩子(每类 5 条)

## 架构

```
GitHub Actions (cron 01:02 北京)
   → scripts/build_digest.py
      → scripts/anysearch_search.py   (anysearch JSON-RPC 抓取)
      → 解析成 标题/网址/摘要
      → 生成 public/index.html         (9 分类 tab 页,标题做超链接)
      → 部署到 GitHub Pages            (每天覆盖同一 URL)
      → Server酱 API                   (推瘦身消息:链接 + 每类每地域预览3条到微信)
```

微信里收到的是一句话 + 链接 + 今日提要;点链接打开网页看全部 9 类。

## 一次性配置(约 10 分钟)

### 1. 拿到 Server酱 SendKey
1. 打开 https://sct.ftqq.com ,微信扫码登录
2. 点击「发送消息」→ 绑定微信,关注它的企业微信应用
3. 在 https://sct.ftqq.com/sendkey 复制你的 **SendKey**

### 2. 创建 GitHub 仓库并推送代码
```bash
cd D:\claude-info\news
git init
git add .
git commit -m "init: 每日新闻推送 + Pages"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 3. 开启 GitHub Pages
仓库 → Settings → Pages → Build and deployment → **Source 选 GitHub Actions**。
(不用选 branch,workflow 会自己部署。)

### 4. 配置 Secrets
仓库 → Settings → Secrets and variables → Actions → New repository secret:
| Name | Value |
|------|-------|
| `SERVERCHAN_SENDKEY` | 第 1 步复制的 SendKey |
| `PAGES_BASE_URL` | 你的 Pages 地址,如 `https://<用户名>.github.io/<仓库名>` |
| `ANYSEARCH_API_KEY` | (可选)提高 anysearch 速率;不填走匿名访问 |

> `PAGES_BASE_URL` 的值就是开启 Pages 后那个固定 URL,末尾不要带 `/`。第一次手动触发跑完即可看到实际地址,填进去就行。

### 5. 手动触发首次测试
仓库 → Actions → 选「每日新闻推送」→ Run workflow。
约 1-2 分钟后:微信收到一条带链接的消息,点开就是当天的 tab 页。

### 6. 等待自动触发
cron 设为 `2 17 * * *`(UTC 17:02 = 北京次日 01:02)。此后每天凌晨 1 点自动跑。
> GitHub cron 有几分钟抖动且不保证精确;若需严格 06:00,改用 VPS cron。

## 本地测试(不依赖 GitHub)

```powershell
$env:SERVERCHAN_SENDKEY = "你的sendkey"
$env:PAGES_BASE_URL = "http://localhost:8000"   # 或你的线上地址;不设则只生成不推送
pip install -r requirements.txt
python scripts/build_digest.py
```

生成 `public/index.html`,本地预览:
```powershell
python -m http.server -d public
# 浏览器打开 http://localhost:8000
```
设了 `PAGES_BASE_URL` 才会推微信;不设只生成 HTML。

### 当天缓存(改样式时免重抓)
首次跑完会把原始结果与成品分别写到 `.cache/raw-<日期>.json` 和 `.cache/final-<日期>.json`:

- 同一天再次运行会**直接复用成品**,几秒内重渲染,不再请求 anysearch;
- 想强制重新抓取最新新闻:`$env:REFRESH='1'`;
- 只改了过滤/排序等后端逻辑、想在不联网的情况下重滤缓存数据时,可删掉 `final-<日期>.json` 保留 `raw-<日期>.json` 后再跑。

## 关键文件
| 文件 | 作用 |
|------|------|
| `scripts/anysearch_search.py` | 精简 anysearch 客户端(JSON-RPC → Markdown) |
| `scripts/build_digest.py` | 抓取 → 生成 tab 页 HTML → 推送 的主编排脚本 |
| `.github/workflows/daily-push.yml` | GitHub Actions 定时任务 + Pages 部署 |
| `.cache/`(本地) | 当天原始/成品缓存,`REFRESH=1` 强制重抓 |

## 调整
- **改分类/搜索词**:编辑 `scripts/build_digest.py` 的 `CATEGORIES` 列表
- **改每类条数**:改 `MAX_RESULTS` 环境变量(1-10)
- **改推送时间**:改 `.github/workflows/daily-push.yml` 的 `cron`(UTC!)
- **改页面样式**:编辑 `scripts/build_digest.py` 里的 `_CSS`

## 注意事项
- 页面每天覆盖同一 URL,不留历史。几天后点微信里旧消息的链接,看到的是当天内容而非那天的。需要每天永久留一份可改 `build_html` 输出按日期命名的页面。
- Server酱免费版每天 5 条;本任务 1 条/天,够用。需更多用 ServerChan Turbo 付费版。
- anysearch 匿名访问有速率限制,建议配 `ANYSEARCH_API_KEY`。
- 政府类站点正文常反爬;本流水线用 anysearch **搜索摘要**而非单页正文,稳定性更高。
