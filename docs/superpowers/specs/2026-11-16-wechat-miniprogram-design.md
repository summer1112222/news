# 微信小程序「每日新闻摘要」设计(路线 A:静态打包 + CI 每日上传)

日期:2026-11-16
状态:设计已确认(体验成员 ≤ 15 已核实,2026-11-16 用户确认)
适用:仅家人自用(个人主体体验版),不正式发布

## 意图与动机

家人目前通过 Server酱 微信推送收到一条瘦身消息,点链接跳 GitHub Pages H5 看全文。痛点:外链体验割裂(微信内打开第三方页面)、gnews 跳转链在微信内置浏览器经常转圈。目标:一个**原生微信内的阅读入口**——打开小程序即当天 9 类新闻,分类/地域切换流畅,体验接近 App,家人扫码即用。

关键前提(用户已确认):**只给自己/家人用,走路线 A**,不正式发布、不引入付费后端。

## 目标

- 家人(≤ 15 名体验成员)扫码使用体验版,无需应用商店/审核
- 每天凌晨 CI 流水线跑完,家人下次打开即当天新闻,**全自动、零运维、零月费**
- 9 分类横滑 tab + 国内/国外切换(移植现有移动版交互习惯)
- 查看近 30 天历史(按日期)
- 单条新闻:标题/来源/日期/摘要 + 一键复制原文链接

## 非目标(v1 不做)

- 正式发布上架、通过审核、对外推广
- 评论/收藏/搜索/推送(小程序订阅消息另议)
- 小程序内打开原文(web-view 个人主体不可用,见约束)
- 多端(App/H5 已有,小程序是补充)

## 反目标(即使技术可行也拒绝)

- 引入云开发/自建服务器/任何月费成本
- 每天提交审核发正式版(审核类目风险 + 审核时长不现实)
- 在小程序里堆全量历史导致包体积失控

## 平台约束(设计依据)

1. **体验版机制**:上传的开发版本设为体验版后,体验成员打开即最新版,**无需审核**;个人主体体验成员上限 15(企业 90)。
2. **request 域名白名单**:小程序网络请求域名须 HTTPS + ICP 备案,`github.io` 不满足 → 本方案**零网络请求**,数据全部静态打包进代码。
3. **包体积**:主包 ≤ 2MB,单个分包 ≤ 2MB,总包 ≤ 20MB。当天数据进主包,历史进分包按需加载。
4. **外链**:无法内嵌浏览外部网页;gnews 跳转链国内打不开。阅读在小程序,看原文靠「复制链接 → 浏览器」。

## 备选方案(已讨论,用户选定 A)

| 方案 | 取舍 | 结论 |
|------|------|------|
| **A. 静态打包 + miniprogram-ci 每日上传体验版** | 零后端零成本;上限 15 人、带体验版水标 | ✅ 选定 |
| B. 微信云开发(云数据库实时拉取) | 支持正式发布与增长;¥19.9/月 + 类目资质无解 | 排除(家人用不到这个规模) |
| C. 每日提审正式版 | 数据每天陈旧或每天过审 | 排除(不可行) |

框架选择:原生小程序(WXML/WXSS/JS),不用 Taro/uni-app——无跨端需求,原生零构建链最稳,CI 只需 node + miniprogram-ci。

## 架构与数据流

```
GitHub Actions cron 01:02(现有)
  → build_digest.py(现有,不动)
     → .cache/final-<日期>.json(现有)
  → 新 scripts/build_mp_data.py
     读取 final-<日期>.json + 仓库内 miniprogram/data/history/(昨日滚动文件)
     并把上一次打包的 data/today.js(现为"昨天")晋升进滚动池(历史累积机制)
     → 生成 miniprogram/data/today.js          (主包:仅当天)
     → 生成 miniprogram/packageHistory/data/history.js(分包:近 30 天)
     → 更新 miniprogram/data/index.js          (日期索引:各日分类计数,供历史页列表)
  → node scripts/mp_upload.mjs(miniprogram-ci 机器人上传,版本描述=日期)
  → workflow 将 data/ 变更 commit 回 main(contents: write,含 yesterday 滚出)
```

本地等价:`python scripts/build_mp_data.py` 单独可跑(读当天 cache,无 cache 时报错提示先跑 build_digest),配合微信开发者工具导入 `miniprogram/` 预览。

### 数据文件格式(精简字段控制体积)

```js
// data/today.js(主包);packageHistory/data/history.js 结构同理:{"2026-11-15": {...同一天结构}}
module.exports = {
  date: "2026-11-16",
  generatedAt: "01:05",
  categories: [
    {
      title: "👨‍👩‍👧 育儿新闻",
      tab: "育儿",              // short_cat 预计算,小程序端零重逻辑
      domestic: [ { t: "标题", s: "来源", d: "11-16", u: "https://...", n: "摘要" } ],
      foreign:  [ ... ]
    }
  ]
}
```

- 字段名用单字母缩体积;`url` 原样保留(复制链接用),gnews 跳转链也保留(有代理时可用)
- 体积依据(2026-11-16 实测 `final-*.json` 76–108KB/天,字段压缩后约 60–90KB/天):
  主包 = 当天 + 代码 < 300KB;历史分包**按体积自适应**——从最新往旧逐日装,装到 ≈1.7MB 预算即止
  (常态 25–30 天,数据偏胖的日子自动缩窗到 ~15 天,保底 14 天,永不触碰 2MB 分包上限;
  终审裁决:MIN_DAYS 保底与 2MB 上限冲突时上限优先,从最旧天起裁,硬上限 1.95MB)

## 小程序端设计

页面(2 个,均在 `miniprogram/`):

**pages/index(主包)——当天浏览**
- 刊头:「每日新闻摘要」+ 日期 + 国内 N 条 · 国外 N 条
- 国内/国外 segmented control(同现有 H5)
- 分类横滑 tab(sticky,scroll-view,9 个短名)
- 列表项:标题(衬线加粗)、`来源 · 11-16` 元信息行、单行摘要;点条目 → `wx.setClipboardData` 复制原文链接 + toast「链接已复制,可去浏览器打开」
- 分享:`onShareAppMessage`(标题带日期,家人群转发)
- 下拉刷新不用(无网络);页脚注明「每日凌晨自动更新 · 体验版」

**pages/history(分包 packageHistory)——历史浏览**
- 日期列表(近 30 天,各日显示总条数,来自 index.js)
- 点某日 → 复用 index 的浏览组件渲染该日数据(列表组件抽成 components/news-list)

**样式**:深浅色跟随系统(`darkmode: true` + theme.json);沿用现有报纸衬线风(serif 字体栈、暗红 accent `#8B2A2A`/暗色 `#C75656`、发丝线分隔),与 H5 版视觉一致。

## 一次性人工步骤(用户操作,约 20 分钟)

1. mp.weixin.qq.com 注册**个人主体**小程序,取 AppID
2. 后台「开发管理-开发设置-小程序代码上传」生成上传密钥 `private.<appid>.key`,连同 IP 白名单(填 Actions IP 段或留空试)配置进 GitHub Secrets:`MP_APPID`、`MP_UPLOAD_PRIVATE_KEY`
3. 后台「成员管理-体验成员」加入家人微信号(≤ 15)
4. 首次上传后,后台把该版本设为体验版,分享体验二维码给家人
5. (可选)本地装微信开发者工具,导入 `miniprogram/` 真机预览

## CI 变更(.github/workflows/daily-push.yml)

- `permissions` 增加 `contents: write`(commit 数据文件回 main)
- 新增 step:`setup-node@v4` + `npm ci`(提交 package.json/package-lock,仅 miniprogram-ci 一个依赖)
- build_digest 之后跑 `python scripts/build_mp_data.py` → `node scripts/mp_upload.mjs`(读 Secrets,失败不阻塞 Pages 部署,仅告警)
- 末尾 `git add miniprogram/data && git commit && git push`(无变更则跳过)

## 仓库布局

```
miniprogram/            # 小程序源码(原生)
  app.js / app.json / app.wxss / theme.json
  data/today.js / index.js          (CI 生成,gitignore? 否——commit 回 main 便于本地预览)
  components/news-list/
  pages/index/  pages/history/(分包)
  packageHistory/data/history.js    (CI 生成)
scripts/build_mp_data.py            # 数据打包
scripts/mp_upload.mjs              # miniprogram-ci 上传
package.json / package-lock.json    # 仅 miniprogram-ci
docs/superpowers/specs/             # 本设计文档
```

## 风险与已决开放问题

- ~~**体验成员 15 人上限**~~ **已确认:家人不超过 15 人,个人主体足够**(2026-11-16)
- ~~**⚠ 体验版是否随机器人上传自动更新(路线 A 命门,评审提出 2026-11-16)**~~
  **Phase 0 实测通过(2026-11-16)**:两次机器人上传,家人重开小程序即看到 v2——
  体验版随 miniprogram-ci 上传自动更新,无需人工去后台切版。路线 A 全自动成立,按本设计继续。
- **体验版更新可见性**:上传新体验版后,体验成员下次冷启动即最新;个别情况需在小程序菜单里手动「重新进入小程序」
- **每日 commit 体积**:每天一个小 commit(滚动历史数据),git 历史增长有限,可接受
- **上传密钥 IP 白名单**:GitHub Actions 出口 IP 不固定;若强制白名单导致上传失败,留空白名单(密钥本身在 Secrets,风险可控,属家庭项目可接受)
