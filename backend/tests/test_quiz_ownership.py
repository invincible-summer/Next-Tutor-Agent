"""W1 评分入口可信化回归（docs/updatePlan.md A01/A02 + 同提交幂等）。

A01：/quiz/record 与 /quiz/grade 曾按裸 session_id 读写会话，从不校验归属
——B 身份可用 A 的 session_id 把 A 的题卡判分与学习痕迹污染成任意结果
（审查时已在沙箱复现）。修复后：越权 404（与 chat 路由同款「不可见」
语义），在任何评分/写入之前返回，零存储变更。

A02：判分依据收归服务端——客户端 correct_answer 不再可信，题目（答案/
选项/知识点）从本人会话的 quiz_history 快照解析；无法解析降级为
「未验证练习」零写入；同一作答重复提交只记分一次。

全部用例继承 StorageSandboxTestCase，禁止触碰生产存储根。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core.session import TutorSession, save_session  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.models import UserProfile  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

_STEM = "已知 P(A)=0.5，P(A∩B)=0.2，求 P(B|A)。"


def _quiz_set(questions: list[dict]) -> dict:
    return {"topic": "条件概率", "grade": "本科", "difficulty": 3,
            "questions": questions, "answer_verified": True,
            "verification": {"mode": "critic", "answer_verified": True}}


def _mc_question(stem: str = _STEM, answer: str = "B",
                 knowledge_point: str = "条件概率") -> dict:
    return {"id": "1", "type": "multiple_choice", "stem": stem,
            "options": {"A": "0.2", "B": "0.4", "C": "0.5", "D": "0.7"},
            "answer": answer,
            "explanation": "P(B|A)=P(A∩B)/P(A)=0.2/0.5=0.4，注意分母是条件事件的概率。",
            "knowledge_point": knowledge_point, "difficulty": 3}


class _FakeStreamLLM:
    """Offline stand-in for AsyncLLMClient: yields one answer chunk."""

    def __init__(self, text: str = "[对] 步骤与结果都正确。"):
        self._text = text
        self.prompts: list[list[dict]] = []

    async def stream(self, messages, **kwargs):
        self.prompts.append(messages)
        yield {"kind": "answer", "delta": self._text}


class QuizOwnershipTestBase(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.alice = id_store.create_user(
            email="alice@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="alice"))
        self.bob = id_store.create_user(
            email="bob@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="bob"))
        self.headers_a = {"Authorization": f"Bearer {create_token(self.alice.id)}"}
        self.headers_b = {"Authorization": f"Bearer {create_token(self.bob.id)}"}
        self.client = TestClient(create_app())

    def _make_session(self, session_id: str, owner: str,
                      questions: list[dict] | None = None) -> TutorSession:
        s = TutorSession(session_id=session_id, student_id=owner, title="t")
        s.quiz_history = [_quiz_set(questions if questions is not None
                                    else [_mc_question()])]
        save_session(s)
        return s

    def _record_payload(self, session_id: str, *, student_answer: str = "B",
                        correct_answer: str = "B", stem: str = _STEM) -> dict:
        return {"stem": stem, "q_type": "multiple_choice",
                "student_answer": student_answer,
                "correct_answer": correct_answer,
                "options": _mc_question()["options"],
                "explanation": "ignored", "knowledge_point": "条件概率",
                "session_id": session_id, "difficulty": 3}

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        out = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(root))] = p.read_bytes()
        return out


class TestAttemptFlow(QuizOwnershipTestBase):
    """W2/A14：一次判分 = 一个服务端 attempt_id 贯穿 M2 事件/账本/会话写回；
    同作答重放同 id；重练（不同作答）在账本 supersede 而非覆写历史。"""

    def _ledger_item(self, session_id: str, owner_id: str) -> dict:
        from app.core import learning_records as lr
        items = [x for x in lr.list_records(owner_id)
                 if x.get("session_id") == session_id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_attempt_id_flows_through_all_stores(self):
        from app.core.session import load_session
        from app.agents.student_model.store import read_events
        self._make_session("sess_att", self.alice.id)
        r = self.client.post(
            "/api/v1/quiz/record",
            json=self._record_payload("sess_att", student_answer="B"),
            headers=self.headers_a)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        att = body.get("attempt_id")
        self.assertTrue(str(att).startswith("att_"))
        # 会话 quiz_history 的 result 携带同一 attempt_id（重放可识别）。
        res = load_session("sess_att").quiz_history[0]["questions"][0]["result"]
        self.assertEqual(res.get("attempt_id"), att)
        # 账本 attempts 链第一条是它。
        item = self._ledger_item("sess_att", self.alice.id)
        self.assertEqual(item["attempts"][0]["attempt_id"], att)
        # M2 事件同 id（同一证据单次影响）。
        graded = [e for e in read_events(self.alice.id)
                  if e.type.name == "QUIZ_GRADED"]
        self.assertTrue(graded)
        self.assertEqual(graded[-1].payload.get("attempt_id"), att)
        # 同作答重放：返回同一 attempt_id，账本不追加第二条。
        r2 = self.client.post(
            "/api/v1/quiz/record",
            json=self._record_payload("sess_att", student_answer="B"),
            headers=self.headers_a)
        self.assertEqual(r2.json().get("attempt_id"), att)
        item = self._ledger_item("sess_att", self.alice.id)
        self.assertEqual(len(item["attempts"]), 1)

    def test_reanswer_supersedes_instead_of_overwriting(self):
        self._make_session("sess_re", self.alice.id)
        r1 = self.client.post(
            "/api/v1/quiz/record",
            json=self._record_payload("sess_re", student_answer="A"),
            headers=self.headers_a).json()
        self.assertEqual(r1["result"]["verdict"], "wrong")
        r2 = self.client.post(
            "/api/v1/quiz/record",
            json=self._record_payload("sess_re", student_answer="B"),
            headers=self.headers_a).json()
        self.assertEqual(r2["result"]["verdict"], "correct")
        item = self._ledger_item("sess_re", self.alice.id)
        attempts = item["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["superseded_by"], r2["attempt_id"])
        self.assertNotIn("superseded_by", attempts[1])
        # 顶层投影 = 最新判定；历史 attempt 保留审计（不再被覆写抹掉）。
        self.assertEqual(item["verdict"], "correct")
        self.assertEqual(attempts[0]["verdict"], "wrong")


class TestQuizOwnership(QuizOwnershipTestBase):

    def test_record_into_foreign_session_is_404_zero_writes(self):
        """A01：B 用 A 的 session_id 提交 /quiz/record → 404，A 的全部文件零变化。"""
        self._make_session("sess_a", self.alice.id)
        before = self._snapshot(self.root)
        r = self.client.post("/api/v1/quiz/record",
                             json=self._record_payload("sess_a"),
                             headers=self.headers_b)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self._snapshot(self.root), before)

    def test_grade_into_foreign_session_is_404_zero_writes(self):
        """A01：B 用 A 的 session_id 调 /quiz/grade(record=true) → 404，零变化。"""
        from app.api.v1 import quiz as quiz_api
        self._make_session("sess_a", self.alice.id)
        fake_llm = _FakeStreamLLM()
        with patch.object(quiz_api, "get_llm", lambda: fake_llm):
            before = self._snapshot(self.root)
            payload = dict(self._record_payload("sess_a"), q_type="short_answer",
                           record=True)
            r = self.client.post("/api/v1/quiz/grade", json=payload,
                                 headers=self.headers_b)
            self.assertEqual(r.status_code, 404)
            self.assertEqual(self._snapshot(self.root), before)
        # 越权请求在进入任何评分前被拒：LLM 不应收到 prompt。
        self.assertEqual(fake_llm.prompts, [])

    def test_missing_session_404_for_record(self):
        r = self.client.post("/api/v1/quiz/record",
                             json=self._record_payload("sess_nope"),
                             headers=self.headers_a)
        self.assertEqual(r.status_code, 404)

    def test_legacy_unstamped_session_belongs_to_guest(self):
        """与 chat 路由同款语义：未盖章的旧会话归共享游客，登录用户不可写。"""
        self._make_session("sess_legacy", "")
        r_authed = self.client.post(
            "/api/v1/quiz/record", json=self._record_payload("sess_legacy"),
            headers=self.headers_a)
        self.assertEqual(r_authed.status_code, 404)
        r_guest = self.client.post(
            "/api/v1/quiz/record", json=self._record_payload("sess_legacy"))
        self.assertEqual(r_guest.status_code, 200)
        self.assertEqual(r_guest.json()["status"], "ok")

    def test_owner_record_still_works(self):
        self._make_session("sess_a", self.alice.id)
        r = self.client.post("/api/v1/quiz/record",
                             json=self._record_payload("sess_a"),
                             headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["result"]["verdict"], "correct")


class TestServerAuthoritativeAnswers(QuizOwnershipTestBase):

    def test_client_answer_key_is_ignored(self):
        """A02：客户端把错误选项谎报为正确答案，服务端仍按快照判 wrong。"""
        from app.core.learning_records import list_records
        self._make_session("sess_a", self.alice.id,
                           [_mc_question(answer="B")])
        # 客户端声称 A 是正确答案、学生也选了 A —— 旧实现会记 correct。
        r = self.client.post("/api/v1/quiz/record",
                             json=self._record_payload(
                                 "sess_a", student_answer="A",
                                 correct_answer="A"),
                             headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"]["verdict"], "wrong")
        records = [rec for rec in list_records(self.alice.id)
                   if rec.get("stem", "").startswith(_STEM[:30])]
        self.assertTrue(records)
        self.assertEqual(records[-1]["verdict"], "wrong")

    def test_unmatched_stem_degrades_to_unverified_practice(self):
        """A02：会话里定位不到该题 → 可恢复响应，零写入（不信客户端答案）。"""
        self._make_session("sess_a", self.alice.id)
        before = self._snapshot(self.root)
        payload = self._record_payload("sess_a", stem="一道会话里不存在的题目？")
        r = self.client.post("/api/v1/quiz/record", json=payload,
                             headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "unverified_practice")
        self.assertEqual(body["code"], "question_unresolved")
        self.assertEqual(self._snapshot(self.root), before)

    def test_no_session_id_is_unverified_practice(self):
        """A02：无会话的自带题目只进未验证练习，不写 M2/账本。"""
        before = self._snapshot(self.root)
        payload = self._record_payload("")
        r = self.client.post("/api/v1/quiz/record", json=payload,
                             headers=self.headers_a)
        self.assertEqual(r.json()["status"], "unverified_practice")
        self.assertEqual(self._snapshot(self.root), before)

    def test_grade_prompt_uses_server_answer(self):
        """A02：主观题批改 prompt 使用服务端快照答案，而非客户端字段。"""
        self._make_session("sess_a", self.alice.id,
                           [dict(_mc_question(), type="short_answer",
                                 answer="0.4")])
        fake_llm = _FakeStreamLLM()
        from app.api.v1 import quiz as quiz_api
        with patch.object(quiz_api, "get_llm", lambda: fake_llm):
            payload = dict(self._record_payload("sess_a"),
                           q_type="short_answer", student_answer="0.4",
                           correct_answer="999（客户端伪造）", record=False)
            r = self.client.post("/api/v1/quiz/grade", json=payload,
                                 headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            prompt = fake_llm.prompts[0][0]["content"]
            self.assertIn("0.4", prompt)
            self.assertNotIn("999", prompt)

    def test_grade_unresolved_marks_unverified_and_skips_writes(self):
        """A02：解析失败的主观题仍有反馈，但 done 标 unverified 且零写入。"""
        self._make_session("sess_a", self.alice.id)
        fake_llm = _FakeStreamLLM()
        from app.api.v1 import quiz as quiz_api
        with patch.object(quiz_api, "get_llm", lambda: fake_llm):
            before = self._snapshot(self.root)
            payload = dict(self._record_payload("sess_a"),
                           q_type="short_answer",
                           stem="会话里没有的主观题？", record=True)
            r = self.client.post("/api/v1/quiz/grade", json=payload,
                                 headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertIn('"unverified":true', r.text.replace(" ", ""))
            self.assertEqual(self._snapshot(self.root), before)

    def test_grade_record_false_writes_nothing(self):
        """§9.4：record=false 的只读点评语义保留，不写任何存储。"""
        self._make_session("sess_a", self.alice.id)
        fake_llm = _FakeStreamLLM()
        from app.api.v1 import quiz as quiz_api
        with patch.object(quiz_api, "get_llm", lambda: fake_llm):
            before = self._snapshot(self.root)
            payload = dict(self._record_payload("sess_a"),
                           q_type="short_answer", record=False)
            r = self.client.post("/api/v1/quiz/grade", json=payload,
                                 headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self._snapshot(self.root), before)


class TestIdempotentScoring(QuizOwnershipTestBase):

    def _learning_records_for_stem(self) -> list[dict]:
        from app.core.learning_records import list_records
        return [rec for rec in list_records(self.alice.id)
                if rec.get("stem", "").startswith(_STEM[:30])]

    def test_same_submission_scored_once(self):
        """同提交记分一次：重复 /quiz/record 返回已记录结果，不再新增写入。"""
        self._make_session("sess_a", self.alice.id)
        payload = self._record_payload("sess_a", student_answer="B")
        r1 = self.client.post("/api/v1/quiz/record", json=payload,
                              headers=self.headers_a)
        self.assertEqual(r1.json()["result"]["verdict"], "correct")
        r2 = self.client.post("/api/v1/quiz/record", json=payload,
                              headers=self.headers_a)
        self.assertTrue(r2.json().get("duplicate"))
        self.assertEqual(r2.json()["result"]["verdict"], "correct")
        records = self._learning_records_for_stem()
        self.assertEqual(len(records), 1)

    def test_grade_duplicate_replays_recorded_verdict_without_llm(self):
        """重复 /quiz/grade 不再触发第二次 LLM 调用，重放已记录判定。"""
        self._make_session("sess_a", self.alice.id)
        # 先经 /quiz/record 落一份已记录结果
        self.client.post("/api/v1/quiz/record",
                         json=self._record_payload("sess_a"),
                         headers=self.headers_a)
        fake_llm = _FakeStreamLLM()
        from app.api.v1 import quiz as quiz_api
        with patch.object(quiz_api, "get_llm", lambda: fake_llm):
            payload = dict(self._record_payload("sess_a"),
                           q_type="short_answer", record=True)
            r = self.client.post("/api/v1/quiz/grade", json=payload,
                                 headers=self.headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertIn("duplicate", r.text)
            self.assertEqual(fake_llm.prompts, [])  # 无第二次模型调用
        self.assertEqual(len(self._learning_records_for_stem()), 1)


class TestHintAndDispute(QuizOwnershipTestBase):
    """W3/F02+F05：量规派生提示（服务端记录 assistance）与异议标记。"""

    def _rubric_mc(self):
        q = _mc_question()
        q["rubric"] = {
            "rubric_id": q["id"], "version": 1,
            "criteria": [
                {"id": "c1", "description": "识别条件事件与交集事件",
                 "weight": 1.0, "critical": True},
                {"id": "c2", "description": "用交集概率除以条件概率",
                 "weight": 1.0},
            ],
            "equivalent_solutions": [], "frozen_at": 1757000000.0,
        }
        return q

    def test_hint_from_rubric_without_answer_leak(self):
        self._make_session("sess_h", self.alice.id, [self._rubric_mc()])
        r = self.client.get("/api/v1/quiz/hint",
                            params={"session_id": "sess_h", "stem": _STEM},
                            headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("识别条件事件", body["hint"])
        self.assertNotIn("0.4", body["hint"])  # 不泄答案
        # 服务端标记落盘
        from app.core.session import load_session
        qd = load_session("sess_h").quiz_history[0]["questions"][0]
        self.assertTrue(qd.get("hint_requested"))

    def test_hint_foreign_session_404(self):
        self._make_session("sess_h2", self.alice.id, [self._rubric_mc()])
        r = self.client.get("/api/v1/quiz/hint",
                            params={"session_id": "sess_h2", "stem": _STEM},
                            headers=self.headers_b)
        self.assertEqual(r.status_code, 404)

    def test_hint_without_rubric_is_recoverable(self):
        self._make_session("sess_h3", self.alice.id)
        r = self.client.get("/api/v1/quiz/hint",
                            params={"session_id": "sess_h3", "stem": _STEM},
                            headers=self.headers_a)
        self.assertEqual(r.json()["status"], "no_rubric")

    def test_hint_after_answer_not_recorded(self):
        self._make_session("sess_h4", self.alice.id, [self._rubric_mc()])
        self.client.post("/api/v1/quiz/record",
                         json=self._record_payload("sess_h4"),
                         headers=self.headers_a)
        r = self.client.get("/api/v1/quiz/hint",
                            params={"session_id": "sess_h4", "stem": _STEM},
                            headers=self.headers_a)
        self.assertEqual(r.json()["status"], "already_answered")

    def test_hint_marks_assistance_in_m2_event_with_confidence_cap(self):
        from app.agents.student_model.store import read_events
        self._make_session("sess_h5", self.alice.id, [self._rubric_mc()])
        self.client.get("/api/v1/quiz/hint",
                        params={"session_id": "sess_h5", "stem": _STEM},
                        headers=self.headers_a)
        r = self.client.post("/api/v1/quiz/record",
                             json=self._record_payload("sess_h5"),
                             headers=self.headers_a)
        self.assertEqual(r.json()["status"], "ok")
        gate = r.json()["result"].get("evidence_gate") or {}
        self.assertLessEqual(gate.get("max_confidence", 1.0), 0.70)
        graded = [e for e in read_events(self.alice.id)
                  if e.type.name == "QUIZ_GRADED"]
        self.assertTrue(graded)
        self.assertEqual(graded[-1].payload.get("assistance"), "hint")

    def test_dispute_flags_own_attempt(self):
        from app.core.learning_records import record_verdict
        record_verdict(self.alice.id, "sess_d", stem="争议题", verdict="wrong",
                       student_answer="x", concept="条件概率",
                       attempt_id="att_disp1")
        r = self.client.post("/api/v1/quiz/dispute",
                             json={"attempt_id": "att_disp1",
                                   "reason": "判错了"},
                             headers=self.headers_a)
        self.assertEqual(r.json()["status"], "ok")
        from app.core.learning_records import list_records
        rec = {x["stem"]: x for x in list_records(self.alice.id)}["争议题"]
        self.assertEqual(rec["attempts"][-1]["evidence_status"], "disputed")
        # 不属于本人的/不存在的 id → not_found
        r2 = self.client.post("/api/v1/quiz/dispute",
                              json={"attempt_id": "att_nope"},
                              headers=self.headers_b)
        self.assertEqual(r2.json()["status"], "not_found")


class TestM9EvidenceEndpoint(QuizOwnershipTestBase):
    """W4/A08：/quiz 判分端点把已受理判定直供 M9——SRS 复习质量更新 +
    quiz_evidence 事件（attempt_id 幂等，重复提交不二次增长）。"""

    def _m9_state(self):
        from app.agents.learning_orchestration import store as orch_store
        return orch_store.load_state(self.alice.id)

    def test_record_endpoint_feeds_m9_srs_and_event(self):
        from app.agents.learning_orchestration import store as orch_store
        self._make_session("sess_m9", self.alice.id)
        r = self.client.post(
            "/api/v1/quiz/record",
            json=self._record_payload("sess_m9", student_answer="B"),
            headers=self.headers_a)
        att = r.json()["attempt_id"]
        state = self._m9_state()
        card = state.review_queue.get("条件概率")
        self.assertIsNotNone(card)
        self.assertEqual(card.repetitions, 1)
        events = [e for e in orch_store.read_events(self.alice.id)
                  if e.type == "quiz_evidence"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload.get("attempt_id"), att)

    def test_duplicate_submission_does_not_double_grow(self):
        from app.agents.learning_orchestration import store as orch_store
        self._make_session("sess_m9d", self.alice.id)
        payload = self._record_payload("sess_m9d", student_answer="B")
        self.client.post("/api/v1/quiz/record", json=payload,
                         headers=self.headers_a)
        r2 = self.client.post("/api/v1/quiz/record", json=payload,
                              headers=self.headers_a)
        self.assertTrue(r2.json().get("duplicate"))
        card = self._m9_state().review_queue["条件概率"]
        self.assertEqual(card.repetitions, 1)
        self.assertEqual(len([e for e in orch_store.read_events(self.alice.id)
                              if e.type == "quiz_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
