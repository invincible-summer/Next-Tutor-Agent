"""课堂检索适配器与网络边界回归（plan.md C02/C05 + §7.5）。

全部经 httpx.MockTransport 注入人工构造响应，无真实凭证/网络出站。
覆盖：Tavily 显式固定参数、时效参数、部分失败、预算、24h 缓存、
错误分类（鉴权/限流/5xx）、内网 URL 出站前拒绝、netcheck 边界。
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

import httpx  # noqa: E402

from tests.classroom_provider_mocks import (  # noqa: E402
    SAMPLE_PUBLIC_URLS, TAVILY_EXTRACT_OK, TAVILY_SEARCH_OK,
    ProviderMockTransport)

from app.classroom import limits, netcheck  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.classroom.research import ResearchBudget, TavilyResearchProvider  # noqa: E402
from app.classroom.research import base as research_base  # noqa: E402


class NetcheckTests(unittest.TestCase):
    def test_public_https_url_parsed(self) -> None:
        host, target, port = netcheck.parse_public_https_url(
            "https://Example.edu/a/b?q=1")
        self.assertEqual((host, target, port),
                         ("example.edu", "/a/b?q=1", 443))

    def test_rejects_non_https_and_userinfo(self) -> None:
        for bad in ("http://example.edu/a", "file:///etc/passwd",
                    "data:text/html,hi", "javascript:alert(1)",
                    "https://user:pw@example.edu/", "https://example.edu/a b",
                    "", "https://", "//example.edu/x",
                    "https://[::1]/", "https://127.0.0.1/x",
                    "https://169.254.169.254/meta",
                    "https://10.0.0.5/x", "https://192.168.1.2/x",
                    "https://100.64.10.20/x", "https://localhost/x",
                    "https://metadata.google.internal/x",
                    "https://example.edu:" + "9" * 6):
            with self.assertRaises(netcheck.NetcheckError, msg=bad):
                netcheck.parse_public_https_url(bad)

    def test_resolved_ips_reject_private_answers(self) -> None:
        with self.assertRaises(netcheck.NetcheckError):
            netcheck.resolve_public_ips("127.0.0.1", 443)
        with self.assertRaises(netcheck.NetcheckError):
            netcheck.resolve_public_ips("localhost", 443)

    def test_host_allowlist_uses_full_hostname(self) -> None:
        allow = ("images.pexels.com", "pixabay.com")
        self.assertTrue(netcheck.host_allowed("images.pexels.com", allow))
        self.assertFalse(netcheck.host_allowed("evil.images.pexels.com", allow),
                         "后缀匹配漏洞必须被拒绝")
        self.assertFalse(netcheck.host_allowed("pexels.com.evil.io", allow))


class TavilyProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        research_base.shared_research_cache().clear()
        self.mocks = ProviderMockTransport()
        self.provider = TavilyResearchProvider(
            api_key="test-key", transport=self.mocks.transport())

    async def asyncTearDown(self) -> None:
        await self.provider.aclose()
        research_base.shared_research_cache().clear()

    async def test_search_sends_explicit_fixed_params(self) -> None:
        self.mocks.route("POST", "/search", json_body=TAVILY_SEARCH_OK)
        hits = await self.provider.search(
            "动量守恒定律", owner="u1", budget=ResearchBudget())
        posts = self.mocks.posts_to("api.tavily.com/search")
        self.assertEqual(len(posts), 1)
        body = posts[0]["json"]
        self.assertEqual(body, {
            "query": "动量守恒定律",
            "search_depth": "basic",
            "auto_parameters": False,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "max_results": 5,
        })
        self.assertEqual(posts[0]["headers"].get("authorization"),
                         "Bearer test-key")
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].published_at, date(2024, 3, 1))
        self.assertIsNone(hits[1].published_at)

    async def test_search_timeliness_appends_range_only_when_needed(self) -> None:
        seen: list[dict] = []
        self.mocks.route("POST", "/search", json_body=TAVILY_SEARCH_OK)
        await self.provider.search("量子纠错进展", owner="u2",
                                   timeliness="week")
        seen.append(self.mocks.posts_to("/search")[-1]["json"])
        await self.provider.search("牛顿第二定律", owner="u2",
                                   timeliness="basic")
        seen.append(self.mocks.posts_to("/search")[-1]["json"])
        self.assertEqual(seen[0].get("time_range"), "week")
        self.assertNotIn("time_range", seen[1])

    async def test_search_cache_hit_skips_http_and_budget(self) -> None:
        self.mocks.route("POST", "/search", json_body=TAVILY_SEARCH_OK)
        budget = ResearchBudget()
        first = await self.provider.search("动量守恒定律", owner="u1",
                                           budget=budget)
        second = await self.provider.search("动量守恒定律", owner="u1",
                                            budget=budget)
        self.assertEqual(len(self.mocks.posts_to("/search")), 1)
        self.assertEqual(budget.search_calls, 1)
        self.assertEqual([h.url for h in first], [h.url for h in second])
        # 不同 owner 缓存隔离：再次真实出站
        await self.provider.search("动量守恒定律", owner="u2", budget=budget)
        self.assertEqual(len(self.mocks.posts_to("/search")), 2)

    async def test_search_budget_exhausted_blocks_call(self) -> None:
        self.mocks.route("POST", "/search", json_body=TAVILY_SEARCH_OK)
        budget = ResearchBudget(
            search_calls=limits.SEARCH_CALL_BUDGET)
        with self.assertRaises(ClassroomError) as ctx:
            await self.provider.search("动量守恒定律", owner="u3",
                                       budget=budget)
        self.assertEqual(ctx.exception.code, "budget_exceeded")
        self.assertEqual(self.mocks.requests, [],
                         "预算耗尽后不得发起外部请求")

    async def test_extract_partial_failure(self) -> None:
        self.mocks.route("POST", "/extract", json_body=TAVILY_EXTRACT_OK)
        budget = ResearchBudget()
        outcome = await self.provider.extract(SAMPLE_PUBLIC_URLS, owner="u1",
                                              budget=budget)
        self.assertEqual(budget.extract_urls, 2)
        self.assertEqual(len(outcome.pages), 1)
        self.assertEqual(len(outcome.failures), 1)
        page = outcome.pages[0]
        self.assertEqual(page.url, SAMPLE_PUBLIC_URLS[0])
        self.assertIn("动量守恒定律", page.text)
        self.assertIsNotNone(page.retrieved_at)
        self.assertEqual(outcome.failures[0].url, SAMPLE_PUBLIC_URLS[1])
        self.assertIn("Failed", outcome.failures[0].reason)

    async def test_extract_rejects_internal_urls_before_request(self) -> None:
        self.mocks.route("POST", "/extract", json_body=TAVILY_EXTRACT_OK)
        for bad in ("http://example.edu/x", "https://localhost/meta",
                    "https://169.254.169.254/latest",
                    "file:///etc/passwd", "https://192.168.0.10/admin"):
            with self.assertRaises(ClassroomError) as ctx:
                await self.provider.extract([bad], owner="u1")
            self.assertEqual(ctx.exception.code, "content_invalid")
        self.assertEqual(self.mocks.requests,
                         [], "内网 URL 不得离开服务端")

    async def test_extract_over_limit_rejected(self) -> None:
        urls = [f"https://example.edu/p{i}" for i in range(9)]
        with self.assertRaises(ClassroomError):
            await self.provider.extract(urls, owner="u1")

    async def test_http_error_classification(self) -> None:
        for status, retryable in ((401, False), (403, False),
                                  (429, True), (500, True)):
            self.mocks = ProviderMockTransport()
            self.mocks.route("POST", "/search", status=status,
                             json_body={"detail": "x"})
            provider = TavilyResearchProvider(
                api_key="test-key", transport=self.mocks.transport())
            try:
                with self.assertRaises(ClassroomError) as ctx:
                    await provider.search("q", owner="u9")
                self.assertEqual(ctx.exception.code, "research_unavailable")
                self.assertEqual(ctx.exception.retryable, retryable)
            finally:
                await provider.aclose()

    async def test_missing_key_is_unavailable_not_fake_online(self) -> None:
        with mock.patch("app.core.config.settings.tavily_api_key", ""):
            provider = TavilyResearchProvider(api_key="")
            try:
                with self.assertRaises(ClassroomError) as ctx:
                    await provider.search("q", owner="u1")
                self.assertEqual(ctx.exception.code, "research_unavailable")
            finally:
                await provider.aclose()

    def test_validate_query_bounds(self) -> None:
        from app.classroom.research.base import validate_query
        self.assertEqual(validate_query("  动量  守恒 \n 定律 "),
                         "动量 守恒 定律")
        with self.assertRaises(ClassroomError):
            validate_query("")
        with self.assertRaises(ClassroomError):
            validate_query("长" * 201)

    async def test_extract_dedupes_urls_and_spends_once(self) -> None:
        self.mocks.route("POST", "/extract", json_body=TAVILY_EXTRACT_OK)
        budget = ResearchBudget()
        await self.provider.extract(SAMPLE_PUBLIC_URLS + SAMPLE_PUBLIC_URLS[:1],
                                    owner="u1", budget=budget)
        self.assertEqual(budget.extract_urls, 2)
        body = self.mocks.posts_to("/extract")[0]["json"]
        self.assertEqual(body["urls"], SAMPLE_PUBLIC_URLS)


if __name__ == "__main__":
    unittest.main()
