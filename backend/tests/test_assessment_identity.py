"""M4 × M0 身份回归（新版统一受理契约，plan §11.1/§11.4）。

- JWT 键定每用户的评价 journal；游客回退共享 ``student_default``。
- 请求体未知字段（student_id/raw_grade/mastery 等历史字段）一律 422
  拒绝——不再有“兼容保留但忽略”的旁路。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.agents.assessment import manager as am  # noqa: E402
from app.agents.student_model.evaluation import schema as S  # noqa: E402
from app.agents.student_model.evaluation import store as st  # noqa: E402
from app.core import learner_runtime  # noqa: E402
from tests.test_unified_submission import FakeRunner, _learner_output  # noqa: E402


class AssessmentIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self.tmp = tempfile.TemporaryDirectory(prefix="asmt_ident_")
        root = Path(self.tmp.name)
        for sub in ("users", "students", "chat_history", "chat_history/library",
                    "chat_history/library/data", "knowledge", "knowledge/custom",
                    "traces", "uploads", "chat_history/workspaces",
                    "chat_history/trash", "notes"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        from tests.storage_sandbox import patch_all_storage_roots
        self.patches = patch_all_storage_roots(root)
        id_patches = [
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret"),
            patch.object(id_store, "_ACCOUNTS_FILE",
                         root / "users" / "accounts.json"),
        ]
        for p in id_patches:
            p.start()
        self.patches += id_patches
        id_store.create_user("alice@example.com", "Alice",
                             hash_password("pw123456"), user_id="usr_alice")
        id_store.create_user("bob@example.com", "Bob",
                             hash_password("pw123456"), user_id="usr_bob")
        self.token_alice = create_token("usr_alice")
        self.token_bob = create_token("usr_bob")
        self.client = TestClient(create_app())
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)
        # 注册一道 MC 题到 Alice 的 journal（经服务层，不走 HTTP）
        self.task = S.TaskSnapshot(
            question_id="q_ident_1", question_revision=1,
            q_type=S.QuestionType.MULTIPLE_CHOICE, stem="1+1=?",
            options={"A": "2", "B": "3"}, answer="A", explanation="算术",
            rubric=[S.FrozenCriterion(id="c1", description="算对", weight=1.0)])
        am.register_task_snapshot("usr_alice", self.task)

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ[self._env_old] = self._env_old
        learner_runtime.reset_learner_runtime()
        self.tmp.cleanup()

    def _submit(self, token: str, body: dict):
        return self.client.post("/api/v1/assessment/submissions", json=body,
                                headers={"Authorization": f"Bearer {token}"})

    def test_token_keys_each_users_journal(self):
        self.runner.outputs = [_learner_output(applicable=False)]
        r1 = self._submit(self.token_alice, {
            "question_id": "q_ident_1", "question_revision": 1,
            "student_answer": "A"})
        self.assertEqual(r1.status_code, 202, r1.text)
        # Bob 没有 Alice journal 里的题目 → 404（不可见，非 403）
        r2 = self._submit(self.token_bob, {
            "question_id": "q_ident_1", "question_revision": 1,
            "student_answer": "A"})
        self.assertEqual(r2.status_code, 404)
        self.assertEqual(r2.json()["detail"]["error"]["code"],
                         "question_not_found")
        # Alice 的受理只落在自己的 journal
        self.assertTrue(st.journal_path("usr_alice").exists())
        self.assertFalse(st.journal_path("usr_bob").exists())

    def test_guest_falls_back_to_default_student(self):
        r = self.client.post("/api/v1/assessment/submissions", json={
            "question_id": "q_none", "question_revision": 1,
            "student_answer": "A"})
        # 未注册题目 → 404，但游客身份解析不报 401
        self.assertEqual(r.status_code, 404)

    def test_body_student_id_rejected_as_unknown_field(self):
        """§11.1：未知 JSON 字段拒绝——body student_id 不再被“忽略”，
        直接 422，杜绝伪造身份的歧义。"""
        r = self._submit(self.token_alice, {
            "question_id": "q_ident_1", "question_revision": 1,
            "student_answer": "A", "student_id": "usr_bob"})
        self.assertEqual(r.status_code, 422)

    def test_raw_grade_not_accepted(self):
        r = self._submit(self.token_alice, {
            "question_id": "q_ident_1", "question_revision": 1,
            "student_answer": "A", "raw_grade": "[对]"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
