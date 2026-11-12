#!/usr/bin/env python3
"""AnySearch 精简客户端 —— 仅实现 search,供 build_digest.py 调用。

协议:JSON-RPC 2.0,POST 到 https://api.anysearch.com/mcp
method=tools/call, params.name=工具名(如 "search")
API Key 可选,通过环境变量 ANYSEARCH_API_KEY 传入(匿名也可用,速率较低)。
"""
import os
import json
import requests

ENDPOINT = "https://api.anysearch.com/mcp"
CLIENT_HEADER = "skill/3.0.1"


def _call(tool_name: str, arguments: dict) -> str:
    api_key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Anysearch-Client": CLIENT_HEADER,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=40)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"AnySearch API 错误: {data['error']}")

    content = data.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            return item.get("text", "")
    return json.dumps(data.get("result", {}), ensure_ascii=False, indent=2)


def search(query: str, max_results: int = 6) -> str:
    """单条搜索,返回 Markdown 文本。"""
    return _call("search", {"query": query, "max_results": max_results})


def batch_search(queries: list, max_results: int = 6) -> str:
    """批量搜索(单次最多 5 条),返回 Markdown 文本。"""
    if isinstance(queries, str):
        queries = [queries]
    if len(queries) > 5:
        raise ValueError("batch_search 单次最多 5 条查询")
    return _call("batch_search", {
        "queries": [{"query": q} for q in queries],
        "max_results": max_results,
    })


if __name__ == "__main__":
    # 自测
    print(search("最新时政新闻", max_results=3))
