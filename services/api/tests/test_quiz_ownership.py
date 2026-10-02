"""W1 评分入口可信化回归（统一链版本，plan §11.4 / A02/A03/A05）。

- 越权 session 404（在任何评分/写入之前，零存储变更）。
- 同题同答案重放同 attempt（不重复评价）；同题不同答案 409（需新实例）。
- 客户端 correct_answer/raw_grade/record 等历史字段 → 422（不再兼容）。
- hint 从冻结量规派生且服务端记录帮助；作答后不再计帮助。
- dispute 登记 C9 复核请求；同源只有一个 active review。

全部继承 StorageSandboxTestCase；fake runner 零网络。
"""
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core.session import TutorSession, save_session  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.agents.assessment import manager as am  # noqa: E402
from app.agents.student_model.evaluation import schema as S  # noqa: E402
from app.agents.student_model.evaluation import store as st  # noqa: E402
from app.core import learner_runtime  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from tests.test_unified_submission import FakeRunner, _learner_output  # noqa: E402


def _mc_task(qid: str = "q_own_1") -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id=qid, question_revision=1,
        q_type=S.QuestionType.MULTIPLE_CHOICE, stem="P(B|A) 等于多少？",
        options={"A": "0.2", "B": "0.4", "C": "0.5"}, answer="B",
        explanation="P(B|A)=P(A∩B)/P(A)",
        rubric=[S.FrozenCriterion(id="c1", description="选对", weight=1.0)])


class QuizOwnershipTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)
        id_store.create_user("own@example.com", "Own",
                             hash_password("pw123456"), user_id="usr_own")
        id_store.create_user("eve@example.com", "Eve",
                             hash_password("pw123456"), user_id="usr_eve")
        self.token = create_token("usr_own")
        self.foreign_token = create_token("usr_eve")
        self.client = TestClient(create_app())
        save_session(TutorSession(session_id="sess_own",
                                  student_id="usr_own", title="t"))

    def tearDown(self) -> None:
        learner_runtime.reset_learner_runtime()
        super().tearDown()

    def _headers(self, token: str | None = None) -> dict:
        return {"Authorization": f"Bearer {token or self.token}"}

    def _register_task(self, task: S.TaskSnapshot | None = None) -> S.TaskSnapshot:
        task = task or _mc_task()
        am.register_task_snapshot("usr_own", task)
        return task

    def _grade(self, body: dict, token: str | None = None):
        return self.client.post("/api/v1/quiz/grade", json=body,
                                headers=self._headers(token))

    def test_grade_into_foreign_session_404_zero_writes(self):
        task = self._register_task()
        # Eve 的命名空间里也注册同题（题目身份按 journal 隔离），但用
        # Alice 的会话提交 → 会话归属 404，先于任何写入。
        am.register_task_snapshot("usr_eve", task)
        r = self._grade({"question_id": task.question_id,
                         "question_revision": 1, "student_answer": "B",
                         "session_id": "sess_own"},
                        token=self.foreign_token)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"]["code"],
                         "session_not_found")
        # 越权者 journal 零学习证据写入（题目注册不是学生证据）
        state = st.get_journal("usr_eve").state()
        self.assertEqual(len(state.sources), 0)

    def test_duplicate_submission_replays_same_attempt(self):
        task = self._register_task()
        self.runner.outputs = [_learner_output(applicable=False)]
        body = {"question_id": task.question_id, "question_revision": 1,
                "student_answer": "B", "session_id": "sess_own"}
        r1 = self._grade(body)
        calls = len(self.runner.calls)
        r2 = self._grade(body)
        self.assertEqual(r1.status_code, 202, r1.text)
        self.assertEqual(r2.status_code, 202)
        self.assertTrue(r2.json()["duplicate"])
        self.assertEqual(r2.json()["attempt_id"], r1.json()["attempt_id"])
        self.assertEqual(len(self.runner.calls), calls)  # 不二次评价

    def test_different_answer_conflicts(self):
        task = self._register_task()
        self.runner.outputs = [_learner_output(applicable=False)]
        self._grade({"question_id": task.question_id,
                     "question_revision": 1, "student_answer": "B",
                     "session_id": "sess_own"})
        r = self._grade({"question_id": task.question_id,
                         "question_revision": 1, "student_answer": "A",
                         "session_id": "sess_own"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["error"]["code"],
                         "question_already_answered")

    def test_legacy_fields_rejected(self):
        task = self._register_task()
        for extra in ({"correct_answer": "B"}, {"raw_grade": "[对]"},
                      {"record": False}, {"stem": "题干"}):
            r = self._grade({"question_id": task.question_id,
                             "question_revision": 1, "student_answer": "B",
                             **extra})
            self.assertEqual(r.status_code, 422, extra)

    def test_hint_from_rubric_records_assistance(self):
        task = self._register_task()
        r = self.client.get("/api/v1/quiz/hint", params={
            "question_id": task.question_id, "question_revision": 1},
            headers=self._headers())
        self.assertEqual(r.status_code, 200)
        self.assertIn("选对", r.json()["hint"])
        state = st.get_journal("usr_own").state()
        events = state.assistance_by_question.get(
            (task.question_id, 1), [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind,
                         S.AssistanceEventKind.HINT_REQUESTED)
        # 作答后不再给提示（计帮助）
        self.runner.outputs = [_learner_output(applicable=False)]
        self._grade({"question_id": task.question_id,
                     "question_revision": 1, "student_answer": "B",
                     "session_id": "sess_own"})
        r2 = self.client.get("/api/v1/quiz/hint", params={
            "question_id": task.question_id, "question_revision": 1},
            headers=self._headers())
        self.assertEqual(r2.json()["status"], "already_answered")
        state2 = st.get_journal("usr_own").state()
        self.assertEqual(len(state2.assistance_by_question.get(
            (task.question_id, 1), [])), 1)

    def test_dispute_registers_review_once(self):
        task = self._register_task()
        self.runner.outputs = [_learner_output(applicable=False)]
        grade = self._grade({"question_id": task.question_id,
                             "question_revision": 1, "student_answer": "B",
                             "session_id": "sess_own"}).json()
        # R02：HTTP 不再同步评价——先由 worker 驱动语义作业完成
        import asyncio
        from app.agents.student_model.evaluation.worker import EvaluationWorker
        asyncio.run(EvaluationWorker(
            runner_provider=lambda: self.runner).process_pass())
        r1 = self.client.post("/api/v1/quiz/dispute", json={
            "source_id": grade["source_id"],
            "reason": "我用了等价解法，判分没有识别"},
            headers=self._headers())
        self.assertEqual(r1.status_code, 200, r1.text)
        review_id = r1.json()["review_id"]
        r2 = self.client.post("/api/v1/quiz/dispute", json={
            "source_id": grade["source_id"], "reason": "再次异议"},
            headers=self._headers())
        self.assertEqual(r2.json()["status"], "duplicate")
        self.assertEqual(r2.json()["review_id"], review_id)
        # 他人 source 404
        r3 = self.client.post("/api/v1/quiz/dispute", json={
            "source_id": grade["source_id"], "reason": "不是我的作答"},
            headers=self._headers(self.foreign_token))
        self.assertEqual(r3.status_code, 404)

    def test_write_back_restores_card_state_by_question_id(self):
        task = self._register_task()
        # 会话 quiz_history 里已有该题（executor 出题时写入 + journal 注册）
        session = TutorSession(session_id="sess_wb", student_id="usr_own",
                               title="t")
        session.quiz_history = [{"topic": "条件概率", "questions": [{
            "id": task.question_id, "type": "multiple_choice",
            "stem": task.stem, "options": task.options,
            "answer": task.answer, "explanation": task.explanation}]}]
        save_session(session)
        self.runner.outputs = [_learner_output(applicable=False)]
        self._grade({"question_id": task.question_id,
                     "question_revision": 1, "student_answer": "B",
                     "session_id": "sess_wb"})
        from app.core.session import load_session
        again = load_session("sess_wb")
        result = again.quiz_history[0]["questions"][0].get("result")
        self.assertIsNotNone(result)
        self.assertEqual(result["question_id"], task.question_id)
        self.assertEqual(result["verdict"], "correct")

    def test_recent_lists_journal_attempts(self):
        task = self._register_task()
        self.runner.outputs = [_learner_output(applicable=False)]
        self._grade({"question_id": task.question_id,
                     "question_revision": 1, "student_answer": "B",
                     "session_id": "sess_own"})
        r = self.client.get("/api/v1/quiz/recent", headers=self._headers())
        rows = r.json()["questions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question_id"], task.question_id)
        self.assertEqual(rows[0]["verdict"], "correct")


if __name__ == "__main__":
    unittest.main()
