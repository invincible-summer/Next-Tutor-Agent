"""课堂 provider HTTP mocks（plan.md C05）。

只保存人工构造的响应样例——不含真实用户查询、API key 或下载素材。
供 research/media 适配器测试以 httpx.MockTransport 注入，禁止出真实网络。
"""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlparse

import httpx


class ProviderMockTransport:
    """录制请求并按路由返回人工构造响应的 MockTransport 工厂。

    用法：
        mocks = ProviderMockTransport()
        mocks.route("POST", "/search", json=TAVILY_SEARCH_OK)
        provider = TavilyResearchProvider(api_key="test-key",
                                          transport=mocks.transport())
    断言侧：``mocks.requests`` 是 [(method, url, parsed_json_body|None)]。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._routes: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []

    def route(self, method: str, url_fragment: str, *,
              json_body: Any = None, status: int = 200,
              handler: Callable[[httpx.Request], httpx.Response] | None = None,
              ) -> None:
        if handler is None:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status, json=json_body)
        self._routes.append((method.upper(), url_fragment, handler))

    def transport(self) -> httpx.MockTransport:
        outer = self

        def handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            try:
                body = json.loads(request.content.decode("utf-8")) \
                    if request.content else None
            except (ValueError, UnicodeDecodeError):
                body = None
            outer.requests.append({"method": request.method, "url": url,
                                   "json": body,
                                   "headers": dict(request.headers)})
            for method, frag, fn in outer._routes:
                if request.method == method and frag in url:
                    return fn(request)
            return httpx.Response(404, json={"detail": "mock: no route"})

        return httpx.MockTransport(handle)

    def posts_to(self, fragment: str) -> list[dict[str, Any]]:
        return [r for r in self.requests
                if r["method"] == "POST" and fragment in r["url"]]


# ---------------------------------------------------------------------------
# Tavily —— 人工构造样例（结构与官方 API 文档一致，内容为虚构）
# ---------------------------------------------------------------------------

TAVILY_SEARCH_OK: dict[str, Any] = {
    "query": "动量守恒定律",
    "results": [
        {
            "title": "动量守恒定律 - 示例大学物理讲义",
            "url": "https://example.edu/physics/momentum-conservation",
            "content": "动量守恒定律：系统不受外力或合外力为零时总动量保持不变。（人工构造样例）",
            "score": 0.97,
            "published_date": "2024-03-01",
        },
        {
            "title": "Impulse and Momentum - Example Notes",
            "url": "https://example.org/notes/impulse",
            "content": "Impulse is the change of momentum. (hand-crafted sample)",
            "score": 0.91,
        },
        {
            "title": "碰撞与动量 - 示例科普站",
            "url": "https://example.net/collision",
            "content": "弹性碰撞与完全非弹性碰撞的差异。（人工构造样例）",
            "score": 0.83,
            "published_date": "2025-11-20",
        },
    ],
    "response_time": 0.41,
}

TAVILY_EXTRACT_OK: dict[str, Any] = {
    "results": [
        {
            "url": "https://example.edu/physics/momentum-conservation",
            "raw_content": "动量守恒定律的完整正文（人工构造，占位两段）。\n"
                           "适用条件与常见误区说明。",
        }
    ],
    "failed_results": [
        {"url": "https://example.org/notes/impulse",
         "error": "Failed to extract content from URL"}
    ],
    "response_time": 0.62,
}

# 公开 HTTPS 测试 URL（人工构造域名，解析不用于出网——extract 只把 URL
# 发给 Tavily API 本身，不做本地 DNS）
SAMPLE_PUBLIC_URLS = [
    "https://example.edu/physics/momentum-conservation",
    "https://example.org/notes/impulse",
]


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""
