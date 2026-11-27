'use strict';
// 埋点 Worker 的纯逻辑:事件校验 + 日聚合。
// 与 I/O 无关,可被 worker 入口(src/worker.js)与 node --test 测试共同引用。
// 事件字段均为非个人身份信息:vid 为前端随机 hex,不含任何可识别个人的数据。

const TYPES = new Set(['view', 'tab', 'click']);
const VID_RE = /^[0-9a-f]{1,32}$/; // 只收小写 hex(前端生成即小写)
const MAX_STR = 40; // cat/region 最大长度,防注入/防滥用

// 校验并归一化一条事件;非法返回 null(入口层据此回 400)。
// item 仅 click 保留(0-99 整数,序号);cat/region 缺省为空串。
function validateEvent(obj) {
  if (!obj || typeof obj !== 'object') return null;
  if (typeof obj.vid !== 'string' || !VID_RE.test(obj.vid)) return null;
  if (!TYPES.has(obj.type)) return null;

  // cat/region 可缺省(归一为空串),但给了就必须是短字符串——畸形 payload 一律拒绝
  if (obj.cat !== undefined && typeof obj.cat !== 'string') return null;
  if (obj.region !== undefined && typeof obj.region !== 'string') return null;
  const cat = typeof obj.cat === 'string' ? obj.cat : '';
  const region = typeof obj.region === 'string' ? obj.region : '';
  if (cat.length > MAX_STR || region.length > MAX_STR) return null;

  let item = null;
  if (obj.type === 'click') {
    if (!Number.isInteger(obj.item) || obj.item < 0 || obj.item > 99) return null;
    item = obj.item;
  }

  return { vid: obj.vid, type: obj.type, cat, region, item };
}

// 聚合一日的行(rows 来自 D1,字段 {vid,type,cat,region,item},多余字段忽略)。
// 返回 /stats 的数据体:views(view 次数)、unique_vids(去重访客)、
// by_cat(tab 次数按分类)、click_total、clicks([{cat,item,n}] 按 n 降序)。
function aggregateRows(rows) {
  const out = { views: 0, unique_vids: 0, by_cat: {}, click_total: 0, clicks: [] };
  if (!Array.isArray(rows) || rows.length === 0) return out;

  const vids = new Set();
  const clickCounts = new Map(); // key: JSON.stringify([cat, item])
  for (const r of rows) {
    if (r && typeof r.vid === 'string') vids.add(r.vid);
    if (!r || !TYPES.has(r.type)) continue;
    if (r.type === 'view') out.views += 1;
    if (r.type === 'tab' && r.cat) out.by_cat[r.cat] = (out.by_cat[r.cat] || 0) + 1;
    if (r.type === 'click') {
      out.click_total += 1;
      const key = JSON.stringify([r.cat, r.item]);
      clickCounts.set(key, (clickCounts.get(key) || 0) + 1);
    }
  }
  out.unique_vids = vids.size;
  out.clicks = [...clickCounts.entries()]
    .map(([key, n]) => {
      const [cat, item] = JSON.parse(key);
      return { cat, item, n };
    })
    .sort((a, b) => b.n - a.n);
  return out;
}

module.exports = { validateEvent, aggregateRows };
