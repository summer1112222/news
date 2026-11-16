"""build_mp_data.py 的单元测试(stdlib unittest,零新增依赖)。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import build_mp_data as b

JS_PREFIX = "module.exports = "


def js_obj(text):
    """把 to_js 输出剥壳还原成 Python 对象。"""
    assert text.startswith(JS_PREFIX) and text.endswith(";")
    return json.loads(text[len(JS_PREFIX):-1])


def fake_item(title="测试标题", url="https://example.com/a?x=1",
              snippet="正文内容。", date="2026-11-16"):
    return {"title": title, "url": url, "source": "example.com",
            "snippet": snippet, "date": date}


def fake_cats(n=1, snippet="正文内容。"):
    return [{"category": "👨‍👩‍👧 育儿新闻",
             "domestic": [fake_item(snippet=snippet) for _ in range(n)],
             "foreign": []}]


def fake_day(n=1, snippet="正文内容。"):
    return {"date": "2026-11-16", "generatedAt": "01:05",
            "categories": fake_cats(n, snippet)}


def prev_days(n_days, n_items=1, snippet="正文内容。", end="2026-11-16"):
    """生成 n_days 天伪历史,end 为最新一天(不含当天语义由调用方保证)。"""
    out = {}
    d_day, d_mon, d_year = int(end[8:]), int(end[5:7]), int(end[:4])
    for i in range(n_days):
        # 简单倒推日期(跨月不管,测试固定用 11 月中旬,安全)
        day = d_day - i
        key = f"{d_year}-{d_mon:02d}-{day:02d}"
        day_obj = fake_day(n_items, snippet)
        day_obj["date"] = key
        out[key] = day_obj
    return out


class TestCompactItem(unittest.TestCase):
    def test_compact_item_maps_fields(self):
        it = b.compact_item(fake_item(), "2026-11-16")
        self.assertEqual(set(it), {"t", "s", "d", "u", "n"})
        self.assertEqual(it["t"], "测试标题")
        self.assertEqual(it["s"], "example.com")
        self.assertEqual(it["d"], "11-16")
        self.assertEqual(it["u"], "https://example.com/a?x=1")
        self.assertEqual(it["n"], "正文内容。")


class TestToJs(unittest.TestCase):
    def test_to_js_roundtrip_special_chars(self):
        obj = {"t": '标题含"引\'号\\反斜杠</script>与\n换行', "n": "摘要"}
        restored = js_obj(b.to_js(obj))
        self.assertEqual(restored, obj)


class TestBuildPack(unittest.TestCase):
    def test_tab_short_name(self):
        pack = b.build_pack("2026-11-16", fake_cats(), {})
        today = js_obj(pack["today_js"])
        self.assertEqual(today["date"], "2026-11-16")
        self.assertEqual(today["categories"][0]["tab"], "育儿")
        self.assertEqual(today["categories"][0]["title"], "👨‍👩‍👧 育儿新闻")
        self.assertEqual(today["categories"][0]["domestic"][0]["t"], "测试标题")

    def test_today_excluded_from_history_and_index_newest_first(self):
        prev = prev_days(3)  # 11-14/15/16 三天
        prev["2026-11-16"] = fake_day()  # 旧文件里残留的“当天”,必须被剔除
        pack = b.build_pack("2026-11-16", fake_cats(), prev)
        history = js_obj(pack["history_js"])
        index = js_obj(pack["index_js"])
        self.assertEqual(set(history), {"2026-11-15", "2026-11-14"})
        self.assertEqual([row["date"] for row in index], ["2026-11-15", "2026-11-14"])
        self.assertEqual(index[0]["total"], 1)  # fake_day 单分类 domestic 1 条

    def test_adaptive_window_trims_when_over_budget(self):
        prev = prev_days(6, n_items=5, snippet="这是一段很长的摘要内容用于撑大体积。" * 10)
        pack = b.build_pack("2026-11-17", fake_cats(), prev,
                            budget_bytes=2000, min_days=1)
        kept = pack["kept_days"]
        self.assertLess(len(kept), 6)
        self.assertIn("2026-11-16", kept)      # 最新一天必留
        self.assertNotIn("2026-11-11", kept)   # 最旧被裁
        history = js_obj(pack["history_js"])
        self.assertEqual(set(history), set(kept))
        index = js_obj(pack["index_js"])
        self.assertEqual([r["date"] for r in index], kept)  # 降序

    def test_min_days_guarantee(self):
        prev = prev_days(6, n_items=5, snippet="长摘要撑体积。" * 10)
        pack = b.build_pack("2026-11-17", fake_cats(), prev,
                            budget_bytes=100, min_days=3)
        self.assertEqual(len(pack["kept_days"]), 3)
        self.assertEqual(pack["kept_days"],
                         ["2026-11-16", "2026-11-15", "2026-11-14"])

    def test_hard_cap_trims_when_min_days_overshoot(self):
        # min_days 让步于硬上限:2MB 分包上限是平台铁律,保底天数只是尽力而为
        prev = prev_days(6, n_items=5, snippet="长摘要撑体积。" * 10)
        pack = b.build_pack("2026-11-17", fake_cats(), prev,
                            budget_bytes=100, min_days=3, hard_cap_bytes=2500)
        kept = pack["kept_days"]
        self.assertLess(len(kept), 3)
        size = len(pack["history_js"].encode("utf-8"))
        self.assertTrue(size <= 2500 or len(kept) == 1,
                        f"分包 {size}B 既超硬上限又不只剩一天")


class TestLoadHistory(unittest.TestCase):
    def test_load_history_skips_malformed_days(self):
        good = fake_day()
        good["date"] = "2026-11-14"
        payload = {
            "2026-11-15": {"date": "2026-11-15"},          # 缺 categories
            "2026-11-14": good,
            "2026-11-13": {"categories": "不是列表"},       # categories 类型错
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "history.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(b.to_js(payload))
            loaded = b.load_history(path)
        self.assertEqual(set(loaded), {"2026-11-14"})

    def test_load_history_missing_file_returns_empty(self):
        self.assertEqual(b.load_history("no/such/file.js"), {})


class TestMain(unittest.TestCase):
    def test_main_exits_when_final_cache_missing(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as mp:
            with self.assertRaises(SystemExit):
                b.main(cache_dir=cache, mp_dir=mp)

    def test_main_promotes_yesterdays_today_into_history(self):
        # 历史累积机制:上一次打包的 today.js(昨天)必须被晋升进滚动历史,
        # 否则 history.js 永远为空,历史页永远「暂无数据」
        from datetime import date, timedelta
        from build_digest import beijing_date
        today = beijing_date()
        yday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        yday_obj = js_obj(b.build_pack(yday, fake_cats(2), {})["today_js"])

        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as mp:
            with open(os.path.join(cache, f"final-{today}.json"), "w", encoding="utf-8") as f:
                json.dump({"categories": fake_cats()}, f)
            os.makedirs(os.path.join(mp, "data"), exist_ok=True)
            with open(os.path.join(mp, "data", "today.js"), "w", encoding="utf-8") as f:
                f.write(b.to_js(yday_obj))
            os.makedirs(os.path.join(mp, "packageHistory", "data"), exist_ok=True)

            b.main(cache_dir=cache, mp_dir=mp)

            with open(os.path.join(mp, "packageHistory", "data", "history.js"), encoding="utf-8") as f:
                history = js_obj(f.read())
            with open(os.path.join(mp, "data", "index.js"), encoding="utf-8") as f:
                index = js_obj(f.read())
        self.assertIn(yday, history)          # 昨天晋升进了滚动历史
        self.assertNotIn(today, history)      # 当天仍在主包不进分包
        self.assertEqual([r["date"] for r in index], [yday])
        self.assertEqual(index[0]["total"], 2)


if __name__ == "__main__":
    unittest.main()
