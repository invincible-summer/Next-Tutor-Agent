"""End-to-end integration: a full Supervisor turn fuses the Student Model.

Uses a fake LLM so no network is needed (mirrors how the V2 Supervisor tests
work). Verifies the three V3 touchpoints in the live turn path:
  1. derive_snapshot() fills the V3 snapshot fields from the Student Model.
  2. _adapt_for_turn() injects a student-aware strategy note into messages.
  3. _collect_turn_events() + record_events() persists a CONCEPT_TAUGHT event
     so the next turn sees a richer student state.
Plus the quiz/grade loop helper updates mastery when a verdict lands.
"""
import asyncio
import os
import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.state import TaskType, TaskUnderstanding
from app.agents.student_model import StudentModel, record_quiz_result


class FakeLLM:
    """Minimal async LLM stand-in implementing stream()/complete().

    stream() yields one answer chunk then done. complete() returns a canned
    JSON for the understanding/planner prompts. No tools, no thinking."""
    def __init__(self, answer: str = "讲解完成。", complete_json: str = ""):
        self.answer = answer
        self.complete_json = complete_json

    async def stream(self, messages, tools=None, temperature=None, max_tokens=None):
        yield {"kind": "answer", "delta": self.answer}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return self.complete_json, {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class TestSupervisorFusion(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.sid = "student_e2e_" + os.urandom(3).hex()
        # seed the model with a known weak concept BEFORE the turn
        from app.agents.student_model import manager as smgr
        sm = StudentModel(self.sid).load()
        sm.record_quiz_result(concept="牛顿第二定律", correct=False, subject="物理", note="力当成速度")
        sm.record_quiz_result(concept="牛顿第二定律", correct=False, subject="物理", note="漏单位")
        smgr._CACHE["student_default"] = sm  # default-student path used by supervisor
        # but supervisor uses DEFAULT_STUDENT_ID; alias it
        from app.agents.student_model.store import DEFAULT_STUDENT_ID
        smgr._CACHE[DEFAULT_STUDENT_ID] = sm

    def test_snapshot_fills_v3_fields(self):
        from app.agents.supervisor import derive_snapshot
        from app.core.session import TutorSession
        session = TutorSession(grade="高中")
        snap = derive_snapshot(session)
        # the weak newton_second should appear in weak_skills (V3 field)
        self.assertIn("physics.dynamics.newton_second", snap.weak_skills)
        self.assertEqual(snap.grade, "高中")

    def test_adaptation_injects_review_first_for_unmet_prereq(self):
        from app.agents.supervisor import _adapt_for_turn
        from app.core.trace import Trace
        from app.core.session import TutorSession
        from app.agents.student_model import get_student_model
        # make newton_second the lone weak prereq of buoyancy by mastering
        # its siblings (velocity + gravity), then adapt.
        sm = get_student_model()
        sm.mastery.record_observation("physics.kinematics.velocity", True)
        sm.mastery.record_observation("physics.kinematics.velocity", True)
        sm.mastery.record_observation("physics.mechanics.gravity", True)
        sm.mastery.record_observation("physics.mechanics.gravity", True)
        sm._persist()
        session = TutorSession(grade="高中")
        u = TaskUnderstanding(intent=TaskType.EXPLAIN, concept="浮力", subject="物理")
        # W3/D08: _adapt_for_turn is async now (bounded LLM teaching decision
        # rides after the rules path); rules mode changes nothing here.
        import asyncio as _aio
        strat, recap = _aio.run(_adapt_for_turn(u, derive_snapshot_lite(),
                                                session, Trace()))
        self.assertIsNotNone(strat)
        # buoyancy's lone weak prereq is now newton_second -> review_first
        self.assertTrue(any("牛顿" in n.name for n in strat.review_first), [n.name for n in strat.review_first])
        self.assertIn("学生智能", recap)

    def test_full_turn_records_concept_taught_event(self):
        from app.agents.supervisor import run
        from app.core.session import TutorSession
        from app.agents.student_model import get_student_model
        session = TutorSession(grade="高中")
        session.session_id = "sess_e2e_" + os.urandom(3).hex()
        events = []
        async def go():
            async for ev in run("讲一下浮力", session, tools=[], llm=FakeLLM(),
                                lang="zh", output_language=None):
                events.append(ev)
        asyncio.run(go())
        # a CONCEPT_TAUGHT event for 浮力 should now be persisted
        sm = get_student_model()
        sm.load()
        found = any(rec.concept and "浮力" in rec.concept
                    for rec in sm.memory.values())
        self.assertTrue(found, "concept_taught for 浮力 must be recorded")
        # the turn emitted a done event
        self.assertTrue(any(e.get("type") == "done" for e in events))

    def test_quiz_result_helper_updates_mastery(self):
        from app.agents.student_model import get_student_model
        before = get_student_model().load().mastery_view()
        # simulate a graded wrong answer via the endpoint helper
        record_quiz_result(concept="摩擦力", correct=False, session_id="sess_x",
                           knowledge_point="摩擦力", subject="物理", note="方向判断错")
        after = get_student_model().load().mastery_view()
        node = "physics.dynamics.friction"
        self.assertIn(node, after)
        self.assertLess(after[node]["p_known"], before.get(node, {"p_known": 0.1})["p_known"])


def derive_snapshot_lite():
    # helper to build a minimal snapshot for _adapt_for_turn without importing
    # the full derive path twice
    from app.agents.state import StudentSnapshot
    from app.agents.student_model import get_student_model
    sm = get_student_model()
    snap = StudentSnapshot(grade="高中")
    snap.weak_skills = sm.weak_skills()
    snap.mastery_map = {k: v["p_known"] for k, v in list(sm.mastery_view().items())[:20]}
    return snap


if __name__ == "__main__":
    unittest.main()
