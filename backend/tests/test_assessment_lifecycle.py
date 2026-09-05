"""M4 CAT lifecycle regressions (W2/A03, updatePlan.md §12.2 W2 行).

Pins the persisted-state contract: independent assessment ids, stop decisions
written together with the answer that triggered them, next-question guards
(no stacking on an unanswered question, no new question after terminal),
abandon persistence, refresh recovery (GET /assessment/active) and same-loop
concurrent answers recording exactly one result.

All storage goes through StorageSandboxTestCase — no production root is ever
touched.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from app.agents.assessment import get_assessment_manager  # noqa: E402
from app.agents.assessment.adaptive_test import AssessmentSession  # noqa: E402
from app.agents.assessment.question import Question, QuestionType  # noqa: E402
from app.agents.assessment.state import AssessmentContext, AssessmentGoal  # noqa: E402
from app.agents.assessment import session_store  # noqa: E402
from app.api.v1 import assessment as assessment_api  # noqa: E402
from app.main import create_app  # noqa: E402


def _mc(qid: str, stem: str, difficulty: int, answer: str = "A") -> Question:
    return Question(
        id=qid, concept="条件概率", knowledge_points=["条件概率"],
        difficulty=difficulty, q_type=QuestionType.MULTIPLE_CHOICE,
        stem=stem, options={"A": "选项甲", "B": "选项乙"}, answer=answer,
        explanation="解析")


class _ScriptedGen:
    """Replaces manager._gen_for_session: pops scripted questions in order."""

    def __init__(self, questions: list[Question]) -> None:
        self.queue = list(questions)
        self.calls = 0

    async def __call__(self, session, llm):
        self.calls += 1
        return self.queue.pop(0) if self.queue else None


def _goal(count: int = 6, difficulty: int = 3) -> AssessmentGoal:
    return AssessmentGoal(concept="条件概率", purpose="adaptive",
                          count=count, difficulty=difficulty)


def _ctx() -> AssessmentContext:
    return AssessmentContext(concept="条件概率", subject="数学", grade="高中")


def _run(coro):
    return asyncio.run(coro)


def _disk_json(sid: str) -> dict:
    path = session_store.session_path(sid)
    return json.loads(path.read_text(encoding="utf-8"))


class TestLifecycleManager(StorageSandboxTestCase):
    """Manager-level lifecycle semantics (no HTTP, no LLM)."""

    def setUp(self) -> None:
        super().setUp()
        self.am = get_assessment_manager()
        self.sid = "st_life"

    def test_start_assigns_fresh_assessment_id_each_run(self):
        # A03：每次 start 生成独立 assessment_id（旧代码恒为空串，API 回显的
        # session_id 其实是学生 id）。
        with patch.object(self.am, "_gen_for_session", _ScriptedGen([_mc("1", "题干一", 3)])):
            s1, _ = _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
        with patch.object(self.am, "_gen_for_session", _ScriptedGen([_mc("1", "题干二", 3)])):
            s2, _ = _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
        self.assertTrue(s1.assessment_id.startswith("asmt_"))
        self.assertTrue(s2.assessment_id.startswith("asmt_"))
        self.assertNotEqual(s1.assessment_id, s2.assessment_id)
        self.assertEqual(_disk_json(self.sid)["assessment_id"], s2.assessment_id)

    def test_stop_triggered_by_answer_is_persisted_in_same_write(self):
        # A03 核心：answer 触发的停止必须随本次作答落盘——此前停止只存在于
        # 响应里，刷新/重启后 get_active_session 仍当作进行中。
        gen = _ScriptedGen([_mc("1", "题干一", 3), _mc("2", "题干二", 4)])
        with patch.object(self.am, "_gen_for_session", gen):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
            r1 = _run(self.am.record_cat_answer(self.sid, answer="A"))
            self.assertEqual(r1.verdict, "correct")
            session, q2, stop = _run(self.am.next_question(self.sid, llm=None))
            self.assertEqual(stop, "")
            self.assertEqual(q2.id, "2")
            r2 = _run(self.am.record_cat_answer(self.sid, answer="A"))
            self.assertEqual(r2.verdict, "correct")
        disk = _disk_json(self.sid)
        self.assertEqual(disk["status"], "mastered")
        self.assertEqual(disk["stop_reason"], "mastered")
        self.assertIsNone(self.am.get_active_session(self.sid))

    def test_terminal_replay_same_answer_zero_writes(self):
        # 停止后同答重放：返回已记录判定，盘上文件字节不变。
        gen = _ScriptedGen([_mc("1", "题干一", 3), _mc("2", "题干二", 4)])
        with patch.object(self.am, "_gen_for_session", gen):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
            _run(self.am.record_cat_answer(self.sid, answer="A"))
            _run(self.am.next_question(self.sid, llm=None))
            _run(self.am.record_cat_answer(self.sid, answer="A"))
        before = session_store.session_path(self.sid).read_bytes()
        replay = _run(self.am.record_cat_answer(self.sid, answer="A"))
        self.assertIsNotNone(replay)
        self.assertEqual(replay.verdict, "correct")
        self.assertEqual(
            session_store.session_path(self.sid).read_bytes(), before)
        # 同题不同答不是重放：拒绝（None），也零写入。
        self.assertIsNone(_run(self.am.record_cat_answer(self.sid, answer="B")))
        self.assertEqual(
            session_store.session_path(self.sid).read_bytes(), before)

    def test_next_reissues_unanswered_question_without_stacking(self):
        # A03：当前题未答时 next 幂等重发该题，绝不叠加新题（旧代码直接追加，
        # 跳过的题永远无法被评分）。
        gen = _ScriptedGen([_mc("1", "题干一", 3), _mc("2", "题干二", 3)])
        with patch.object(self.am, "_gen_for_session", gen):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
            session, q, stop = _run(self.am.next_question(self.sid, llm=None))
        self.assertEqual(stop, "")
        self.assertIsNotNone(q)
        self.assertEqual(q.id, "1")           # 同一道题，不是第二道
        self.assertEqual(session.questions[-1].id, "1")
        self.assertEqual(len(_disk_json(self.sid)["questions"]), 1)

    def test_next_on_terminal_session_returns_persisted_stop(self):
        gen = _ScriptedGen([_mc("1", "题干一", 3), _mc("2", "题干二", 4)])
        with patch.object(self.am, "_gen_for_session", gen):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
            _run(self.am.record_cat_answer(self.sid, answer="A"))
            _run(self.am.next_question(self.sid, llm=None))
            _run(self.am.record_cat_answer(self.sid, answer="A"))
        calls_before = gen.calls
        session, q, stop = _run(self.am.next_question(self.sid, llm=None))
        self.assertIsNone(q)
        self.assertEqual(stop, "mastered")
        self.assertEqual(gen.calls, calls_before)   # 没有再生成任何题
        self.assertEqual(len(_disk_json(self.sid)["questions"]), 2)

    def test_abandon_persists_and_report_still_reads(self):
        # A03：abandon 从删文件改为落盘 abandoned——报告/审计仍可读。
        with patch.object(self.am, "_gen_for_session",
                          _ScriptedGen([_mc("1", "题干一", 3)])):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))
        self.am.abandon_session(self.sid)
        disk = _disk_json(self.sid)
        self.assertEqual(disk["status"], "abandoned")
        self.assertIsNone(self.am.get_active_session(self.sid))
        report = self.am.cat_report(self.sid)
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "abandoned")
        # 终态后 next 不出新题。
        session, q, stop = _run(self.am.next_question(self.sid, llm=None))
        self.assertIsNone(q)

    def test_legacy_session_file_loads_and_maps(self):
        # 旧格式（无 assessment_id）与终态旧文件：兼容读取，不伪造数据。
        legacy = AssessmentSession(
            student_id=self.sid, status="stopped", stop_reason="confirmed_gap")
        legacy.questions.append(_mc("1", "旧题干", 2))
        session_store.save_session(self.sid, legacy.to_dict())
        loaded = self.am.get_session(self.sid)
        self.assertEqual(loaded.status, "stopped")
        self.assertEqual(loaded.stop_reason, "confirmed_gap")
        self.assertEqual(loaded.assessment_id, "")
        self.assertIsNone(self.am.get_active_session(self.sid))
        # 旧 active 文件照常可续答。
        legacy_active = AssessmentSession(student_id=self.sid, status="active")
        legacy_active.questions.append(_mc("1", "旧题干二", 3))
        session_store.save_session(self.sid, legacy_active.to_dict())
        self.assertIsNotNone(self.am.get_active_session(self.sid))

    def test_concurrent_same_answer_records_single_result(self):
        # W2 验收「双标签一致」：同一事件循环上并发提交同一作答，只记录一次。
        # threading.RLock 对同线程协程可重入挡不住——这里钉住 asyncio 生命周期锁。
        with patch.object(self.am, "_gen_for_session",
                          _ScriptedGen([_mc("1", "题干一", 3)])):
            _run(self.am.start_adaptive_test(
                _goal(), _ctx(), llm=None, student_id=self.sid))

        async def scenario():
            await asyncio.gather(
                self.am.record_cat_answer(self.sid, answer="A"),
                self.am.record_cat_answer(self.sid, answer="A"),
            )

        _run(scenario())
        disk = _disk_json(self.sid)
        self.assertEqual(len(disk["results"]), 1)
        self.assertEqual(disk["results"][0]["verdict"], "correct")
        # M2 事件同样只写一条 quiz_graded（同一证据不能重复影响 M2）。
        events_path = Path(session_store.session_path(self.sid)).parent / f"{self.sid}.events.jsonl"
        if events_path.exists():
            graded = [json.loads(line) for line in
                      events_path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            self.assertEqual(
                sum(1 for e in graded if e.get("type") == "quiz_graded"), 1)


class TestLifecycleAPI(StorageSandboxTestCase):
    """/assessment API x lifecycle（含 GET /assessment/active 恢复端点）。"""

    def setUp(self) -> None:
        super().setUp()
        self.am = get_assessment_manager()
        # API 按身份解析命名空间：建真实用户并携 token，manager 层写入同一 id。
        from app.identity import store as id_store
        from app.identity.models import UserProfile
        from app.identity.security import create_token, hash_password
        user = id_store.create_user(
            email="life-api@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="life-api"),
        )
        self.sid = user.id
        self.headers = {"Authorization": f"Bearer {create_token(user.id)}"}
        self._client = TestClient(create_app())
        self._gen_patch = patch.object(
            assessment_api, "get_llm", lambda: object())
        self._gen_patch.start()
        self.addCleanup(self._gen_patch.stop)

    def _start_via_manager(self, questions):
        gen = _ScriptedGen(questions)
        patcher = patch.object(self.am, "_gen_for_session", gen)
        patcher.start()
        self.addCleanup(patcher.stop)
        _run(self.am.start_adaptive_test(
            _goal(), _ctx(), llm=None, student_id=self.sid))
        return gen

    def test_active_endpoint_three_states(self):
        # 无会话 → none
        r = self._client.get("/api/v1/assessment/active", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "none")
        # 进行中 → 当前题公开内容（不含答案/解析）+ 进度
        self._start_via_manager([_mc("1", "题干一", 3)])
        r = self._client.get("/api/v1/assessment/active", headers=self.headers)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["session_status"], "active")
        self.assertEqual(body["answered"], 0)
        self.assertTrue(body["assessment_id"].startswith("asmt_"))
        self.assertIn("stem", body["question"])
        self.assertNotIn("answer", body["question"])
        self.assertNotIn("explanation", body["question"])
        # 已终止 → 终态与 stop_reason（从盘上读，不从响应推算）
        with patch.object(self.am, "_gen_for_session",
                          _ScriptedGen([_mc("2", "题干二", 4)])):
            _run(self.am.record_cat_answer(self.sid, answer="A"))
            _run(self.am.next_question(self.sid, llm=None))
            _run(self.am.record_cat_answer(self.sid, answer="A"))
        r = self._client.get("/api/v1/assessment/active", headers=self.headers)
        body = r.json()
        self.assertEqual(body["session_status"], "mastered")
        self.assertEqual(body["stop_reason"], "mastered")
        self.assertIsNone(body["question"])
        self.assertEqual(body["summary"]["status"], "mastered")

    def test_answer_response_reads_persisted_stop_state(self):
        self._start_via_manager([_mc("1", "题干一", 3), _mc("2", "题干二", 4)])
        r = self._client.post("/api/v1/assessment/answer",
                              json={"student_answer": "A"}, headers=self.headers)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["stop_reason"], "")   # 第一答不触发停止
        r = self._client.post("/api/v1/assessment/next", json={}, headers=self.headers)
        self.assertEqual(r.json()["status"], "ok")
        r = self._client.post("/api/v1/assessment/answer",
                              json={"student_answer": "A"}, headers=self.headers)
        body = r.json()
        self.assertEqual(body["stop_reason"], "mastered")
        self.assertEqual(body["summary"]["stop_reason"], "mastered")
        # 盘上与响应一致（重启/刷新后仍一致）
        self.assertEqual(_disk_json(self.sid)["status"], "mastered")
        # 停止后重复 answer：幂等重放，不再报 no_active_question
        r2 = self._client.post("/api/v1/assessment/answer",
                               json={"student_answer": "A"}, headers=self.headers)
        body2 = r2.json()
        self.assertEqual(body2["status"], "ok")
        self.assertEqual(body2["result"]["verdict"], "correct")
        self.assertEqual(body2["stop_reason"], "mastered")

    def test_start_and_next_expose_assessment_id_and_public_question(self):
        self._start_via_manager(
            [_mc("1", "题干一", 3), _mc("2", "题干二", 3), _mc("3", "题干三", 3)])
        r = self._client.post("/api/v1/assessment/start",
                              json={"concept": "条件概率"}, headers=self.headers)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["assessment_id"].startswith("asmt_"))
        # start 响应题面为公开内容（不含答案/解析）
        self.assertNotIn("answer", body["question"])
        self.assertNotIn("explanation", body["question"])
        cur_id = body["question"]["id"]
        r = self._client.post("/api/v1/assessment/next", json={}, headers=self.headers)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["question"]["id"], cur_id)  # 未答重发当前题
        self.assertNotIn("answer", body["question"])
        self.assertNotIn("explanation", body["question"])

    def test_abandon_endpoint_persists_state(self):
        self._start_via_manager([_mc("1", "题干一", 3)])
        r = self._client.post("/api/v1/assessment/abandon", json={}, headers=self.headers)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(_disk_json(self.sid)["status"], "abandoned")
        report = self._client.get("/api/v1/assessment/report", headers=self.headers)
        self.assertEqual(report.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
