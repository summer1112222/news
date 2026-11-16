// 新闻列表组件:items 元素 {t 标题, s 来源, d 日期MM-DD, u 原文链接, n 摘要}
// 点条目复制原文链接(个人主体无 web-view,看原文靠浏览器)
Component({
  properties: {
    items: {
      type: Array,
      value: [],
    },
  },
  methods: {
    onTap(e) {
      const u = e.currentTarget.dataset.u;
      if (!u) {
        wx.showToast({ title: '无原文链接', icon: 'none' });
        return;
      }
      wx.setClipboardData({
        data: u,
        success() {
          wx.showToast({ title: '链接已复制,可去浏览器打开', icon: 'none' });
        },
      });
    },
  },
});
