# Phase A 增长设计:熟人圈验证 + 反馈闭环

日期:2026-11-26
状态:待评审(builder check)

## 意图与为什么

这个产品(每日新闻摘要)运行稳定,但「让更多人用起来」之前,先要在熟人圈(家人 → 朋友 → 同事群,几十到几百人)验证一件事:**每天真的有人看吗?看什么?** 现在零反馈数据,所有内容调优都是拍脑袋。Phase A 用最低成本把「可分享的落地页 + 点击数据回流」建起来,观察 3~4 周,数据达标再决定 Phase B(公开运营:换主体、资质、公众号)。

已选定路线(见下方「方案」):**自定义域名 + Cloudflare 免费档**,理由:链接可达性是验证可信度的前提——github.io 在国内时好时坏,会分不清「没人看」和「打不开」。

## 目标 / 非目标 / 反目标

**目标**
1. 熟人可通过一个国内稳定可达的链接打开每日页面,在微信内打开体验良好
2. 每天有埋点数据:日独立访客、各分类 tab 点击、条目点击
3. 数据回流:日报页面与微信消息展示「昨日 N 人阅读」
4. 分享卡片(微信默认卡片)自带信息量

**非目标(留给 Phase B)**
- 个性化订阅、多用户偏好
- 公众号广播推送、认证
- 正式小程序上架、换企业主体
- 备案域名与国内托管

**反目标(即使技术上成功也算失败)**
- 埋点收集任何个人身份信息(姓名/微信号/IP 精确定位)
- 验证期改动现有内容分类与抓取逻辑(让数据说话,不边跑边改内容)
- 统计功能故障阻塞主流水线(日报每天必须照常出)

## 约束

- 现有流水线不能退化:CI 每天 04:02(北京)抓取 → 生成 → 部署 → 推微信 → 小程序上传,全部保留
- 现金成本上限 ≈ 域名 ¥60/年,其余全免费档
- 微信内 H5 无认证公众号 → 不能自定义分享卡片,只能靠 `<title>`/og:title
- `build_digest.py` 是唯一渲染入口(移动版 + PC 版);小程序数据独立打包,本期不动小程序端
- 用户(产品负责人)需自行完成:买域名、Cloudflare 账号、DNS 接管、API token、GitHub Secrets 配置

## 方案(已比较,已选)

| 路线 | 结论 |
|------|------|
| **1. 自定义域名 + Cloudflare(已选)** | Pages 托管 + Worker 计数,免费档够用;域名 ~¥60/年 |
| 2. 零现金先跑(github.io 直链 + 免费统计服务) | 否——国内可达性不稳,验证数据会被「打不开」污染 |
| 3. 备案域名 + 国内托管 | 否——属于 Phase B,现在投入为时过早 |

GitHub Pages 部署保留不动,作为后备入口(零成本,微信消息主链接切到 Cloudflare 域名即可,`PAGES_BASE_URL` 本来就只是 URL 前缀,值换成新域名,不涉及托管改动)。

## 设计

### 1. 域名与托管

- 用户购买域名(名称由用户定,文档用占位 `<DOMAIN>`),DNS 接入 Cloudflare
- `miniprogram` 之外的 `public/` 每次 CI 同时部署到 Cloudflare Pages(新增一个 workflow step,用 wrangler;secrets:`CF_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`)
- GitHub Pages 步骤原样保留
- 微信消息与页面内归档链接用 `PAGES_BASE_URL = https://<DOMAIN>`(改 secret 值,不改代码语义)

### 2. 统计 API(Cloudflare Worker + D1,免费档)

一个 Worker,两个路由:

- `POST /beacon` — 记一条事件。事件字段(全部非个人身份):
  - `date`(YYYY-MM-DD,**服务器按 UTC+8 北京日界计算**——与流水线 `beijing_date()` 及 CI 04:02 拉「昨日」的口径一致,勿用 UTC)
  - `vid`:访客匿名 ID(前端生成,见下)
  - `type`:`view`(打开页面)/ `tab`(切换分类)/ `click`(点条目)
  - `cat`:分类名;`type=click` 时附 `item`(条目在列表中的序号)
  - `region`(国内/国外,**tab 与 click 事件都带**——`cat`+`item` 无法区分国内外侧,观察期内漏采无法补采)
- `GET /stats?date=YYYY-MM-DD` — 返回该日汇总:`{views, unique_vids, by_cat: {分类: tab次数}, clicks: [{cat, item, n}], click_total}`;带 token 鉴权(secrets 里配一个随机串)
- `GET /summary?days=N&token=` — 验证期 PM 手动拉(不进主流水线):近 N 天(默认 7,clamp 1-30)每日 `unique_vids` 序列 + 相邻两日 vid 交集(次日回访率)。Phase B 触发指标靠此端点,单日 /stats 算不出跨日留存

Worker route 挂在同一 `<DOMAIN>` 下(zone 级 Worker route 优先于 Pages custom domain,`/beacon*`、`/stats*`、`/summary*` 三条)。埋点 JS 用绝对 URL(基于 `PAGES_BASE_URL`)——github.io 后备入口的访问也计入统计,视作真实读者。

D1 免费档 500 万写/天,远超需求。Worker 代码入库(放 `worker/` 目录),一次性手动部署,不进每日 CI(改版才重新部署)。

### 3. 前端埋点(`build_digest.py` 注入约 1~2KB JS)

- 页面打开:生成/读取 localStorage 里的 `vid`(8 字节随机 hex,无任何个人信息),发 `view`
- 切换分类 tab / 国内国外:发 `tab`
- 点击新闻条目:发 `click`(链接照常跳转,`navigator.sendBeacon` 不阻塞)
- 全部 `try/catch` 静默失败:埋点 JS 报错绝不影响页面
- 移动版与 PC 版都注入;PC 归档页(`digest-<日期>.html`)同样注入(归档页也被分享)

### 4. 数据回流

- `build_digest.py` 构建「今天」的页面前,`GET /stats?date=昨天`(CI 跑在北京 04:02,昨日数据已完整)
- 页面 header 显示「昨日 N 人阅读 · 最热:某分类」;微信消息 meta 行同样加一句
- 统计接口失败/超时(2 秒):正常出报,不显示该行,主流水线不受影响

### 5. 分享卡片优化

`build_digest.py` 的 `<title>` / `og:title`(现 `build_html` 内 `每日新闻摘要 <today>`)改为:
`<today> · 10类新闻精选 · <N>条`
微信默认卡片即显示此标题,无需公众号。

### 6. 用户(产品负责人)操作清单

1. 买域名、接入 Cloudflare DNS
2. Cloudflare 建 Pages 项目 + D1 数据库 + Worker 部署(token 权限:Pages 写、Workers 写、D1 写);Worker route 绑定 `<DOMAIN>/beacon*`、`<DOMAIN>/stats*`、`<DOMAIN>/summary*`
3. GitHub Secrets 更新:`PAGES_BASE_URL`(新域名)、新增 `CF_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`、`STATS_READ_TOKEN`
4. 每天把 Server酱 微信消息转发到家人群/朋友群(分发动作,零开发)

### 7. 验证指标与 Phase B 触发条件

观察 3~4 周,满足以下任一才启动 Phase B 讨论:
- 周内稳定 ≥ 15 日独立访客(定义:任意连续 7 天中,日独立访客 ≥ 15 的天数 ≥ 5),且
- 次日回访率(昨日 vid 今天再现,由 `GET /summary` 相邻日交集计算)≥ 30%

未达标:用 tab/click 数据调整内容分类(此时才动 `CATEGORIES`),再观察一个周期;连续两个周期不达标则收缩回自用工具定位。

## 留给实施者的

- Worker 代码与 D1 表结构(单表 `events(date, vid, type, cat, region, item, ts)` 即可)
- 埋点 JS 具体实现与注入位置(`build_html`/`build_html_pc` 共用的 head 尾部)
- wrangler 调用方式与 CI step 排序(建议 Pages 部署放 GitHub Pages 部署之后、小程序上传之前)
- 「昨日 N 人阅读」的具体展示样式(沿用现有 header 风格)

## 决策记录

- 路线 1(用户确认);单一版本不做个性化、验证期不动内容分类(PM 判断);GitHub Pages 保留为后备(PM 判断);统计仅匿名聚合计数、无 PII(PM 判断);成功门槛数值(PM 判断,用户可调)
