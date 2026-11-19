// 首页:当天全部分类(动态来自 data/today.js,现为 10 类)浏览。数据静态打包(data/today.js),零网络请求。
const data = require('../../data/today.js');

function pick(data, catIdx, region) {
  const cat = data.categories[catIdx];
  return cat ? cat[region] : [];
}

Page({
  data: {
    date: data.date,
    generatedAt: data.generatedAt || '',
    tabs: data.categories.map((c) => c.tab),
    catIdx: 0,
    region: 'domestic',
    items: pick(data, 0, 'domestic'),
    domTotal: data.categories.reduce((s, c) => s + c.domestic.length, 0),
    forTotal: data.categories.reduce((s, c) => s + c.foreign.length, 0),
    // 当前分类的国内/国外条数(面板计数行)
    curDom: data.categories[0] ? data.categories[0].domestic.length : 0,
    curFor: data.categories[0] ? data.categories[0].foreign.length : 0,
    scrollInto: 'tab-0',
  },

  switchCat(e) {
    const catIdx = Number(e.currentTarget.dataset.i) || 0;
    this.update(catIdx, this.data.region);
  },

  switchRegion(e) {
    const region = e.currentTarget.dataset.r === 'foreign' ? 'foreign' : 'domestic';
    this.update(this.data.catIdx, region);
  },

  update(catIdx, region) {
    const cat = data.categories[catIdx] || { domestic: [], foreign: [] };
    this.setData({
      catIdx,
      region,
      items: pick(data, catIdx, region),
      curDom: cat.domestic.length,
      curFor: cat.foreign.length,
      scrollInto: 'tab-' + catIdx,
    });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  onShareAppMessage() {
    return {
      title: '每日新闻摘要 ' + data.date,
      path: '/pages/index/index',
    };
  },
});
