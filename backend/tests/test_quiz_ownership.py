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


if __name__ == "__main__":
    unittest.main()
