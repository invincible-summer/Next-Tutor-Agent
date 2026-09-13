"""G3 回归：学习评价 API 契约（plan §11.1/§11.2 / §18.2 test_evaluation_routes）。

- 错误 envelope、身份（JWT）、分页、读接口零模型调用。
- POST review/synthesis/retry；DELETE evidence；无 scope 反馈。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.identity import config as id_config  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.agents.student_model.evaluation import dialogue as dlg  # noqa: E402
from app.agents.student_model.evaluation import schema as S  # noqa: E402
from app.agents.student_model.evaluation.store import get_journal  # noqa: E402
from app.core import learner_runtime  # noqa: E402
from app.core.session import TutorSession, save_session  # noqa: E402
from app.core.workspace import Workspace, save_workspace  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

SID = "usr_route_a"
WS = "ws_route"


class RouteFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        id_store.create_user("route@example.com", "R",
                             hash_password("pw123456"), user_id=SID)
        self.token = create_token(SID)
        self.client = TestClient(create_app())
        save_workspace(Workspace(workspace_id=WS, name="物理", student_id=SID))
        self.session = TutorSession(session_id="sess_route", grade="高中",
                                    student_id=SID, workspace_id=WS)
        save_session(self.session)

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def add_source(self, message_id: str = "m_r1") -> str:
        sid = dlg.register_dialogue_source(
            student_id=SID, session=self.session,
            message={"role": "user", "content": "我能用公共端点判断并联。",
                     "message_id": message_id})
        assert sid is not None
        return sid


class TestReadRoutes(RouteFixture):
    def test_workspaces_list_includes_empty_workspace(self):
        r = self.client.get("/api/v1/learner-evaluation/workspaces",
                            headers=self.headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = [w["workspace_id"] for w in body["items"]]
        self.assertIn(WS, ids)      # 无证据的工作区也出现（§11.2）
        self.assertEqual(body["total"], 1)

    def test_workspace_detail_shape(self):
        r = self.client.get(f"/api/v1/learner-evaluation/workspaces/{WS}",
                            headers=self.headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("coverage", body)
        self.assertIn("evaluation_status", body)
        self.assertIn("scope_revision", body)

    def test_concepts_left_join_pagination(self):
        r = self.client.get(
            f"/api/v1/learner-evaluation/workspaces/{WS}/concepts",
            params={"offset": 0, "limit": 5}, headers=self.headers())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["limit"], 5)
        self.assertIn("revision", body)

    def test_foreign_workspace_404_envelope(self):
        r = self.client.get(
            "/api/v1/learner-evaluation/workspaces/ws_other",
            headers=self.headers())
        self.assertEqual(r.status_code, 404)
        err = r.json()["detail"]["error"]
        self.assertEqual(err["code"], "workspace_not_found")
        self.assertIn("request_id", err)

    def test_sessions_and_evidence_timeline(self):
        self.add_source()
        r = self.client.get(
            f"/api/v1/learner-evaluation/workspaces/{WS}/sessions",
            headers=self.headers())
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_session_ref"], "sess_route")
        r2 = self.client.get(
            f"/api/v1/learner-evaluation/workspaces/{WS}/evidence",
            headers=self.headers())
        self.assertEqual(r2.json()["total"], 1)

    def test_evidence_detail_owner_check(self):
        src = self.add_source()
        r = self.client.get(
            f"/api/v1/learner-evaluation/evidence/{src}",
            headers=self.headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["kind"], "dialogue")
        r2 = self.client.get(
            f"/api/v1/learner-evaluation/evidence/{src}")
        # 未登录游客（student_default 命名空间）看不到他人证据
        self.assertEqual(r2.status_code, 404)

    def test_reads_never_call_llm(self):
        """§10.4：GET 图谱/列表/视图零模型调用。"""
        from tests.test_unified_submission import FakeRunner
        runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(runner)
        self.add_source()
        for url in (f"/learner-evaluation/workspaces",
                    f"/learner-evaluation/workspaces/{WS}",
                    f"/learner-evaluation/workspaces/{WS}/concepts",
                    f"/learner-evaluation/workspaces/{WS}/sessions",
                    f"/learner-evaluation/workspaces/{WS}/evidence"):
            self.client.get("/api/v1" + url, headers=self.headers())
        self.assertEqual(len(runner.calls), 0)


class TestManageRoutes(RouteFixture):
    def test_review_created_and_duplicate(self):
        src = self.add_source()
        # 造一个已提交解释，使 review 有对象
        state = get_journal(SID).state()
        journal = get_journal(SID)
        interp = S.LearnerInterpretation(applicable=False,
                                         abstain_reason="none")
        journal.append([S.OpResultCommitted(
            job_id="job_x", source_id=src, source_revision=1,
            scope_revision="sr", interpretation_id="itp_x",
            interpretation=interp, abstained=True)])
        r = self.client.post(
            f"/api/v1/learner-evaluation/evidence/{src}/reviews",
            json={"interpretation_id": "itp_x",
                  "reason": "这个判读漏了我的等价解法",
                  "issue_kind": "misinterpretation", "expected_revision": 1},
            headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        review_id = r.json()["review_id"]
        r2 = self.client.post(
            f"/api/v1/learner-evaluation/evidence/{src}/reviews",
            json={"interpretation_id": "itp_x", "reason": "再来一次异议",
                  "expected_revision": 1},
            headers=self.headers())
        self.assertTrue(r2.json().get("duplicate"))
        self.assertEqual(r2.json()["review_id"], review_id)

    def test_review_revision_conflict_409(self):
        src = self.add_source()
        r = self.client.post(
            f"/api/v1/learner-evaluation/evidence/{src}/reviews",
            json={"interpretation_id": "itp_missing",
                  "reason": "理由至少四个字", "expected_revision": 9},
            headers=self.headers())
        self.assertEqual(r.status_code, 409)

    def test_synthesis_post_dedupes(self):
        r = self.client.post(
            f"/api/v1/learner-evaluation/workspaces/{WS}/synthesis",
            json={"expected_scope_revision": ""},
            headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()["job_id"]
        r2 = self.client.post(
            f"/api/v1/learner-evaluation/workspaces/{WS}/synthesis",
            json={"expected_scope_revision": ""},
            headers=self.headers())
        self.assertEqual(r2.json()["job_id"], job_id)   # 已有同版作业同 ID

    def test_delete_evidence_removes_source(self):
        src = self.add_source()
        r = self.client.delete(
            f"/api/v1/learner-evaluation/evidence/{src}",
            headers=self.headers())
        self.assertEqual(r.status_code, 200)
        state = get_journal(SID).state()
        self.assertNotIn(src, state.sources)

    def test_job_not_found(self):
        r = self.client.get("/api/v1/learner-evaluation/jobs/job_none",
                            headers=self.headers())
        self.assertEqual(r.status_code, 404)

    def test_retry_only_failed(self):
        from app.core import learner_runtime
        scheduler = learner_runtime.get_scheduler()
        job = scheduler.enqueue(SID, kind=S.JobKind.DIALOGUE_EVALUATION,
                                workspace_id=WS)
        r = self.client.post(
            f"/api/v1/learner-evaluation/jobs/{job.job_id}/retry",
            json={"expected_revision": 0}, headers=self.headers())
        self.assertEqual(r.status_code, 409)   # queued 不可手动重试


if __name__ == "__main__":
    unittest.main()
