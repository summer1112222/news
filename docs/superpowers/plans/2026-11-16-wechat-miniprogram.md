# 微信小程序「每日新闻摘要」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有每日新闻流水线新增一个原生微信小程序(个人主体体验版),CI 每日自动把当天数据打包进小程序并经 miniprogram-ci 上传,家人扫码即读。

**Architecture:** 零网络请求——数据在 CI 阶段静态打进代码包(当天进主包、滚动历史进分包);新增两个脚本(`build_mp_data.py` 数据打包、`mp_upload.mjs` 上传)接到现有 `daily-push.yml` 后段;小程序端 2 页 + 1 组件,视觉沿用 H5 报纸衬线风。

**Tech Stack:** 原生小程序(WXML/WXSS/JS,无跨端框架)、Python 3(stdlib unittest 测试)、Node 20 + miniprogram-ci(唯一 npm 依赖)。

**Spec:** `docs/superpowers/specs/2026-11-16-wechat-miniprogram-design.md`

## Global Constraints

- Python 侧唯一运行依赖仍是 `requests`(build_mp_data.py 只用 stdlib + 复用 build_digest 函数)
- 测试用 stdlib `unittest`,不引入 pytest(项目无测试框架,保持零新增依赖)
- 所有落盘走 `build_digest._write_text_atomic`(先 .tmp 再 os.replace)
- 输出中文/emoji 的脚本,开头照 `build_digest.py` 惯例重配 stdout/stderr 为 UTF-8
- 不打印密钥:MP 私钥/SendKey 一样只打长度或类型名,错误消息不得回显 key 内容(延续 `push_serverchan` 约定)
- 数据 JS 文件一律 `json.dumps` 序列化生成,禁止字符串拼接(gnews URL 含引号/反斜杠会拼出非法 JS)
- 空结果保护:当天 `final-<日期>.json` 缺失或空时 `build_mp_data.py` 直接 SystemExit,不覆盖已有数据文件
- `node_modules/` 不入库;.gitignore 增补;`package-lock.json` 入库(CI 用 `npm ci`)
- 历史分包体积预算 1.7MB(2MB 上限留余量),保底 14 天,自适应裁剪
- 每个任务结束 commit 一次;不 push(用户统一 push)

## Review Focus

1. **胖数据日超预算**:历史 108KB+/天的日子,分包必须裁窗而不是写爆 —— Task 1 `test_adaptive_window_trims_when_over_budget`
2. **当天 final cache 缺失/为空**:必须报错退出不覆盖旧数据(同 build_digest 空结果保护)—— Task 1 `test_main_exits_when_final_cache_missing`
3. **历史 JSON 损坏/字段漂移**:滚动文件里某天结构不对要跳过该天,不能整包崩 —— Task 1 `test_load_history_skips_malformed_days`
4. **URL/标题含引号反斜杠等特殊字符**:生成的 .js 必须可被 `require` 且字段无损(gnews 跳转链常见)—— Task 1 `test_to_js_roundtrip_special_chars` + 各任务验证步骤里的 `node -e require`
5. **某分类某地域条目为空**:页面显示「此类暂无更新」空态,不渲染空白区块 —— Task 2 wxml 固定含空态块(开发者工具人工验证)

---

### Task 1: 数据打包脚本 build_mp_data.py

**Files:**
- Create: `scripts/build_mp_data.py`
- Create: `tests/test_build_mp_data.py`
- Test: `python -m unittest discover -s tests -v`(仓库根目录运行)

**Interfaces:**
- Consumes: `build_digest.py` 的 `short_cat(title) -> str`、`pub_date(it, today) -> str`、`_clean_snippet(text, title, limit) -> str`、`_clip(text, n) -> str`、`_write_text_atomic(path, text)`;`.cache/final-<日期>.json` 的 `{categories: [{category, domestic, foreign}]}` 结构
- Produces(Task 2/3/4/6 依赖):
  - `miniprogram/data/today.js`:`module.exports = {date, generatedAt, categories: [{title, tab, domestic: [{t,s,d,u,n}], foreign: [...]}]}`
  - `miniprogram/data/index.js`:`module.exports = [{date: "YYYY-MM-DD", total: 172}, ...]`(仅分包实际包含的日期,新→旧)
  - `miniprogram/packageHistory/data/history.js`:`module.exports = {"2026-11-15": {date, generatedAt, categories: [...]}, ...}`(不含当天)
  - Python API:`build_pack(today, final_cats, prev_history, budget_bytes=1_700_000, min_days=14) -> dict`(键:today_js/index_js/history_js/kept_days);`load_history(path) -> dict`;`compact_item(it, today) -> dict`;`to_js(obj) -> str`;`main(cache_dir=".cache", mp_dir="miniprogram")`(目录参数供测试注入,默认真实路径)

- [ ] **Step 1: 写失败测试** `tests/test_build_mp_data.py`

```python
import sys, os, json, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_mp_data as b

def fake_item(title="测试标题", url="https://example.com/a?x=1", snippet="正文内容。", date="2026-11-16"):
    return {"title": title, "url": url, "source": "example.com", "snippet": snippet, "date": date}

def fake_cats():
    return [{"category": "👨‍👩‍👧 育儿新闻", "domestic": [fake_item()], "foreign": []}]
```
用例(逐个方法):
- `test_compact_item_maps_fields`:断言 t/s/d/u/n 五字段;d == "11-16";n 是清洗后的摘要
- `test_tab_short_name`:build_pack 输出里 tab == "育儿"
- `test_to_js_roundtrip_special_chars`:条目 title 含 `"'\\</script>`,`json.loads(to_js(x)[len("module.exports = "):-1])` 与原对象相等
- `test_today_excluded_from_history_and_index_newest_first`:prev_history 含 2 天 + 当天,断言 history_js 无当天键、index_js 首位是当天前一天、total 为该日条数和
- `test_adaptive_window_trims_when_over_budget`:budget 设小(如 2000 字节)+ 每天 5 条长摘要,断言 kept 天数减少、最新一天保留、最旧被裁;`test_min_days_guarantee`:min_days=3 时保底 3 天(允许超预算)
- `test_load_history_skips_malformed_days`:history.js 里混入缺 categories 的日期,断言该日被跳过、其余保留
- `test_main_exits_when_final_cache_missing`:tempfile 目录做 CACHE/输出根,无 final cache 时 `assertRaises(SystemExit)`

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL,ModuleNotFoundError/AttributeError(build_mp_data 不存在)

- [ ] **Step 3: 实现 `scripts/build_mp_data.py`**

- 开头 UTF-8 重配 + `sys.path.insert(0, dirname(__file__))`,`from build_digest import short_cat, pub_date, _clean_snippet, _clip, _write_text_atomic, _load_final_cache`
- `compact_item`:`d = pub_date(it, today)[5:]`(无日期空串);`n = _clean_snippet(it.get("snippet",""), it.get("title",""))`;`t = it.get("title","").strip()`
- `to_js(obj)`:`"module.exports = " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";"`
- `build_pack`:当天 → today_js;prev_history(去当天、按日期降序)逐日累加,每装一天先算该天 JSON 增量字节,超 budget 停,但不足 min_days 继续装到 min_days;输出 index_js 与 history_js;kept_days 降序列表
- `load_history(path)`:读 .js,剥 `module.exports = ` 前缀 `json.loads`;单日结构异常(非 dict/缺 categories/categories 非列表)整日跳过,stderr 提示
- `main(cache_dir, mp_dir)`:`final-{today}.json` 用 `json.load` 直读(缺失/为空/结构异常 → SystemExit,提示先跑 build_digest.py,不覆盖任何输出);读现有 `{mp_dir}/packageHistory/data/history.js`(不存在视作 `{}`);三个目标文件经 `_write_text_atomic` 写出;stderr 打印 `[MP数据] 当天N条 历史{len(kept)}天 分包{K}KB`
- 命令行无参数(读环境变量能力都不要,保持零配置)

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest discover -s tests -v`
Expected: 全部 PASS

- [ ] **Step 5: 用真实数据验证 + commit**

Run: `python scripts/build_mp_data.py`(当天 cache 已存在)
Expected: stderr 汇总行;`node -e "const d=require('./miniprogram/data/today.js');if(!d.date||!d.categories.length)process.exit(1)"` 退出码 0

```bash
git add scripts/build_mp_data.py tests/ miniprogram/data/today.js miniprogram/data/index.js miniprogram/packageHistory/data/history.js
git commit -m "feat: 小程序数据打包脚本(当天主包+滚动历史分包,自适应体积)"
```

### Task 2: 小程序主包骨架(首页)

**Files:**
- Create: `miniprogram/app.js`、`miniprogram/app.json`、`miniprogram/app.wxss`、`miniprogram/theme.json`、`miniprogram/sitemap.json`、`miniprogram/project.config.json`
- Create: `miniprogram/components/news-list/`(js/json/wxml/wxss 四件)
- Create: `miniprogram/pages/index/`(js/json/wxml/wxss 四件)

**Interfaces:**
- Consumes: Task 1 的 `miniprogram/data/today.js` 结构
- Produces: 页面路径 `pages/index/index`(Task 3 的 app.json、Task 6 依赖);组件 `news-list`,properties `{items: Array}`(元素 {t,s,d,u,n}),内部 tap 复制 `u` 到剪贴板(Task 3 复用)

- [ ] **Step 1: 全局配置文件**

`app.json`:pages 仅 `["pages/index/index"]`;`darkmode: true` + `themeLocation: "theme.json"`;`lazyCodeLoading: "requiredComponents"`;window 用 `@navBgColor/@navTxtStyle` 变量。`theme.json`:light `#F9F7F2`/black,dark `#16140F`/white。`project.config.json`:`appid: "touristappid"`(Task 4 上传时用 ci.Project 的 appid 参数覆盖,此文件仅服务开发者工具预览)、`compileType: "miniprogram"`。`app.js`:`App({})`。

- [ ] **Step 2: news-list 组件**

wxml:`<article class="news">` 内标题(`t`)、`{{s}}{{s && d ? ' · ' : ''}}{{d}}` 元信息行、摘要(`n`)三层;**必须含空态**:`wx:if="{{!items.length}}"` 显示「此类暂无更新。」(Review Focus #5)。tap 事件 → `wx.setClipboardData({data: u})` 成功后 `wx.showToast({title: '链接已复制', icon: 'none'})`;无 `u` 时 toast「无原文链接」。样式移植 H5 `_CSS`:serif 字体栈(`"Songti SC","SimSun",serif`)、暗红 accent、发丝线分隔;色值用 CSS 变量写在组件 wxss,`@media (prefers-color-scheme: dark)` 换暗色组(light `#8B2A2A` / dark `#C75656`)。

- [ ] **Step 3: pages/index 页面**

js:`const data = require('../../data/today.js')`;data 结构 `{catIdx, region}`(region: `'domestic'/'foreign'`),方法切分类/切地域;`onShareAppMessage` 返回 `{title: '每日新闻摘要 ' + data.date, path: '/pages/index/index'}`。wxml:刊头(标题+`date`+国内N条·国外N条,取自 data.categories 求和)→ 国内/国外两枚 segmented → 横向 `scroll-view scroll-x` 分类 tab(短名 `tab`,scroll-into-view 联动当前分类)→ news-list(`items = categories[catIdx][region]`)。页脚「每日凌晨自动更新 · 体验版」。

- [ ] **Step 4: 本地验证**

Run: `node -e "const d=require('./miniprogram/data/today.js');console.log(d.categories.length + ' 类')"`
Expected: 输出 `9 类`
用户在微信开发者工具导入 `miniprogram/` 目录(touristappid 即可预览):确认 tab 切换、地域切换、点条目出复制 toast、空分类显示空态。

- [ ] **Step 5: Commit**

```bash
git add miniprogram/
git commit -m "feat: 小程序主包骨架(首页9分类tab+国内外切换+复制链接)"
```

### Task 3: 历史分包页

**Files:**
- Create: `miniprogram/packageHistory/pages/history/`(js/json/wxml/wxss 四件)
- Modify: `miniprogram/app.json`(加 subPackages)

**Interfaces:**
- Consumes: Task 1 的 `miniprogram/data/index.js`(日期列表)、`miniprogram/packageHistory/data/history.js`(按日数据);Task 2 的 `components/news-list`(主包组件,分包经 `../../../components/news-list/news-list` 引用)
- Produces: 完整可用的小程序(首页 + 历史);页面路径 `packageHistory/pages/history/history`(Task 6 后 CI 产物随包上传)

- [ ] **Step 1: app.json 注册分包**

`subPackages: [{root: "packageHistory", pages: ["pages/history/history"]}]`;首页 wxml 加入口(刊头日期区旁「历史」链接,`wx.navigateTo` url `/packageHistory/pages/history/history`)。

- [ ] **Step 2: history 页面**

js:`require('../../../data/index.js')` 得日期列表;`require('../../data/history.js')` 得按日数据(页面加载时才 require,数据文件随分包下载);选中日期后取 `history[date].categories` 复用与首页相同的分类/地域切换状态渲染 news-list。wxml:日期列表(`{{date}} · 共{{total}}条`,新→旧)→ 选中后同首页布局。空态:某日数据缺失(index 有而 history 无)显示「该日数据不完整」。

- [ ] **Step 3: 本地验证**

Run: `node -e "const h=require('./miniprogram/packageHistory/data/history.js');const i=require('./miniprogram/data/index.js');console.log(Object.keys(h).length, i.length)"`
Expected: 两个数字一致(index 列出的日期都在 history 里)
开发者工具:从首页点「历史」进入分包页(工具会模拟分包加载),选一天能正常浏览。

- [ ] **Step 4: Commit**

```bash
git add miniprogram/
git commit -m "feat: 小程序历史分包页(近30天按日浏览)"
```

### Task 4: 上传脚本 mp_upload.mjs + npm 依赖

**Files:**
- Create: `package.json`、`scripts/mp_upload.mjs`
- Modify: `.gitignore`(加 `node_modules/`)

**Interfaces:**
- Consumes: Task 1/2/3 产出的 `miniprogram/` 目录
- Produces: CLI `node scripts/mp_upload.mjs`,环境变量 `MP_APPID`(必填)、`MP_PRIVATE_KEY`(必填,密钥文件全文,多行);Task 6 的 workflow 直接以这两个 env 调用;成功上传一个开发版本(版本号=当天日期)

- [ ] **Step 1: 初始化 npm 并装依赖**

```bash
npm init -y > /dev/null; npm install --save-dev miniprogram-ci
```
`package.json` 精简为 `{private: true, devDependencies: {miniprogram-ci: "<锁定安装出的精确版本>"}}`;确认生成了 `package-lock.json`。`.gitignore` 追加 `node_modules/`。

- [ ] **Step 2: 实现 `scripts/mp_upload.mjs`**

- `import ci from 'miniprogram-ci'`
- 启动即校验两个 env,缺失时 `process.exit(1)` 并打印 `❌ 未设置 MP_APPID/MP_PRIVATE_KEY`
- 密钥内容写临时文件:`os.tmpdir()` 下随机名,用后 `fs.unlinkSync`(try/catch 兜底)
- `new ci.Project({appid, type: 'miniProgram', projectPath: 'miniprogram', privateKeyPath, ignores: ['node_modules/**/*']})`
- `version = require('./miniprogram/data/today.js')`… 注意 mjs 里用 `createRequire`;version 取 `data.date`,desc 为 `日期 + HH:mm`
- `await ci.upload({project, version, desc, setting: {es6: true, minify: true}, robot: 1})`
- catch:打印 `type/message`,**message 不得含密钥内容**(打印前用密钥字符串 replace 成 `***`);exit 1

- [ ] **Step 3: 本地验证(两种失败路径)**

Run: `node scripts/mp_upload.mjs`(无 env)
Expected: 退出码 1,提示缺环境变量
Run: `$env:MP_APPID='wx'+'test'; $env:MP_PRIVATE_KEY='not-a-real-key'; node scripts/mp_upload.mjs`
Expected: 退出码 1,报鉴权/私钥格式类错误且不含 key 原文

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json scripts/mp_upload.mjs .gitignore
git commit -m "feat: miniprogram-ci 上传脚本(体验版机器人,密钥不落日志)"
```

### Task 5: Phase 0 实测——体验版是否随机器人上传自动更新(用户人工步骤)

**Files:**
- Modify: `docs/superpowers/specs/2026-11-16-wechat-miniprogram-design.md`(记录实测结论)

**Interfaces:**
- Consumes: Task 4 的上传脚本、可上传的完整小程序包
- Produces: 设计文档「风险」节的实测结论;Task 6 是否继续/如何调整的依据

此任务是**决策门**,全部步骤由用户在微信后台操作,代码侧无改动:

- [ ] **Step 1: 注册与配置(用户,约 20 分钟)**

1. mp.weixin.qq.com 注册**个人主体**小程序,记下 AppID
2. 「开发管理-开发设置-小程序代码上传」生成密钥文件(下载 `private.<appid>.key`)
3. 仓库 Settings → Secrets 添加 `MP_APPID`(AppID)与 `MP_UPLOAD_PRIVATE_KEY`(密钥文件全文)
4. 「成员管理-体验成员」加入家人微信号(≤ 15)

- [ ] **Step 2: 两次上传实验(用户)**

```powershell
$env:MP_APPID='真实AppID'; $env:MP_PRIVATE_KEY=(Get-Content private.<appid>.key -Raw); node scripts/mp_upload.mjs
```
后台「版本管理-开发版本」把首个版本**手动设为体验版**,家人扫码确认能打开;改一处可见文案(如刊头加 "v2")后**再传一次**,家人直接重新打开小程序(必要时菜单里「重新进入小程序」)。

- [ ] **Step 3: 记录结论并分支(用户 + 我)**

- 家人看到 v2 → 体验版自动随上传更新:**路线 A 成立**,结论写入设计文档,继续 Task 6
- 仍是旧版 → 回到设计文档的两分支,用户拍板:(a) 接受每周手动去后台点「设为体验版」,CI 照常每日上传;(b) 数据通道改云开发(约 ¥19.9/月,小程序代码不变,数据加载改云调用——届时回到 brainstorming 补一份增量设计)

- [ ] **Step 4: 结论写回设计文档并 commit**

```bash
git add docs/superpowers/specs/2026-11-16-wechat-miniprogram-design.md
git commit -m "docs: Phase0 实测结论-体验版自动更新与否"
```

### Task 6: CI workflow 集成

**Files:**
- Modify: `.github/workflows/daily-push.yml`

**Interfaces:**
- Consumes: Task 1(`build_mp_data.py`)、Task 4(`mp_upload.mjs` + secrets `MP_APPID`/`MP_UPLOAD_PRIVATE_KEY`)
- Produces: 每日凌晨全自动流水线:抓取 → 生成 H5 → 部署 Pages → 打包 MP 数据 → 上传体验版 → 数据 commit 回 main(滚动历史)

- [ ] **Step 1: 改 workflow**

- `permissions` 增加 `contents: write`(数据 commit 回 main 需要;现有 `pages: write`/`id-token: write` 保留)
- 在「部署到 Pages」step **之后**追加三步:
  1. `uses: actions/setup-node@v4`,`node-version: '20'`
  2. `run: npm ci && python scripts/build_mp_data.py && node scripts/mp_upload.mjs`,env 注入 `MP_APPID: ${{ secrets.MP_APPID }}`、`MP_UPLOAD_PRIVATE_KEY: ${{ secrets.MP_UPLOAD_PRIVATE_KEY }}`,该 step `continue-on-error: true`(上传失败不阻塞 Pages 部署成果,仅 Actions 标黄告警;Step 2 的 build_mp_data 失败也会随整步跳过,数据不破坏)
  3. 提交数据回 main:`git config user.name "github-actions[bot]"` + `user.email`、`git add miniprogram/data miniprogram/packageHistory/data`、`git diff --cached --quiet || (git commit -m "chore: 小程序滚动数据更新" && git push)`(推送用默认 `GITHUB_TOKEN`,已由 permissions 授权;无变更自动跳过)

- [ ] **Step 2: 验证**

- `node -e` require 三个数据文件均成功(上任务已保证,回归确认)
- 用户在仓库 Actions 页面手动 Run workflow 一次(网页操作,`workflow_dispatch` 已有):观察新增 step 出现、上传步不红(或黄且有明确原因)、最后一步产生数据 commit
- 次日家人打开小程序看到当天新闻即端到端验收(依赖 Task 5 结论为「自动更新」)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/daily-push.yml
git commit -m "feat: CI 接入小程序打包上传与数据滚动回写"
```

## 任务外事项(不构成本计划任务)

- README/CLAUDE.md 增补小程序章节:随 Task 6 完成后一并补(一次性文档更新,包含「体验版二维码怎么发」「家人怎么加体验成员」)
- 云开发分支(b 方案):仅当 Task 5 实测不通过且用户选 b 时立项,回 brainstorming 走增量设计
