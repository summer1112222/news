#!/usr/bin/env python3
"""小程序数据打包 —— 从当天 final 缓存生成小程序静态数据文件。

产出三个文件(全部走原子写):
  miniprogram/data/today.js                     主包:仅当天
  miniprogram/data/index.js                     日期索引(分包实际包含的日期,新→旧)
  miniprogram/packageHistory/data/history.js    分包:近 N 天滚动历史(N 由体积预算自适应)

体积策略:历史从最新往旧逐日装,装到 budget(默认 1.7MB,给 2MB 分包上限留余量)即止,
保底 min_days 天(常态 25-30 天,数据偏胖的日子自动缩窗)。
零网络、零配置:只读本地 .cache,不设任何环境变量。
用法:python scripts/build_mp_data.py(需先跑过 build_digest.py 有当天缓存)
"""
import io
import json
import os
import sys

# Windows 控制台默认 gbk,重配为 UTF-8(emoji/中文),与 build_digest 同惯例
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_digest import (  # noqa: E402
    short_cat, pub_date, _clean_snippet, _write_text_atomic, _beijing_now, beijing_date,
)

JS_PREFIX = "module.exports = "
# 历史分包体积预算(字节):2MB 分包上限留 ~15% 余量
BUDGET_BYTES = 1_700_000
# 历史保底天数:即使超预算也至少保留(常态数据下 14 天 ≈ 1.3MB,仍在预算内)
MIN_DAYS = 14
# 硬上限(字节):2MB 平台铁律,MIN_DAYS 保底只是尽力而为,超此值必须裁
# (胖数据日 14 天可能 ≈2MB+,保底让位于上限,从最旧一天开始丢)
HARD_CAP_BYTES = 1_950_000


def compact_item(it: dict, today: str) -> dict:
    """final 缓存里的一条新闻 → 小程序精简字段 {t,s,d,u,n}(单字母缩体积)。

    d 取 pub_date 的 MM-DD(无日期线索则空串);n 复用 build_digest 的摘要清洗,
    与 H5 版显示一致;t 不裁剪(超长由小程序端展示处理)。
    """
    d = pub_date(it, today)
    return {
        "t": (it.get("title") or "").strip(),
        "s": it.get("source") or "",
        "d": d[5:] if d else "",
        "u": it.get("url") or "",
        "n": _clean_snippet(it.get("snippet") or "", it.get("title") or ""),
    }


def to_js(obj) -> str:
    """Python 对象 → 合法 JS 模块(gnews URL 含引号/反斜杠,必须走 json 序列化禁拼接)。"""
    return JS_PREFIX + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";"


def _compact_cats(final_cats: list, today: str) -> list:
    """[{category, domestic, foreign}] → [{title, tab, domestic, foreign}](条目已精简)。"""
    out = []
    for cat in final_cats:
        out.append({
            "title": cat.get("category", ""),
            "tab": short_cat(cat.get("category", "")),
            "domestic": [compact_item(it, today) for it in cat.get("domestic", [])],
            "foreign": [compact_item(it, today) for it in cat.get("foreign", [])],
        })
    return out


def build_pack(today: str, final_cats: list, prev_history: dict,
               budget_bytes: int = BUDGET_BYTES, min_days: int = MIN_DAYS,
               hard_cap_bytes: int = HARD_CAP_BYTES) -> dict:
    """打包:当天进主包,历史按体积自适应裁窗。

    prev_history:{日期: {date, generatedAt, categories(已精简)}}——上一次打包的滚动文件,
    当天键(残留)会被剔除。返回 {today_js, index_js, history_js, kept_days(降序)}。
    体积规则:装到 budget 即止;不足 min_days 继续装(保底);但 hard_cap(2MB 铁律)
    优先于保底,超硬上限从最旧一天开始丢。
    """
    today_data = {
        "date": today,
        "generatedAt": _beijing_now().strftime("%H:%M"),
        "categories": _compact_cats(final_cats, today),
    }
    today_js = to_js(today_data)

    # 历史天:去当天、降序;从最新往旧装,超预算即止(不足 min_days 继续装)
    day_keys = sorted((k for k in prev_history if k != today), reverse=True)
    kept_days = []
    incs = []
    total = 0
    for key in day_keys:
        inc = len(json.dumps({key: prev_history[key]}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8"))
        if len(kept_days) >= min_days and total + inc > budget_bytes:
            break
        kept_days.append(key)
        incs.append(inc)
        total += inc

    # 硬上限优先于 MIN_DAYS 保底:保底是为了历史页不空,超 2MB 是上传必败,
    # 两者冲突时丢最旧的天,直到进硬上限或只剩一天
    while len(kept_days) > 1 and total > hard_cap_bytes:
        kept_days.pop()
        total -= incs.pop()

    history_obj = {k: prev_history[k] for k in kept_days}
    index_obj = [{
        "date": k,
        "total": sum(len(c.get("domestic", [])) + len(c.get("foreign", []))
                     for c in prev_history[k].get("categories", [])),
    } for k in kept_days]
    return {
        "today_js": today_js,
        "index_js": to_js(index_obj),
        "history_js": to_js(history_obj),
        "kept_days": kept_days,
    }


def load_history(path: str) -> dict:
    """读历史滚动文件 → {日期: day_obj};单日结构异常整日跳过(不整包崩)。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    text = text.strip()
    if not (text.startswith(JS_PREFIX) and text.endswith(";")):
        sys.stderr.write(f"[MP数据] 历史文件格式异常(非 module.exports),视作空:{path}\n")
        return {}
    try:
        payload = json.loads(text[len(JS_PREFIX):-1])
    except ValueError:
        sys.stderr.write(f"[MP数据] 历史文件 JSON 损坏,视作空:{path}\n")
        return {}
    if not isinstance(payload, dict):
        return {}
    out = {}
    for key, day in payload.items():
        if isinstance(day, dict) and isinstance(day.get("categories"), list):
            out[key] = day
        else:
            sys.stderr.write(f"[MP数据] 历史单日结构异常,跳过:{key}\n")
    return out


def _load_day(path: str):
    """读单日对象的 .js(today.js 形态);缺失/损坏/结构异常返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    if not (text.startswith(JS_PREFIX) and text.endswith(";")):
        return None
    try:
        obj = json.loads(text[len(JS_PREFIX):-1])
    except ValueError:
        return None
    if isinstance(obj, dict) and obj.get("date") \
            and isinstance(obj.get("categories"), list):
        return obj
    return None


def main(cache_dir: str = ".cache", mp_dir: str = "miniprogram") -> None:
    """读当天 final 缓存 + 现有滚动历史 → 写出三个数据文件。目录参数供测试注入。"""
    today = beijing_date()
    final_path = os.path.join(cache_dir, f"final-{today}.json")
    try:
        with open(final_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # 空结果保护:无当天数据直接退出,不覆盖任何已有输出
        raise SystemExit(f"❌ 缺当天成品缓存 {final_path},先跑 python scripts/build_digest.py")
    final_cats = data.get("categories")
    if not (isinstance(final_cats, list) and final_cats and all(
            isinstance(c, dict) and {"category", "domestic", "foreign"} <= set(c)
            for c in final_cats)):
        raise SystemExit(f"❌ {final_path} 结构异常(空/缺字段),拒绝打包以免覆盖旧数据")

    prev_history = load_history(
        os.path.join(mp_dir, "packageHistory", "data", "history.js"))
    # 历史累积:上一次打包的 today.js 现在是“昨天”,必须晋升进滚动池——
    # 否则当天数据只进主包,无人写回历史,history.js 永远为空
    old_today = _load_day(os.path.join(mp_dir, "data", "today.js"))
    if old_today and old_today["date"] != today:
        prev_history.setdefault(old_today["date"], old_today)
    pack = build_pack(today, final_cats, prev_history)

    _write_text_atomic(os.path.join(mp_dir, "data", "today.js"), pack["today_js"])
    _write_text_atomic(os.path.join(mp_dir, "data", "index.js"), pack["index_js"])
    _write_text_atomic(os.path.join(mp_dir, "packageHistory", "data", "history.js"),
                       pack["history_js"])

    n_today = sum(len(c.get("domestic", [])) + len(c.get("foreign", []))
                  for c in final_cats)
    kb = len(pack["history_js"].encode("utf-8")) // 1024
    sys.stderr.write(f"[MP数据] 当天{n_today}条 历史{len(pack['kept_days'])}天 分包{kb}KB\n")


if __name__ == "__main__":
    main()
