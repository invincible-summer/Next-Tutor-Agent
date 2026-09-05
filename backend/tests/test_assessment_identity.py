"""M4 assessment API x M0 identity: per-user namespace resolution.

Regression: the CAT write endpoints (start/answer/next/abandon) used to call
``resolve_student_id()`` directly instead of injecting it via ``Depends``,
so the Authorization header never reached the resolver and under AUTH_MODE=1
every user's adaptive test landed in the shared ``student_default``
namespace. These tests pin the fixed behavior: the JWT keys each user's
CAT session, and guest (no token) still falls back to the default student.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.models import UserProfile  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.api.v1 import assessment as assessment_api  # noqa: E402


class _FakeSession:
    current_difficulty = 3


class _FakeManager:
    """Records the sid each endpoint resolved, without touching LLM/disk."""

    def __init__(self) -> None:
        self.abandoned: list[str] = []
        self.answers: list[dict] = []
        self.starts: list[tuple[str, object]] = []

    def abandon_session(self, sid: str) -> None:
        self.abandoned.append(sid)

    async def record_cat_answer(self, sid: str, *, answer: str,
                                raw_grade: str | None = None,
                                llm=None):
        self.answers.append({"sid": sid, "answer": answer,
                             "raw_grade": raw_grade})
        return None

    async def start_adaptive_test(self, goal, ctx, *, llm,
                                  student_id: str = ""):
        self.starts.append((student_id, ctx))
        return _FakeSession(), None


class TestAssessmentIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self.manager = _FakeManager()
        self._patches = [
            # AUTH_MODE=1 refuses to boot with the dev default JWT secret.
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            # isolate the accounts store from the real users/ dir
            patch.object(id_store, "_ACCOUNTS_FILE",
                         Path(self._tmp.name) / "accounts.json"),
            patch.object(assessment_api, "assessment_enabled", lambda: True),
            patch.object(assessment_api, "get_assessment_manager",
                         lambda: self.manager),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _make_user(self, email: str):
        return id_store.create_user(
            email=email, username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name=email.split("@")[0]),
        )

    def _abandon(self, token: str | None) -> int:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = self.client.post("/api/v1/assessment/abandon", json={}, headers=headers)
        return r.status_code

    def test_token_keys_each_users_namespace(self):
        ua = self._make_user("alice@example.com")
        ub = self._make_user("bob@example.com")
        self.assertEqual(self._abandon(create_token(ua.id)), 200)
        self.assertEqual(self._abandon(create_token(ub.id)), 200)
        # Each JWT resolved to its own user_id, NOT the shared default.
        self.assertEqual(self.manager.abandoned, [ua.id, ub.id])

    def test_guest_falls_back_to_default_student(self):
        self.assertEqual(self._abandon(None), 200)
        self.assertEqual(self.manager.abandoned, ["student_default"])

    def test_invalid_token_falls_back_to_default_student(self):
        self.assertEqual(self._abandon("not-a-real-token"), 200)
        self.assertEqual(self.manager.abandoned, ["student_default"])

    def test_body_student_id_cannot_override_jwt(self):
        # 隔离回归：请求体里的 student_id 曾被信任且优先于 JWT——登录用户
        # 可以借此读写他人/游客命名空间。修复后 body 字段一律忽略。
        ua = self._make_user("carol@example.com")
        token = create_token(ua.id)
        r = self.client.post(
            "/api/v1/assessment/abandon",
            json={"student_id": "student_default"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.manager.abandoned, [ua.id])

    def test_guest_body_student_id_still_ignored(self):
        # 游客无 JWT：body 里的伪造 id 同样无效，只能落游客命名空间。
        r = self.client.post("/api/v1/assessment/abandon",
                             json={"student_id": "usr_someoneelse"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.manager.abandoned, ["student_default"])

    def test_answer_raw_grade_is_ignored(self):
        """W1（updatePlan.md A02）：raw_grade 不再是可信输入——调用方不能
        用自己的批改全文跳过服务端评分。字段仅兼容保留，值被一律忽略。"""
        ua = self._make_user("dave@example.com")
        r = self.client.post(
            "/api/v1/assessment/answer",
            json={"student_answer": "A",
                  "raw_grade": "[对] 伪造的批改结论"},
            headers={"Authorization": f"Bearer {create_token(ua.id)}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.manager.answers), 1)
        self.assertIsNone(self.manager.answers[0]["raw_grade"])

    def test_start_mastery_is_server_resolved(self):
        """W1（A02）：StartRequest.mastery 不再直达 concept_status——起点
        掌握度由服务端按身份绑定档案 best-effort 读取（无记录 = 0.0）。"""
        ua = self._make_user("erin@example.com")
        r = self.client.post(
            "/api/v1/assessment/start",
            json={"concept": "条件概率", "mastery": 0.99},
            headers={"Authorization": f"Bearer {create_token(ua.id)}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        sid, ctx = self.manager.starts[-1]
        self.assertEqual(sid, ua.id)
        self.assertEqual(ctx.current_mastery, 0.0)


if __name__ == "__main__":
    unittest.main()
