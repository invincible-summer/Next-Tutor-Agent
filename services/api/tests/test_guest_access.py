"""Guest HTTP permissions, ephemeral learning, isolation and revocation."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.storage_sandbox import StorageSandboxTestCase
from app.core import guest_policy, guest_runtime, guest_learning
from app.core.execution_policy import ExecutionPolicy, execution_policy
from app.core.knowledge_store import KnowledgeStore
from app.core.session import TutorSession
from app.core.tool_protocol import ok
from app.identity import store
from app.identity.security import create_token
from app.main import create_app

QUESTION = {"type": "multiple_choice", "stem": "1+1=?", "options": {"A": "2", "B": "3"},
            "answer": "A", "explanation": "1+1=2", "knowledge_point": "加法", "difficulty": "easy"}


class GuestAccessTests(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app())
        self.admin = store.create_user("admin@test.local", "", "unused", role="admin")
        self.user = store.create_user("user@test.local", "", "unused")
        self.admin_headers = {"Authorization": "Bearer " + create_token(self.admin.id)}
        self.user_headers = {"Authorization": "Bearer " + create_token(self.user.id)}

    def visitor(self):
        guest_policy.set_policy(True)
        response = self.client.post("/api/v1/guest/session")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        token = response.json()["token"]
        return guest_runtime.get_context(token), {"X-Guest-Token": token}

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def quiz(self, ctx, q=None):
        payload = {"questions": [copy.deepcopy(q or QUESTION)]}
        guest_learning.register_quiz(ctx, TutorSession(session_id="gst_test", knowledge=KnowledgeStore(memory_only=True)), payload)
        return payload["questions"][0]

    def test_default_deny_and_admin_policy(self):
        self.assertEqual(self.client.get("/api/v1/auth/status").json()["guest_allowed"], False)
        for method, path in [("GET", "/chat/sessions"), ("POST", "/chat/stream"), ("GET", "/guest/textbooks"),
                             ("POST", "/guest/session"), ("GET", "/assistant/capabilities")]:
            self.assertEqual(self.client.request(method, "/api/v1" + path, json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/guest-policy", headers=self.user_headers).status_code, 403)
        for value in ("true", 1, None):
            r = self.client.put("/api/v1/admin/guest-policy", headers=self.admin_headers, json={"allow_guests": value})
            self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/v1/admin/guest-policy", headers=self.admin_headers, json={"allow_guests": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get("/api/v1/auth/status").json()["guest_allowed"])

    def test_whitelist_and_invalid_jwt(self):
        _, headers = self.visitor()
        for method, path in [("GET", "/student/profile"), ("GET", "/workspaces"), ("GET", "/library"),
                             ("GET", "/notes/vault"), ("POST", "/chat/upload"), ("GET", "/chat/sessions/other"),
                             ("GET", "/assistant/capabilities"), ("POST", "/assistant/guide"),
                             ("GET", "/voice/status"), ("POST", "/voice/ticket"), ("POST", "/assessment/start"),
                             ("GET", "/admin/guest-data")]:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, "/api/v1" + path, headers=headers, json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/chat/sessions", headers=headers).json(), {"sessions": []})
        self.assertEqual(self.client.get("/api/v1/guest/textbooks", headers={**headers, "Authorization": "Bearer bad"}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/chat/sessions", headers=self.user_headers).status_code, 200)

    def test_questions_isolated_authoritative_and_nonpersistent(self):
        ctx, headers = self.visitor()
        other, other_headers = self.visitor()
        q = self.quiz(ctx)
        baseline = self.snapshot()
        body = {"question_id": q["question_id"], "question_revision": 1, "student_answer": "A"}
        self.assertEqual(self.client.post("/api/v1/quiz/record", headers=other_headers, json=body).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/quiz/record", headers=headers, json={**body, "question_revision": 2}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/quiz/record", headers=headers, json={**body, "session_id": "private"}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/quiz/record", headers=headers, json={**body, "answer": "B"}).status_code, 422)
        r = self.client.post("/api/v1/quiz/record", headers=headers, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.json()["evaluation"]["reason_code"], "guest_temporary")
        self.assertFalse(r.json()["pending"])
        repeat = self.client.post("/api/v1/quiz/grade", headers=headers, json=body).json()
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["attempt_id"], r.json()["attempt_id"])
        self.assertEqual(self.client.post("/api/v1/quiz/record", headers=headers, json={**body, "student_answer": "B"}).status_code, 409)
        self.assertEqual(self.snapshot(), baseline)
        self.assertFalse(other.submissions)

    def test_open_feedback_has_no_learning_side_effects(self):
        ctx, headers = self.visitor()
        q = self.quiz(ctx, {**QUESTION, "type": "short_answer", "options": {}, "answer": "2"})
        baseline = self.snapshot()
        parsed = guest_learning.GradingOutput(criterion_results=[{"criterion_id": "c1", "result": "met", "comment": "正确"}], feedback="正确")
        with patch("app.agents.student_model.evaluation.llm.EvaluationLLMRunner.run_structured", new=AsyncMock(return_value=SimpleNamespace(parsed=parsed))) as runner:
            r = self.client.post("/api/v1/quiz/record", headers=headers, json={"question_id": q["question_id"], "student_answer": "2"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["feedback"], "正确")
            self.assertEqual(runner.await_count, 1)
        self.assertEqual(self.snapshot(), baseline)

    def test_generation_and_failure_do_not_write(self):
        ctx, headers = self.visitor()
        baseline = self.snapshot()
        fake = SimpleNamespace(name="generate_quiz", run=AsyncMock(return_value=ok("generate_quiz", {"questions": [copy.deepcopy(QUESTION)]})))
        with patch.object(guest_learning, "tools_for", return_value=[fake]):
            r = self.client.post("/api/v1/guest/quiz/generate", headers=headers, json={"topic": "加法"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["questions"][0]["question_id"].startswith("gq_"))
            fake.run.side_effect = RuntimeError("private diagnostic")
            self.assertEqual(self.client.post("/api/v1/guest/quiz/generate", headers=headers, json={"topic": "加法"}).status_code, 503)
        self.assertEqual(self.snapshot(), baseline)

    def test_chat_real_executor_is_memory_only(self):
        ctx, headers = self.visitor()
        baseline = self.snapshot()
        class LLM:
            async def stream(self, messages, **kwargs):
                yield {"kind": "answer", "delta": "临时讲解"}
                yield {"kind": "done", "usage": {}, "finish_reason": "stop"}
        with patch("app.core.llm_async.get_llm", return_value=LLM()):
            r = self.client.post("/api/v1/chat/stream", headers=headers, json={"message": "什么是加法？"})
        self.assertEqual(r.status_code, 200, r.text)
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
        done = next(e for e in events if e["type"] == "done")
        self.assertTrue(done["temporary"])
        self.assertNotIn("trace_id", done)
        self.assertTrue(ctx.session.messages)
        self.assertEqual(self.snapshot(), baseline)

    def test_public_source_validation_and_no_spills_or_trace(self):
        _, headers = self.visitor()
        self.assertEqual(self.client.post("/api/v1/chat/stream", headers=headers, json={"message": "hello", "public_textbook_ids": ["private"]}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/chat/stream", headers=headers, json={"message": "hello", "workspace_id": "private"}).status_code, 401)
        baseline = self.snapshot()
        from app.agents.executor import _build_tool_result_message
        from app.core.trace import Trace
        with execution_policy(ExecutionPolicy(persistent=False)):
            Trace().log("turn_start", message="private temporary text")
            text = _build_tool_result_message(ok("knowledge_read", text="secret" * 50000))
            knowledge = KnowledgeStore()
            knowledge.add_file("temp", "temp.txt", "temporary")
            knowledge.remove_file("temp")
        self.assertNotIn("tool_spill", text)
        self.assertEqual(self.snapshot(), baseline)

    def test_real_chat_quiz_tool_registers_only_in_guest_memory(self):
        ctx, headers = self.visitor()
        baseline = self.snapshot()
        class LLM:
            calls = 0
            async def stream(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "tool_calls", "calls": [{"id": "call_guest", "name": "generate_quiz", "args": {"topic": "加法", "count": 1}}]}
                else:
                    yield {"kind": "answer", "delta": "请作答。"}
                yield {"kind": "done", "usage": {}, "finish_reason": "stop"}
        async def quiz_tool(_self, **kwargs):
            return ok("generate_quiz", {"questions": [copy.deepcopy(QUESTION)]})
        with patch("app.core.llm_async.get_llm", return_value=LLM()), patch("app.tools.quiz.GenerateQuizTool.run", quiz_tool):
            response = self.client.post("/api/v1/chat/stream", headers=headers, json={"message": "请出一道加法题"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"question_id": "gq_', response.text)
        self.assertEqual(len(ctx.questions), 1)
        self.assertEqual(len(ctx.session.quiz_history), 1)
        self.assertEqual(self.snapshot(), baseline)

    def test_selected_public_textbook_is_read_only(self):
        _, headers = self.visitor()
        lib = self.root / "chat_history/library"
        (lib / "public.json").write_text(json.dumps({"student_id": "public", "files": [{"id": "file_public", "filename": "公共教材.txt"}], "folders": []}), encoding="utf-8")
        (lib / "public.textbooks.json").write_text(json.dumps({"textbooks": [{"id": "tb_public", "file_id": "file_public", "name": "公共教材", "status": "ready"}]}), encoding="utf-8")
        (lib / "data/public").mkdir()
        (lib / "data/public/file_public.txt").write_text("加法是将两个数相加。", encoding="utf-8")
        baseline = self.snapshot()
        self.assertEqual(self.client.get("/api/v1/guest/textbooks", headers=headers).json()["items"][0]["id"], "tb_public")
        knowledge = guest_learning.public_knowledge(["tb_public"])
        self.assertTrue(knowledge.memory_only)
        self.assertTrue(knowledge.search("加法"))
        with self.assertRaises(HTTPException):
            guest_learning.public_knowledge(["../private"])
        self.assertEqual(self.snapshot(), baseline)

    def test_disable_delete_expiry_and_admin_purge_revoke(self):
        ctx, headers = self.visitor()
        self.quiz(ctx)
        self.client.put("/api/v1/admin/guest-policy", headers=self.admin_headers, json={"allow_guests": False})
        self.assertTrue(ctx.revoked)
        self.assertFalse(ctx.questions)
        self.assertEqual(self.client.get("/api/v1/guest/textbooks", headers=headers).status_code, 401)
        self.assertEqual(self.client.delete("/api/v1/guest/session", headers=headers).status_code, 204)
        ctx, headers = self.visitor()
        self.assertEqual(self.client.post("/api/v1/admin/guest-data/purge", headers=self.admin_headers).status_code, 200)
        self.assertTrue(ctx.revoked)
        ctx, headers = self.visitor()
        ctx.touched_at -= guest_runtime.IDLE_SECONDS + 1
        self.assertEqual(self.client.get("/api/v1/guest/textbooks", headers=headers).status_code, 401)
        self.assertTrue(ctx.revoked)

    def test_revocation_cancels_work_and_blocks_late_commit(self):
        ctx, _ = self.visitor()
        async def scenario():
            started = asyncio.Event()
            async def work():
                async with guest_runtime.active_request(ctx):
                    started.set()
                    await asyncio.Event().wait()
            task = asyncio.create_task(work())
            await started.wait()
            report = await asyncio.to_thread(guest_runtime.purge_all)
            self.assertEqual(report["cancelled_tasks"], 1)
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(HTTPException):
                self.quiz(ctx)
            self.assertFalse(ctx.questions)
        baseline = self.snapshot()
        asyncio.run(scenario())
        self.assertEqual(self.snapshot(), baseline)
