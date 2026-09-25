"""课堂 API 骨架回归（plan.md A04/A05 退出门）。

覆盖：功能关闭时 capabilities 可读/生成端点 classroom_disabled；开启后
创建幂等（同 key 同 body 重放、不同 body 409、缺 key 422）；配额 429；
多账户交叉访问 404；错误 envelope 形状。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.identity import store as id_store  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.identity.security import create_token, hash_password  # noqa: E402

WS_A = "ws_api_物理A"


def _login_as(client: TestClient, email: str) -> None:
    """直接建账号 + 签 token（绕过注册频控，与 workspace 测试同款）。"""
    user = id_store.create_user(email=email, username="",
                                password_hash=hash_password("pw"))
    client.headers["Authorization"] = f"Bearer {create_token(user.id)}"


def _make_workspace(owner: str, ws_id: str) -> None:
    ws = ws_mod.Workspace(workspace_id=ws_id, name="物理", student_id=owner)
    ws_mod.save_workspace(ws)


def _brief_body(topic: str = "动量守恒") -> dict:
    return {
        "brief": {
            "topic": topic,
            "goals": ["会判断系统动量是否守恒"],
            "source_selection": {
                "files": [{"file_id": "file_x",
                           "chapters": [{"title": "3.4 动量守恒"}]}],
                "extra_sessions": [],
            },
            "source_policy": "strict_textbook",
            "research": {"enabled": False, "timeliness": "basic"},
            "duration_minutes": 15,
            "page_plan": "auto",
            "language": "zh",
            "grade": "本科",
            "pedagogy_id": "concept_deep@1",
            "theme_id": "academic_clear@1",
            "image_density": "balanced",
            "checkpoint_density": "standard",
            "voice_preferences": {},
            "custom_requirements": "",
        },
        "start_mode": "automatic",
    }


class ClassroomApiTests(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(create_app())
        self.user_a = id_store.create_user(
            email="a@example.com", username="",
            password_hash=hash_password("pw"))
        _make_workspace(self.user_a.id, WS_A)
        self.client.headers["Authorization"] = \
            f"Bearer {create_token(self.user_a.id)}"

    def test_capabilities_readable_when_disabled(self):
        # 默认 CLASSROOM_ENABLED=0：能力端点仍可读
        resp = self.client.get("/api/v1/classroom/capabilities")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["enabled"])
        self.assertIn("limits", resp.json())

    def test_generation_disabled_envelope(self):
        resp = self.client.post(
            f"/api/v1/workspaces/{WS_A}/classroom/lessons",
            json=_brief_body(), headers={"Idempotency-Key": "k" * 16})
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error"]["code"], "classroom_disabled")
        self.assertIn("request_id", body["error"])
        self.assertFalse(body["error"]["retryable"])

    def test_templates_require_enabled(self):
        resp = self.client.get("/api/v1/classroom/templates")
        self.assertEqual(resp.status_code, 403)

    def test_create_idempotent_and_conflict(self):
        key = "idem-" + "a" * 16
        from app.core.config import settings as real_settings
        with patch.object(real_settings, "classroom_enabled", True):
            r1 = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(), headers={"Idempotency-Key": key})
            self.assertEqual(r1.status_code, 202, r1.text)
            first = r1.json()

            r2 = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(), headers={"Idempotency-Key": key})
            self.assertEqual(r2.status_code, 202)
            self.assertEqual(r2.json(), first)  # 不重复建课

            r3 = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(topic="动能定理"),
                headers={"Idempotency-Key": key})
            self.assertEqual(r3.status_code, 409)
            self.assertEqual(r3.json()["error"]["code"],
                             "idempotency_conflict")

            r4 = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body())
            self.assertEqual(r4.status_code, 422)
            self.assertEqual(r4.json()["error"]["code"], "content_invalid")

            # 列表能看到生成中的课
            listing = self.client.get(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["total"], 1)
            self.assertEqual(listing.json()["items"][0]["status"],
                             "generating")

    def test_cross_account_404(self):
        from app.core.config import settings as real_settings
        with patch.object(real_settings, "classroom_enabled", True):
            _login_as(self.client, "b@example.com")
            # B 在 A 的工作区建课 / 列表 → 404，不泄露存在性
            resp = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(), headers={"Idempotency-Key": "b" * 16})
            self.assertEqual(resp.status_code, 404)
            listing = self.client.get(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons")
            self.assertEqual(listing.status_code, 404)

    def test_quota_window_exceeded(self):
        from app.core.config import settings as real_settings
        with patch.object(real_settings, "classroom_enabled", True):
            for i in range(3):
                resp = self.client.post(
                    f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                    json=_brief_body(topic=f"主题{i}"),
                    headers={"Idempotency-Key": f"k{i}-" + "a" * 14})
                self.assertEqual(resp.status_code, 202)
            resp = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(topic="第四个"),
                headers={"Idempotency-Key": "k3-" + "a" * 14})
            self.assertEqual(resp.status_code, 429)
            self.assertEqual(resp.json()["error"]["code"], "quota_exceeded")
            # 重放第一个 key 不扣额、仍 202
            replay = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=_brief_body(topic="主题0"),
                headers={"Idempotency-Key": "k0-" + "a" * 14})
            self.assertEqual(replay.status_code, 202)

    def test_unknown_fields_rejected(self):
        from app.core.config import settings as real_settings
        with patch.object(real_settings, "classroom_enabled", True):
            body = _brief_body()
            body["brief"]["surprise"] = 1
            resp = self.client.post(
                f"/api/v1/workspaces/{WS_A}/classroom/lessons",
                json=body, headers={"Idempotency-Key": "u" * 16})
            self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
