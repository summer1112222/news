'use strict';
// Task 1: Worker 纯逻辑(事件校验 + 聚合)的单元测试
// 运行:node --test worker/test/
const { test } = require('node:test');
const assert = require('node:assert');
const { validateEvent, aggregateRows } = require('../src/logic.js');

test('view 事件通过并归一化', () => {
  assert.deepEqual(validateEvent({ vid: 'a1b2c3d4', type: 'view' }), {
    vid: 'a1b2c3d4', type: 'view', cat: '', region: '', item: null,
  });
});

test('type 非法拒绝', () => {
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'x' }), null);
  assert.equal(validateEvent({ vid: 'a1b2c3d4' }), null);
  assert.equal(validateEvent(null), null);
});

test('vid 非 hex / 超 32 字符拒绝', () => {
  assert.equal(validateEvent({ vid: 'hello world', type: 'view' }), null);
  assert.equal(validateEvent({ vid: 'a'.repeat(33), type: 'view' }), null);
  assert.equal(validateEvent({ vid: 'A1B2C3D4', type: 'view' }), null); // 大写拒绝,只收小写 hex
  assert.equal(validateEvent({ type: 'view' }), null);
});

test('cat 超长拒绝(防注入)', () => {
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'tab', cat: 'x'.repeat(41) }), null);
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'tab', cat: 123 }), null); // 非字符串拒绝
});

test('item 仅 click 时可为 0-99 整数,其他 type 忽略', () => {
  // click: item=0 合法且保留(第 0 条是真实条目)
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', item: 0 }).item, 0);
  // click: item 边界 99 合法、100 拒绝
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', item: 99 }).item, 99);
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', item: 100 }), null);
  // click: item 非整数拒绝
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', item: '3' }), null);
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', item: 1.5 }), null);
  // view/tab: 传入 item 也被忽略(归一化为 null)
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'view', item: 5 }).item, null);
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'tab', cat: '时政', item: 5 }).item, null);
});

test('click 事件带 region 通过(tab/click 都允许 region)', () => {
  const ev = validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', region: '国内', item: 2 });
  assert.equal(ev.region, '国内');
  assert.equal(ev.cat, '时政');
  // region 超长拒绝
  assert.equal(validateEvent({ vid: 'a1b2c3d4', type: 'click', cat: '时政', region: 'r'.repeat(41), item: 2 }), null);
});

test('聚合:unique_vids 去重、by_cat 计 tab、clicks 降序', () => {
  const rows = [
    { vid: 'a1', type: 'view' },
    { vid: 'a2', type: 'view' },
    { vid: 'a3', type: 'view' },
    { vid: 'a1', type: 'view' },
    { vid: 'a1', type: 'tab', cat: '时政', region: '国内' },
    { vid: 'a2', type: 'tab', cat: '时政', region: '国内' },
    { vid: 'a3', type: 'tab', cat: '财经' },
    { vid: 'a1', type: 'click', cat: '时政', region: '国内', item: 1 },
    { vid: 'a2', type: 'click', cat: '时政', region: '国内', item: 1 },
    { vid: 'a3', type: 'click', cat: '财经', region: '国外', item: 2 },
    { vid: 'a1', type: 'other' }, // 未知 type 的行不参与任何计数
  ];
  const out = aggregateRows(rows);
  assert.equal(out.views, 4);
  assert.equal(out.unique_vids, 3);
  assert.deepEqual(out.by_cat, { 时政: 2, 财经: 1 });
  assert.equal(out.click_total, 3);
  assert.deepEqual(out.clicks, [
    { cat: '时政', item: 1, n: 2 },
    { cat: '财经', item: 2, n: 1 },
  ]);
});

test('聚合:空数组返回全零', () => {
  assert.deepEqual(aggregateRows([]), {
    views: 0, unique_vids: 0, by_cat: {}, click_total: 0, clicks: [],
  });
});
