// 历史页(分包):按日期浏览近 N 天。日期列表来自主包 data/index.js,
// 按日数据在本分包 data/history.js —— 选中日期时才 require,避免列表阶段整包解析。
const dayIndex = require('../../../data/index.js');

function pick(day, catIdx, region) {
  const cat = day.categories[catIdx];
  return cat ? cat[region] : [];
}

Page({
  data: {
    days: dayIndex,
    selected: '',
    tabs: [],
    catIdx: 0,
    region: 'domestic',
    items: [],
    curDom: 0,
    curFor: 0,
    scrollInto: 'tab-0',
    missing: false, // index 有而 history 缺该日
  },

  pickDay(e) {
    const date = e.currentTarget.dataset.date;
    const history = require('../../data/history.js');
    const day = history[date];
    if (!day || !Array.isArray(day.categories) || !day.categories.length) {
      this.setData({ selected: date, missing: true, items: [], tabs: [] });
      return;
    }
    const cat = day.categories[0];
    this.setData({
      selected: date,
      missing: false,
      tabs: day.categories.map((c) => c.tab),
      catIdx: 0,
      region: 'domestic',
      items: pick(day, 0, 'domestic'),
      curDom: cat.domestic.length,
      curFor: cat.foreign.length,
      scrollInto: 'tab-0',
    });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  backToList() {
    this.setData({ selected: '', missing: false, items: [], tabs: [] });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  switchCat(e) {
    if (!this.data.selected) return;
    const catIdx = Number(e.currentTarget.dataset.i) || 0;
    this.update(catIdx, this.data.region);
  },

  switchRegion(e) {
    if (!this.data.selected) return;
    const region = e.currentTarget.dataset.r === 'foreign' ? 'foreign' : 'domestic';
    this.update(this.data.catIdx, region);
  },

  update(catIdx, region) {
    const history = require('../../data/history.js');
    const day = history[this.data.selected];
    if (!day) return;
    const cat = day.categories[catIdx] || { domestic: [], foreign: [] };
    this.setData({
      catIdx,
      region,
      items: pick(day, catIdx, region),
      curDom: cat.domestic.length,
      curFor: cat.foreign.length,
      scrollInto: 'tab-' + catIdx,
    });
  },
});
