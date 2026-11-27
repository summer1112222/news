'use strict';
// 埋点统计 Worker 入口:POST /beacon(记事件)、GET /stats(单日汇总)、GET /summary(近 N 天留存)。
// 线上由 zone 级 Worker route 挂到 <DOMAIN>(优先于 Pages custom domain);
// date 一律按服务器 UTC+8 北京日界计算——流水线 beijing_date() 与 CI 04:02 拉「昨日」都按北京日,勿用 UTC。
// CommonJS 导出(wrangler 打包时转换);token 走 secret(env.STATS_READ_TOKEN),任何输出不打 token。

const { validateEvent, aggregateRows } = require('./logic.js');

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TZ_OFFSET_MS = 8 * 60 * 60 * 1000; // UTC → 北京
const DAY_MS = 24 * 60 * 60 * 1000;

function beijingDate(offsetDays) {
  return new Date(Date.now() + TZ_OFFSET_MS - offsetDays * DAY_MS).toISOString().slice(0, 10);
}

function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function checkToken(url, env) {
  return (
    typeof env.STATS_READ_TOKEN === 'string' &&
    env.STATS_READ_TOKEN.length > 0 &&
    url.searchParams.get('token') === env.STATS_READ_TOKEN
  );
}

async function handleBeacon(request, env) {
  let body;
  try {
    // sendBeacon 发的是 text/plain(简单请求,无 CORS 预检),这里按文本收再解析
    body = JSON.parse(await request.text());
  } catch (e) {
    return new Response(null, { status: 400 });
  }
  const ev = validateEvent(body);
  if (!ev) return new Response(null, { status: 400 });
  await env.DB.prepare(
    'INSERT INTO events (date, vid, type, cat, region, item, ts) VALUES (?, ?, ?, ?, ?, ?, ?)'
  )
    .bind(beijingDate(0), ev.vid, ev.type, ev.cat, ev.region, ev.item, Date.now())
    .run();
  return new Response(null, { status: 204 });
}

async function handleStats(url, env) {
  if (!checkToken(url, env)) return json(401, { error: 'unauthorized' });
  const date = url.searchParams.get('date') || '';
  if (!DATE_RE.test(date)) return json(400, { error: 'bad date' });
  const { results } = await env.DB.prepare(
    'SELECT vid, type, cat, region, item FROM events WHERE date = ?'
  )
    .bind(date)
    .all();
  return json(200, Object.assign({ date }, aggregateRows(results || [])));
}

async function handleSummary(url, env) {
  if (!checkToken(url, env)) return json(401, { error: 'unauthorized' });
  let days = parseInt(url.searchParams.get('days') || '7', 10);
  if (!Number.isInteger(days)) days = 7;
  days = Math.min(30, Math.max(1, days)); // clamp 1-30,默认 7

  const dates = [];
  for (let i = days - 1; i >= 0; i--) dates.push(beijingDate(i));
  const byDate = new Map(); // date -> Set(vid)
  for (const d of dates) byDate.set(d, new Set());

  const { results } = await env.DB.prepare(
    'SELECT date, vid FROM events WHERE date >= ? AND date <= ?'
  )
    .bind(dates[0], dates[dates.length - 1])
    .all();
  for (const row of results || []) {
    if (byDate.has(row.date)) byDate.get(row.date).add(row.vid);
  }

  const daysOut = dates.map((d) => ({ date: d, unique_vids: byDate.get(d).size }));
  const retention = [];
  for (let i = 0; i + 1 < dates.length; i++) {
    let n = 0;
    for (const v of byDate.get(dates[i])) {
      if (byDate.get(dates[i + 1]).has(v)) n += 1;
    }
    retention.push({ date: dates[i], next_day_retained: n });
  }
  return json(200, { days: daysOut, retention });
}

module.exports = {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (request.method === 'POST' && path === '/beacon') return await handleBeacon(request, env);
      if (request.method === 'GET' && path === '/stats') return await handleStats(url, env);
      if (request.method === 'GET' && path === '/summary') return await handleSummary(url, env);
      return new Response(null, { status: 404 });
    } catch (e) {
      // D1 等后端故障:不回显异常细节(防信息泄露),只给 500
      return json(500, { error: 'internal' });
    }
  },
};
