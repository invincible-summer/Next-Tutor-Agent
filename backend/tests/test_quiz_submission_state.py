"""Open-answer cards recover accepted answers and asynchronous grading."""
import asyncio
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.assessment import manager
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.agents.student_model.evaluation.worker import EvaluationWorker
from app.core import learner_runtime
from app.core.config import settings
from app.core.session import TutorSession, load_session, save_session
from app.identity import store as identity_store
from app.identity.security import create_token, hash_password
from app.main import create_app
from tests.storage_sandbox import StorageSandboxTestCase
from tests.test_unified_submission import FakeRunner, _open_task


class QuizSubmissionStateTest(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.addCleanup(learner_runtime.reset_learner_runtime)
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)
        identity_store.create_user("cards@example.com", "Cards",
                                   hash_password("pw123456"), user_id="usr_cards")
        identity_store.create_user("other@example.com", "Other",
                                   hash_password("pw123456"), user_id="usr_other")
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": f"Bearer {create_token('usr_cards')}"}
        self.task = _open_task()
        manager.register_task_snapshot("usr_cards", self.task)
        q = {"id": 1, "question_id": self.task.question_id,
             "question_revision": 1, "type": "short_answer",
             "stem": self.task.stem}
        self.session = TutorSession(session_id="sess_cards", student_id="usr_cards")
        self.session.quiz_history = [{"questions": [deepcopy(q)]}]
        self.session.messages = [{"role": "assistant", "content": "请作答",
                                  "toolCalls": [{"name": "generate_quiz", "result": {
                                      "data": {"questions": [deepcopy(q)]}}}]}]
        save_session(self.session)

    def submit(self, answer="因为映射改变区间端点。", **kwargs):
        return self.client.post("/api/v1/quiz/record", headers=self.headers,
                                json={"question_id": self.task.question_id,
                                      "question_revision": 1,
                                      "student_answer": answer,
                                      "session_id": self.session.session_id,
                                      **kwargs})

    def lookup(self, **params):
        return self.client.get("/api/v1/quiz/submission", headers=self.headers,
                               params={"question_id": self.task.question_id,
                                       "question_revision": 1, **params})

    def finish(self, result="met"):
        self.runner.outputs = [S.AssessmentInterpretationOutput.model_validate({
            "criterion_results": [
                {"criterion_id": key, "result": result, "evidence_refs": [],
                 "comment": ""} for key in ("k1", "k2")],
            "learner": {"applicable": False, "abstain_reason": "no_new_evidence",
                        "observation_claims": [], "concept_updates": [],
                        "feedback": "指出了端点映射，还可以写出新积分限。"},
        })]
        asyncio.run(EvaluationWorker(runner_provider=lambda: self.runner).process_pass())

    def test_pending_receipt_saves_full_answer_to_both_card_caches(self):
        answer = "第一步说明映射。\n" + "保留完整推导。" * 60 + "最后写出新积分限。"
        response = self.submit(answer)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIsNone(response.json()["task_result"])
        restored = load_session(self.session.session_id)
        a = restored.quiz_history[0]["questions"][0]["result"]
        b = restored.messages[0]["toolCalls"][0]["result"]["data"]["questions"][0]["result"]
        self.assertEqual(a, b)
        self.assertEqual(a["student_answer"], answer)
        self.assertEqual(a["attempt_id"], response.json()["attempt_id"])
        self.assertTrue(self.lookup().json()["submission"]["pending"])

    def test_unanswered_lookup_is_read_only(self):
        journal = get_journal("usr_cards")
        before = journal.state().watermark
        self.assertEqual(self.lookup().json(), {"submission": None})
        self.assertEqual(journal.state().watermark, before)
        self.assertEqual(self.runner.calls, [])

    def test_worker_result_recovers_even_when_old_session_has_no_result(self):
        accepted = self.submit().json()
        # Reproduce both an old missing snapshot and a stale stream overwrite.
        save_session(self.session)
        self.finish()
        result = self.lookup().json()["submission"]
        self.assertEqual(result["attempt_id"], accepted["attempt_id"])
        self.assertEqual(result["task_result"]["verdict"], "correct")
        self.assertEqual(result["revealed"]["answer"], self.task.answer)
        self.assertFalse(result["pending"])
        # Task-only grading outside a workspace must still finish and show up.
        self.assertEqual(result["evaluation"]["status"], "unavailable")

    def test_indeterminate_grade_remains_submitted(self):
        accepted = self.submit().json()
        self.finish("not_observed")
        result = self.lookup().json()["submission"]
        self.assertEqual(result["attempt_id"], accepted["attempt_id"])
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["task_result"]["grading_status"], "indeterminate")
        self.assertFalse(result["pending"])

    def test_failed_grading_keeps_answer_and_stops_polling(self):
        self.submit()
        asyncio.run(EvaluationWorker(runner_provider=lambda: self.runner).process_pass())
        result = self.lookup().json()["submission"]
        self.assertTrue(result["attempt_id"])
        self.assertTrue(result["student_answer"])
        self.assertFalse(result["pending"])

    def test_off_mode_keeps_accepted_answer_without_waiting_forever(self):
        with patch.object(settings, "learner_evaluation_mode", "off"):
            response = self.submit()
        self.assertEqual(response.status_code, 202)
        result = self.lookup().json()["submission"]
        self.assertTrue(result["attempt_id"])
        self.assertFalse(result["pending"])

    def test_identity_revision_and_duplicate_guards(self):
        self.assertEqual(self.lookup(question_revision=2).status_code, 409)
        self.assertEqual(self.lookup(question_id="missing").status_code, 404)
        foreign = self.client.get("/api/v1/quiz/submission", params={
            "question_id": self.task.question_id}, headers={
                "Authorization": f"Bearer {create_token('usr_other')}"})
        self.assertEqual(foreign.status_code, 404)
        first = self.submit().json()
        second = self.submit().json()
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.submit("不同答案").status_code, 409)
