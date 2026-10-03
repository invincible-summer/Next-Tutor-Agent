"""站内助手能力目录与导览回归（A02）。

覆盖：目录结构合法且覆盖全部 route_id、capabilities 的访客/登录/
关闭三种形态、guide 零 LLM 快路与降级、限流与错误 envelope。
测试不落任何存储（目录为只读静态文件）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient
from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import catalog
from app.agents.site_assistant.capabilities import build_assistant_capabilities
from app.agents.site_assistant.ratelimit import SlidingWindowRateLimiter
from app.api.v1.assistant import assistant_error
from app.identity.models import User
from app.schemas.assistant import AssistantRouteId, GuideRequest


class _FakeUser(User):
    def __init__(self, user_id: str, role: str = "student") -> None:  # noqa: D107
        super().__init__()
        self.id = user_id
        self.role = role


class ProductCatalogTest(unittest.TestCase):
    def test_catalog_loads_and_covers_all_routes(self) -> None:
        cat = catalog.load_product_catalog()
        self.assertTrue(cat.catalog_version)
        listed = {m.route_id for m in cat.modules}
        expected = {route for route in AssistantRouteId}
        self.assertEqual(listed, expected)
        for module in cat.modules:
            self.assertTrue(module.name.zh and module.name.en)
            self.assertTrue(module.summary.zh and module.summary.en)
            self.assertTrue(module.steps.zh and module.steps.en)

    def test_alias_matching_finds_course(self) -> None:
        for query in ("做PPT", "做课件", "备课", "课件"):
            matches = catalog.find_module_matches(query)
            self.assertTrue(matches, query)
            self.assertEqual(matches[0].route_id, AssistantRouteId.COURSE, query)

    def test_alias_matching_assessment_and_insights(self) -> None:
        self.assertEqual(
            catalog.find_module_matches("错题")[0].route_id,
            AssistantRouteId.ASSESSMENT)
        self.assertEqual(
            catalog.find_module_matches("AI教学评价")[0].route_id,
            AssistantRouteId.INSIGHTS)

    def test_account_settings_and_legacy_profile_aliases(self) -> None:
        for query, route in (("我的账户", AssistantRouteId.ACCOUNT),
                             ("个人资料", AssistantRouteId.ACCOUNT),
                             ("设置", AssistantRouteId.SETTINGS),
                             ("学习画像", AssistantRouteId.PROFILE),
                             ("我的画像", AssistantRouteId.PROFILE)):
            self.assertEqual(catalog.find_module_matches(query)[0].route_id,
                             route, query)

    def test_route_hint_wins(self) -> None:
        matches = catalog.find_module_matches(
            "笔记", route_hint=AssistantRouteId.DASHBOARD)
        self.assertEqual(matches[0].route_id, AssistantRouteId.DASHBOARD)

    def test_catalog_digest_compact(self) -> None:
        digest = catalog.catalog_digest("zh")
        self.assertIn("[chat] 聊天辅导", digest)
        self.assertLess(len(digest), 8000)


class CapabilitiesTest(unittest.TestCase):
    def test_guest_shape(self) -> None:
        with mock.patch("app.agents.site_assistant.capabilities.settings") as fake:
            fake.site_assistant_enabled = True
            fake.classroom_enabled = False
            fake.llm_api_key = ""
            caps = build_assistant_capabilities(None)
        self.assertEqual(caps.identity_mode, "guest")
        self.assertFalse(caps.conversation_enabled)
        self.assertFalse(caps.model_available)
        course = next(m for m in caps.modules if m.route_id.value == "course")
        self.assertFalse(course.available)
        self.assertIn("course", caps.disabled_reasons)

    def test_authenticated_shape(self) -> None:
        user = _FakeUser("usr_test_1")
        with mock.patch("app.agents.site_assistant.capabilities.settings") as fake:
            fake.site_assistant_enabled = True
            fake.classroom_enabled = True
            fake.llm_api_key = "sk-test"
            with mock.patch(
                "app.classroom.capabilities.user_allowed",
                return_value=(True, "")):
                caps = build_assistant_capabilities(user)
        self.assertEqual(caps.identity_mode, "authenticated")
        self.assertTrue(caps.conversation_enabled)
        self.assertTrue(caps.model_available)
        course = next(m for m in caps.modules if m.route_id.value == "course")
        self.assertTrue(course.available)
        admin = next(m for m in caps.modules if m.route_id.value == "admin")
        self.assertFalse(admin.available)

    def test_disabled_assistant(self) -> None:
        with mock.patch("app.agents.site_assistant.capabilities.settings") as fake:
            fake.site_assistant_enabled = False
            fake.classroom_enabled = True
            fake.llm_api_key = "sk-test"
            caps = build_assistant_capabilities(_FakeUser("usr_test_1"))
        self.assertFalse(caps.enabled)
        self.assertFalse(caps.conversation_enabled)
        self.assertEqual(caps.tools, [])
        self.assertEqual(caps.action_kinds, [])


class GuideServiceTest(unittest.TestCase):
    def test_strong_match_is_deterministic(self) -> None:
        import asyncio

        from app.agents.site_assistant.guide import answer_guide
        req = GuideRequest(schema_version=1, question="备课入口在哪里？")
        resp = asyncio.run(answer_guide(req))
        self.assertIn("备课上课", resp.text)
        self.assertTrue(1 <= len(resp.module_entries) <= 3)
        self.assertEqual(resp.module_entries[0].route_id, AssistantRouteId.COURSE)

    def test_no_match_without_model_falls_back(self) -> None:
        import asyncio

        from app.agents.site_assistant.guide import answer_guide
        with mock.patch(
            "app.agents.site_assistant.guide.model_available",
            return_value=False):
            req = GuideRequest(schema_version=1, question="人生的意义是什么")
            resp = asyncio.run(answer_guide(req))
        self.assertTrue(resp.text)
        self.assertTrue(resp.module_entries)


class RateLimiterTest(unittest.TestCase):
    def test_sliding_window(self) -> None:
        limiter = SlidingWindowRateLimiter(max_events=3, window_seconds=60.0)
        results = [limiter.allow("ip", now=100.0 + i * 0.1) for i in range(4)]
        self.assertEqual([ok for ok, _ in results], [True, True, True, False])
        ok, retry = limiter.allow("ip", now=160.5)
        self.assertTrue(ok)
        limiter.reset()


class AssistantApiSmokeTest(StorageSandboxTestCase):
    """路由层烟测：capabilities 200、guide 503/429 envelope。"""

    def _client(self) -> TestClient:
        from app.main import create_app

        from app.identity.store import create_user
        from app.identity.security import create_token
        user = create_user("assistant-smoke@test.local", "", "unused")
        return TestClient(create_app(), headers={"Authorization": "Bearer " + create_token(user.id)})

    def test_capabilities_endpoint(self) -> None:
        with mock.patch(
            "app.agents.site_assistant.capabilities.settings") as fake:
            fake.site_assistant_enabled = True
            fake.classroom_enabled = False
            fake.llm_api_key = ""
            client = self._client()
            resp = client.get("/api/v1/assistant/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("identity_mode", body)
        self.assertIn("catalog_version", body)

    def test_guide_disabled_returns_envelope(self) -> None:
        with mock.patch(
            "app.agents.site_assistant.capabilities.assistant_enabled",
            return_value=False):
            client = self._client()
            resp = client.post(
                "/api/v1/assistant/guide",
                json={"schema_version": 1, "question": "这个网站怎么用？"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"]["code"], "capability_disabled")
        self.assertTrue(resp.json()["error"]["request_id"])

    def test_guide_rate_limited(self) -> None:
        with mock.patch(
            "app.agents.site_assistant.capabilities.assistant_enabled",
            return_value=True):
            with mock.patch(
                "app.api.v1.assistant._guide_limiter") as fake_limiter:
                fake_limiter.allow = mock.Mock(return_value=(False, 12.0))
                client = self._client()
                resp = client.post(
                    "/api/v1/assistant/guide",
                    json={"schema_version": 1, "question": "你好"})
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "rate_limited")
        self.assertEqual(resp.headers.get("Retry-After"), "13")

    def test_error_envelope_helper(self) -> None:
        import json as jsonlib

        resp = assistant_error(409, "conversation_busy", "会话正忙", retryable=True)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            jsonlib.loads(resp.body)["error"]["code"], "conversation_busy")


if __name__ == "__main__":
    unittest.main()
