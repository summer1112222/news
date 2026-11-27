# Phase A 增长(熟人圈验证 + 反馈闭环)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每日新闻摘要建「国内稳定可达的分享链接 + 匿名点击埋点 + 昨日数据回流展示」,验证熟人圈真实留存。

**Architecture:** 新增 Cloudflare Worker + D1(免费档)做计数 API(POST /beacon、GET /stats);`build_digest.py` 渲染时注入原生 JS 埋点并拉取昨日统计展示;CI 增加 Cloudflare Pages 部署 step,GitHub Pages 原样保留为后备。

**Tech Stack:** Python(仅现有依赖 requests,零新增)/ 原生 JS 埋点 / Cloudflare Worker + D1 + Pages(wrangler)/ GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-11-26-phase-a-growth-design.md`(计划从 spec 出发,执行者需同时读 spec)

## Global Constraints

- Python 端零新依赖,埋点 JS 必须原生(无框架、无外部 CDN)
- 埋点不得含个人身份信息:仅 `date/vid/type/cat/region/item`,vid 为前端随机 hex
- 埋点 JS 与统计拉取的任何故障不得影响主流水线(日报每天必须照常出):全 try/catch,统计超时 2 秒,失败静默
- 埋点注入与统计拉取都以 `PAGES_BASE_URL` 已设置为前提(本地不设 → 全部关闭)
- `STATS_READ_TOKEN` 是密钥:不打日志、不进错误信息(沿用 push_serverchan 的密钥约定)
- 落盘一律走 `_write_text_atomic`;新 Python 文件输出中文前重配 stdout 为 UTF-8
- Worker 代码入库 `worker/` 目录,一次性手动部署,不进每日 CI
- Worker 端 `date` 一律取服务器时间按 **UTC+8** 计算,不信任客户端传的日期

## Review Focus

1. **统计接口挂了/超时,日报照常出** — `requests.get` 抛错/超时 → stats=None,页面与微信消息无统计行,不崩 → Task 4 测试 `test_stats_fetch_failure_returns_none`
2. **本地无 PAGES_BASE_URL 时埋点与统计全关** — HTML 不含 beacon 脚本、不发网络请求 → Task 3 测试 `test_no_beacon_without_base_url`
3. **畸形/恶意 beacon 不入库** — type 非法、vid 超长、cat 注入 → validateEvent 返回 null → Task 1 测试
4. **token 错误拿不到统计数据** — GET /stats 无/错 token → 401 → Task 2(入口层,smoke 验证)
5. **埋点 JS 自身报错不影响页面交互** — 每个 sendBeacon 调用包在 try/catch;JS 语法可用 `node --check` 验证 → Task 3 验证步骤

---

### Task 1: Worker 纯逻辑(事件校验 + 聚合)

**Files:**
- Create: `worker/src/logic.js`
- Test: `worker/test/logic.test.js`

**Interfaces:**
- Produces: `validateEvent(obj) -> event | null`(event 归一化为 `{vid, type, cat, region, item}`,非法返回 null);`aggregateRows(rows) -> {views, unique_vids, by_cat, click_total, clicks}`(rows 为 `{vid,type,cat,region,item}` 的数组,clicks 为 `[{cat,item,n}]` 按 n 降序)

- [ ] **Step 1: 写失败测试** `worker/test/logic.test.js`,用 node 内置 `node:test`:

```js
test('view 事件通过并归一化', () => { assert.deepEqual(validateEvent({vid:'a1b2c3d4', type:'view'}), {vid:'a1b2c3d4', type:'view', cat:'', region:'', item:null}); });
test('type 非法拒绝', () => { assert.equal(validateEvent({vid:'a1b2c3d4', type:'x'}), null); });
test('vid 非 hex / 超 32 字符拒绝', () => { assert.equal(validateEvent({vid:'hello world', type:'view'}), null); assert.equal(validateEvent({vid:'a'.repeat(33), type:'view'}), null); });
test('cat 超长拒绝(防注入)', () => { assert.equal(validateEvent({vid:'a1b2c3d4', type:'tab', cat:'x'.repeat(41)}), null); });
test('item 仅 click 时可为 0-99 整数,其他 type 忽略', () => { /* 断言 normalize 行为 */ });
test('click 事件带 region 通过(tab/click 都允许 region)', () => { assert.equal(validateEvent({vid:'a1b2c3d4', type:'click', cat:'时政', region:'国内', item:2}).region, '国内'); });
test('聚合:unique_vids 去重、by_cat 计 tab、clicks 降序', () => { /* 构造 3 vid、多事件断言全字段 */ });
test('聚合:空数组返回全零', () => { assert.deepEqual(aggregateRows([]), {views:0, unique_vids:0, by_cat:{}, click_total:0, clicks:[]}); });
```

- [ ] **Step 2: 运行确认失败** `node --test worker/test/` → FAIL(logic.js 不存在)
- [ ] **Step 3: 实现 `worker/src/logic.js`** — CommonJS 导出;校验规则:type ∈ {view,tab,click},vid 为 1-32 位 hex(小写),cat/region 字符串长度 ≤ 40,item 为 0-99 整数且仅 click 保留
- [ ] **Step 4: 运行确认通过** `node --test worker/test/` → PASS
- [ ] **Step 5: Commit** `git add worker/ && git commit -m "feat: 埋点 Worker 事件校验与聚合纯逻辑"`

### Task 2: Worker 入口 + D1 schema + wrangler 配置

**Files:**
- Create: `worker/src/worker.js`
- Create: `worker/wrangler.toml`
- Create: `worker/schema.sql`

**Interfaces:**
- Consumes: Task 1 的 `validateEvent` / `aggregateRows`
- Produces(线上契约,Task 3/4 依赖):`POST {PAGES_BASE_URL}/beacon`,JSON body `{vid,type,cat,region?,item?}`,成功 204、非法 400;`GET {PAGES_BASE_URL}/stats?date=YYYY-MM-DD&token=XXX`,正确返回 `{"date":...,"views":...,"unique_vids":...,"by_cat":{...},"click_total":...,"clicks":[...]}`,token 缺失/错误 401,date 格式非法 400;`GET {PAGES_BASE_URL}/summary?days=N&token=XXX` 返回 `{"days":[{"date":...,"unique_vids":...}],"retention":[{"date":...,"next_day_retained":...}]}`(相邻两日 vid 交集数),token 鉴权同上,days 默认 7、clamp 1-30(此端点供验证期 PM 手动拉 Phase B 触发指标,不进主流水线)

- [ ] **Step 1: 写 `worker/schema.sql`** — 单表 `events(date TEXT NOT NULL, vid TEXT NOT NULL, type TEXT NOT NULL, cat TEXT, region TEXT, item INTEGER, ts INTEGER)` + 索引 `idx_date ON events(date)`
- [ ] **Step 2: 写 `worker/wrangler.toml`** — `main = "src/worker.js"`,`compatibility_date = "2026-11-01"`,占位 `[[d1_databases]]`(database_name/database_id 由用户部署时填),`vars` 不放任何密钥(token 走 secret:`wrangler secret put STATS_READ_TOKEN`)
- [ ] **Step 3: 写 `worker/src/worker.js`** — fetch handler 按 method+path 分流(`/beacon`、`/stats`、`/summary` 三个 route;线上由 zone 级 Worker route 挂到 `<DOMAIN>`,优先于 Pages custom domain);date = 服务器当前 **UTC+8** 的 YYYY-MM-DD(勿用 UTC——流水线与 CI 拉数都按北京日界);POST:JSON 解析(失败 400)→ validateEvent(null → 400)→ `INSERT INTO events` → 204;GET /stats:token 比对(env.STATS_READ_TOKEN,不等 → 401)→ date 正则 `^\d{4}-\d{2}-\d{2}$`(不匹配 400)→ `SELECT * WHERE date=?` → aggregateRows → JSON 200;GET /summary:token 同上 → days clamp 1-30 默认 7 → 按日聚合 `unique vid` + 相邻两日 vid 交集(交集在 JS 里对两组 vid Set 计算)→ JSON 200;其他路径 404
- [ ] **Step 4: 语法验证** `node --check worker/src/worker.js` → 无输出
- [ ] **Step 5: Commit** `git add worker/ && git commit -m "feat: 埋点 Worker 入口与 D1 schema"`
- [ ] **Step 6(用户手动,不阻塞后续 Task;部署步骤同步写入 Task 5 的 CLAUDE.md)** — `wrangler d1 execute --file schema.sql` → `wrangler deploy` → `wrangler secret put STATS_READ_TOKEN` → `curl -X POST https://<DOMAIN>/beacon -d '{"vid":"deadbeef","type":"view"}'`(期望 204)→ `curl "https://<DOMAIN>/stats?date=<今日>&token=<TOKEN>"`(期望 200 JSON)

### Task 3: build_digest.py 埋点 JS 注入 + 分享卡片标题

**Files:**
- Modify: `scripts/build_digest.py`(`_render_page` 附近新增 `_beacon_js()`,head 注入点在 1280-1297 的 head 字符串尾部;`<title>`/og:title 在 1285-1287)
- Test: `tests/test_beacon.py`

**Interfaces:**
- Consumes: env `PAGES_BASE_URL`
- Produces: `_beacon_js() -> str`(未设 PAGES_BASE_URL 返回 "");HTML head 尾部出现 `<script>` 埋点;标题/og:title 变为 `{today} · {n}类新闻精选 · {total}条`(PC 版附加 ` · PC版`)

- [ ] **Step 1: 写失败测试** `tests/test_beacon.py`(stdlib unittest,参照 `tests/test_build_mp_data.py`):

```python
def test_title_and_og(self):  # build_html 输出含 "2026-11-27 · 10类新闻精选 · " 与 og:title 同文案
def test_beacon_injected_with_base_url(self):  # monkeypatch os.environ 设 PAGES_BASE_URL → 输出含 "/beacon" 与 "sendBeacon" 与 "localStorage"
def test_no_beacon_without_base_url(self):  # 不设 → 输出不含 "sendBeacon"
def test_beacon_js_silent(self):  # 提取 script 文本:每个 sendBeacon 外层有 try{、"catch" 出现 ≥1 次、不含 "wx" / "name" 等 PII 字段
```

测试构造 categories_data 的最小 fixture(一个分类、每地域 1 条,字段 title/url/snippet)。

- [ ] **Step 2: 运行确认失败** `python -m unittest tests.test_beacon -v` → FAIL
- [ ] **Step 3: 实现** — `_beacon_js()`:env 无 PAGES_BASE_URL 返回 "";有则返回原生 JS:beacon 目标为**绝对 URL** `PAGES_BASE_URL 去尾斜杠 + '/beacon'`(github.io 后备入口的访问同样上报,视作真实读者);vid 取 localStorage 无则 `crypto.randomUUID().replace(/-/g,'')` 截 16 位;监听 `click` 事件(事件委托在列表容器,取 data 属性中的分类/序号/**当前 region**——click 必须带 region,cat+item 分不清国内外侧);tab 切挂到现有 `showTab`/`showRegion` 调用处(在现有脚本尾部包一层 wrapper,不改原函数体);每次上报 `navigator.sendBeacon(beaconUrl, JSON.stringify(...))` 全部包 try/catch;标题行改为 f-string 拼接 `len(categories_data)` 与 `_total_items(categories_data)`
- [ ] **Step 4: 运行确认通过** `python -m unittest tests.test_beacon -v` → PASS
- [ ] **Step 5: 提取 JS 语法验证** — 测试里把 script 文本写入临时文件后可 `node --check`;至少人工确认无语法错(本地无 node 时跳过,CI 有 node)
- [ ] **Step 6: Commit** `git add scripts/build_digest.py tests/test_beacon.py && git commit -m "feat: 页面埋点注入与分享卡片标题"`

### Task 4: 昨日统计拉取 + 页面/微信消息展示

**Files:**
- Modify: `scripts/build_digest.py`(新增 `_fetch_yesterday_stats`;`build_html`/`build_html_pc`/`build_wechat_msg` 加 `stats=None` 参数;`main()` 渲染前拉一次;header 展示点在 `_render_page` 的 header 段 1299 起)
- Modify: `scripts/gen_pc_from_html.py`(调用处传默认 stats=None 即可,不改行为)
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: Task 2 线上契约 `GET {PAGES_BASE_URL}/stats?date=<昨日>&token=...`;env `PAGES_BASE_URL`、`STATS_READ_TOKEN`
- Produces: `_fetch_yesterday_stats(today: str) -> dict | None`(昨日 = beijing today 减 1 天;PAGES_BASE_URL 或 STATS_READ_TOKEN 未设 → None;requests.get 超时=2、任何异常 → None 且 stderr 打一行「[统计] 拉取失败(type 名)」不打 token);展示文案:页面 header「昨日 N 人阅读 · 最热:某分类」(stats 为 None 或 unique_vids==0 时不显示);微信消息 meta 行(1364)追加「 · 昨日N人阅读」(同样条件)

- [ ] **Step 1: 写失败测试** `tests/test_stats.py`:

```python
def test_fetch_no_env_returns_none(self):        # 两个 env 均不设
def test_fetch_failure_returns_none(self):       # monkeypatch requests.get 抛 Timeout → None
def test_fetch_ok(self):                         # monkeypatch 返回 {"unique_vids":8,...} → dict 透传
def test_header_shows_stats(self):               # build_html(stats={...}) 输出含 "昨日 8 人阅读"
def test_header_hides_when_zero_or_none(self):   # stats=None / unique_vids=0 → 不含 "昨日"
def test_wechat_msg_stats_line(self):            # build_wechat_msg(stats={...}) meta 行含 "昨日8人阅读";stats=None → 不含
def test_pc_and_index_consistent(self):          # 两版 build 都接受 stats 参数不报错
```

- [ ] **Step 2: 运行确认失败** `python -m unittest tests.test_stats -v` → FAIL
- [ ] **Step 3: 实现** — `stats` 默认 None 保证 gen_pc_from_html.py 等旧调用零改动兼容;昨日日期用 `datetime` 直接从 today 字符串解析减 1 天,不依赖时区函数
- [ ] **Step 4: 运行确认通过** `python -m unittest tests.test_stats tests.test_beacon -v` → 全 PASS
- [ ] **Step 5: Commit** `git add scripts/build_digest.py scripts/gen_pc_from_html.py tests/test_stats.py && git commit -m "feat: 昨日阅读统计回流展示(页面+微信)"`

### Task 5: CI 部署 Cloudflare Pages + 文档更新

**Files:**
- Modify: `.github/workflows/daily-push.yml`(在「部署到 Pages」之后、小程序上传之前加一步)
- Modify: `CLAUDE.md`(环境变量表 + 数据流 + 用户部署清单)

**Interfaces:**
- Consumes: secrets `CF_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`、`STATS_READ_TOKEN`(注入 env);`PAGES_BASE_URL` secret 值由用户改为 `https://<DOMAIN>`

- [ ] **Step 1: workflow 加 step** — `uses: actions/setup-node@v4` 已有(小程序步骤在用,顺序在后的步骤可复用前置 setup?不能——setup-node 在其后;把 CF 部署 step 放到现有 setup-node 步骤之后或自带一个);`run: npx wrangler pages deploy ./public --project-name news-digest`(env 注入 CF_API_TOKEN/CLOUDFLARE_ACCOUNT_ID),`continue-on-error: true`(CF 部署失败不阻塞微信推送与小程序,GitHub Pages 仍是兜底);构建 step 的 env 增加 `STATS_READ_TOKEN: ${{ secrets.STATS_READ_TOKEN }}`
- [ ] **Step 2: YAML 校验** `python -c "import yaml,sys;yaml.safe_load(open('.github/workflows/daily-push.yml',encoding='utf-8'))"` → 无异常
- [ ] **Step 3: CLAUDE.md 更新** — 环境变量表加 `STATS_READ_TOKEN`(拉昨日统计,空则跳过);架构数据流加第 7 步「埋点与回流」;「关键约定」加:埋点无 PII、Worker 手动部署(`worker/` 目录 + 部署命令)、CF 部署失败 continue-on-error、验证期不动 CATEGORIES
- [ ] **Step 4: Commit** `git add .github/workflows/daily-push.yml CLAUDE.md && git commit -m "feat: CI 部署 Cloudflare Pages 与统计环境注入"`

### Task 6: 端到端联调(用户配合)

**Files:** 无新文件(验证任务)

- [ ] **Step 1: 本地全量回归** `python -m unittest discover tests -v` → 全 PASS;`python scripts/build_digest.py`(无 env)→ 页面正常、无埋点无统计行
- [ ] **Step 2: 用户完成部署清单**(spec 第 6 节:域名/DNS/Pages 项目/D1/Worker/Secrets,Worker 部署按 Task 2 Step 6 命令)
- [ ] **Step 3: 手动触发 CI** `gh workflow run "每日新闻推送" --ref main` → 全绿;微信消息点开链接为新域名;页面正常、含埋点脚本与(次日跑起后)统计行
- [ ] **Step 4: 埋点冒烟** — 手机打开页面 → `curl "https://<DOMAIN>/stats?date=<今日>&token=<TOKEN>"` 出现 view 事件;切 tab/点条目后再查,tab/click 计数增长;`curl "https://<DOMAIN>/summary?days=7&token=<TOKEN>"` 返回逐日 unique_vids(验证期 PM 每周拉此端点跟踪北极星指标)
- [ ] **Step 5: 转发启动** — 把当日微信消息转发家人群/朋友群,开始 3~4 周验证期
