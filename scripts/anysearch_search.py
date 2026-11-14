#!/usr/bin/env python3
"""AnySearch 精简客户端 —— 仅实现 search / batch_search,供 build_digest.py 调用。

协议:JSON-RPC 2.0,POST 到 https://api.anysearch.com/mcp
method=tools/call, params.name=工具名(如 "search")
API Key 可选,通过环境变量 ANYSEARCH_API_KEY 传入(匿名也可用,速率较低)。

对外能力:
  search(query, max_results)                 单条搜索,返回 Markdown
  batch_search(queries, max_results)         批量搜索(单次最多 5 条),返回 Markdown
  search_many(queries, max_results, interval) 自动分批 + 按查询顺序返回结果列表

网络层带指数退避重试:429/5xx/连接错误/超时最多重试 3 次,4xx 参数错误不重试。
"""
import json
import os
import random
import re
import time
from typing import List

import requests

ENDPOINT = "https://api.anysearch.com/mcp"
CLIENT_HEADER = "skill/3.0.1"
TIMEOUT = 60
RETRIES = 3
BATCH_SIZE = 5

# 复用连接:一次跑 18+ 条查询,省去反复 TLS 握手
_session = requests.Session()

# batch_search 返回里每题的分隔标题:## Query 1: 上海 最新 新闻 2026-11-14
_BATCH_HEAD_RE = re.compile(r"^## Query \d+:\s*(.*)$", re.M)


class AnySearchError(RuntimeError):
    """AnySearch 调用失败(非可重试错误或重试耗尽)。"""


def _headers() -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Anysearch-Client": CLIENT_HEADER,
    }
    api_key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_text(data: dict) -> str:
    result = data.get("result", {})
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")
    return json.dumps(result, ensure_ascii=False, indent=2)


def _call(tool_name: str, arguments: dict) -> str:
    """调用一次 JSON-RPC 工具,带指数退避重试。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    last_error = None
    for attempt in range(RETRIES):
        try:
            resp = _session.post(
                ENDPOINT, json=payload, headers=_headers(), timeout=TIMEOUT
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                err = AnySearchError(f"HTTP {resp.status_code}")
                err.response = resp  # 供 _retry_after 读取 Retry-After
                raise err
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise AnySearchError(f"API 错误: {data['error']}")
            text = _extract_text(data)
            # 匿名额度用尽时,接口会返回“自动生成 API key”的说明而非搜索结果;
            # 显式报错,避免上层把它当成 0 条结果静默写出空页面。
            if "automatically generated" in text and "api_key=" in text:
                raise AnySearchError(
                    "anysearch 匿名额度已用尽,请设置 ANYSEARCH_API_KEY 后重试"
                )
            return text
        except (requests.RequestException, ValueError, AnySearchError) as e:
            last_error = e
            # 参数/鉴权错误重试也无用,直接抛出
            if isinstance(e, requests.HTTPError) and e.response is not None:
                code = e.response.status_code
                if 400 <= code < 500 and code != 429:
                    raise AnySearchError(f"HTTP {code}: {e}") from e
            if attempt < RETRIES - 1:
                # 429 优先按 Retry-After 等待,否则指数退避 + 抖动,避免多实例同时重试
                delay = _retry_after(e) or (2 ** attempt)
                time.sleep(delay + random.uniform(0, 0.5))
    raise AnySearchError(f"{tool_name} 调用失败: {last_error}") from last_error


def _retry_after(err: Exception) -> float:
    """从 429/503 响应头读取 Retry-After(秒),取不到返回 0。"""
    resp = getattr(err, "response", None)
    if resp is None:
        return 0
    raw = (resp.headers.get("Retry-After") or "").strip()
    try:
        return min(float(raw), 30.0)
    except ValueError:
        return 0


def search(query: str, max_results: int = 6) -> str:
    """单条搜索,返回 Markdown 文本。"""
    return _call("search", {"query": query, "max_results": max_results})


def batch_search(queries: List[str], max_results: int = 6) -> str:
    """批量搜索(单次最多 5 条),返回 Markdown 文本。

    max_results 必须放进每个 query 项里:接口会忽略顶层 max_results。
    """
    if isinstance(queries, str):
        queries = [queries]
    if not queries:
        return ""
    if len(queries) > BATCH_SIZE:
        raise ValueError(f"batch_search 单次最多 {BATCH_SIZE} 条查询")
    items = [{"query": q, "max_results": max_results} for q in queries]
    return _call("batch_search", {"queries": items})


def split_batch_result(md_text: str) -> list:
    """把 batch_search 的 Markdown 按查询切分,返回 [(query_text, part), ...],按出现顺序。

    每段保留其 `## Query N: <query>` 标题里的 query 文本,供调用方按 query 定位,
    而非仅靠段数匹配——避免结果内容里出现伪 `## Query N:` 行导致段数错乱时整批丢弃。
    """
    matches = list(_BATCH_HEAD_RE.finditer(md_text))
    if not matches:
        return []
    pairs = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        part = md_text[m.end():end].strip()
        # 题与题之间的 --- 分隔线属于上一段尾部,去掉
        part = re.sub(r"\n-{3,}\s*$", "", part).strip()
        pairs.append((m.group(1).strip(), part))
    return pairs


def search_many(queries: List[str], max_results: int = 6, interval: float = 1.0) -> List[str]:
    """批量检索多条查询,返回与 queries 等长的 Markdown 列表。

    每 5 条查询合并成一次 batch_search 请求;按 query 文本定位切分段落,
    即便段数因结果内容含伪 `## Query N:` 行而错乱,也能取到对应段,
    只对未命中的条目降级为逐条 search,不丢弃整批有效响应。interval 为批与批之间的间隔秒数。
    """
    queries = list(queries)
    results = [""] * len(queries)
    batches = [queries[i:i + BATCH_SIZE] for i in range(0, len(queries), BATCH_SIZE)]
    norm = lambda q: re.sub(r"\s+", " ", q).strip().lower()

    for bi, batch in enumerate(batches):
        if bi and interval > 0:
            time.sleep(interval)
        start = bi * BATCH_SIZE
        try:
            body = batch_search(batch, max_results=max_results)
        except Exception:
            # batch 调用本身失败:整批逐条兜底
            for j, q in enumerate(batch):
                try:
                    results[start + j] = search(q, max_results=max_results)
                except Exception:
                    results[start + j] = ""
                if interval > 0 and j < len(batch) - 1:
                    time.sleep(interval)
            continue
        # 按 query 文本定位对应段;未命中的条目逐条降级(保留已命中的,不丢弃整批)
        by_q = {norm(q): p for q, p in split_batch_result(body)}
        for j, q in enumerate(batch):
            p = by_q.pop(norm(q), None)
            if p is not None:
                results[start + j] = p
                continue
            try:
                results[start + j] = search(q, max_results=max_results)
            except Exception:
                results[start + j] = ""
            if interval > 0 and j < len(batch) - 1:
                time.sleep(interval)
    return results


if __name__ == "__main__":
    # 自测
    print(search("最新时政新闻", max_results=3))
