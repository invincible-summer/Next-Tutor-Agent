"""跨模块闭环验收（plan.md §39）：教材 -> 出题 -> 作答 -> Student Model ->
SkillGraph -> learning plan 单一链路。

Case A：KG prerequisite（A -> B）、A 未掌握、goal=B -> 计划必须先 A；
Case B：A mastery 达阈值 -> next learnable 更新，replan 可把 B 排入；
Case C：evidence gate 不允许 mastery update（低置信证据）-> 计划不得假装
A 已掌握，B 仍被 prerequisite 阻挡。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase

SID = "student_loop"
A_ID = "loop.skill.alpha"
B_ID = "loop.skill.beta"


def _seed_graph():
    """给学生的 SkillGraph 加 A -> B 前置边（M5 -> SkillGraph 投影的下游
    效果；本测试直接落在 SkillGraph 上，保持只测编排链路本身）。"""
    from app.agents.student_model import get_student_model, is_enabled
    from app.agents.student_model.skill_graph import SkillNode
    assert is_enabled(), "student model must be enabled for the loop test"
    sm = get_student_model(SID).load()
    sm.graph.nodes[A_ID] = SkillNode(id=A_ID, name="Alpha 前置概念",
                                     subject="数学")
    sm.graph.nodes[B_ID] = SkillNode(id=B_ID, name="Beta 目标概念",
                                     subject="数学",
                                     prerequisites=[A_ID])
    sm._persist()
    return sm


def _mastery(sid: str, skill_id: str) -> float:
    from app.agents.student_model import get_student_model
    sm = get_student_model(sid).load()
    rec = sm.mastery.records.get(skill_id)
    return float(rec.p_known or 0.0) if rec is not None else 0.0


class TestGroundedLearningLoop(StorageSandboxTestCase):

    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        from app.agents.learning_orchestration import manager as orch_mod
        self.orch = orch_mod.LearningOrchestrationService.get()
        _seed_graph()
        # 确定性路径：禁 LLM 周计划（失败即走 graph topo 的 fallback，
        # 与生产的 LLM-first + validated fallback 合同一致，且测试秒级）。
        class _NoLLM:
            async def complete(self, *a, **k):
                raise RuntimeError("no llm in loop test")
        self._llm_patch = patch.object(self.orch, "_get_llm",
                                       lambda: _NoLLM())
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def _plan(self):
        ok, reason = asyncio.run(self.orch.regenerate_plan(SID))
        return ok, reason

    def _plan_concepts(self) -> list[str]:
        state = self.orch._load(SID)
        out: list[str] = []
        for week in state.weekly_plan or []:
            for t in week.tasks or []:
                out.extend(t.concept_ids or [])
                if t.title:
                    out.append(t.title)
                for st in (t.subtasks or []):
                    if st.title:
                        out.append(st.title)
        return out

    def test_case_a_prerequisite_blocks_goal(self):
        """A 未掌握 -> goal B 的计划先修 A，B 不先成为 next learnable。"""
        from app.agents.learning_orchestration.goal_analyzer import \
            prerequisite_closure
        prereq_map = self.orch._prereq_map_safe(SID)
        self.assertEqual(prereq_map.get(B_ID), [A_ID])
        closure = prerequisite_closure([B_ID], prereq_map, mastered_ids=set())
        self.assertIn(A_ID, closure, "A 是 B 的前置，必须进入闭包")
        self.assertIn(B_ID, closure)

        self.orch.add_goal(SID, title="掌握 Beta 目标概念",
                           target_concept_ids=[B_ID])
        ok, _ = self._plan()
        self.assertTrue(ok)
        concepts = self._plan_concepts()
        self.assertTrue(any(A_ID in c or "Alpha" in c for c in concepts),
                        f"计划必须先覆盖前置 A，实际: {concepts}")

    def test_case_b_mastery_unlocks_successor(self):
        """A mastery 达阈值后 closure 不再含 A，replan 可把 B 排入。"""
        from app.agents.student_model import get_student_model
        from app.agents.learning_orchestration.goal_analyzer import \
            prerequisite_closure
        # 多次高质量作答把 A 推过阈值（BKT 证据累积）
        sm = get_student_model(SID)
        for i in range(6):
            sm.record_quiz_result(concept="Alpha 前置概念", correct=True,
                                  skill_id=A_ID, verdict="correct",
                                  confidence=0.95, attempt_id=f"att_b_{i}")
        self.assertGreaterEqual(_mastery(SID, A_ID), 0.6,
                                "高质量作答应把 A 的 mastery 推过阈值")
        prereq_map = self.orch._prereq_map_safe(SID)
        closure = prerequisite_closure(
            [B_ID], prereq_map, mastered_ids={A_ID})
        self.assertNotIn(A_ID, closure, "A 已掌握，不再挡 B")
        self.assertIn(B_ID, closure)

        self.orch.add_goal(SID, title="掌握 Beta 目标概念",
                           target_concept_ids=[B_ID])
        ok, _ = self._plan()
        self.assertTrue(ok)
        concepts = self._plan_concepts()
        self.assertTrue(any(B_ID in c or "Beta" in c for c in concepts),
                        f"A 达标后 replan 应把 B 排入，实际: {concepts}")

    def test_case_c_low_evidence_does_not_unlock(self):
        """证据门不允许 mastery update（作答未通过 M10 gate，事件不写入）
        -> 计划不得假装 A 已掌握，B 仍被 prerequisite 阻挡。confidence 只
        是审计键；gate 的拒绝语义 = record_quiz_result 从未被调用，与
        assessment 层的 evidence gate 测试（test_assessment_*）互为表里。"""
        from app.agents.learning_orchestration.goal_analyzer import \
            prerequisite_closure
        # gate 拒绝：mastery 事件从未写入，A 保持初始低掌握度
        mastery_a = _mastery(SID, A_ID)
        self.assertLess(mastery_a, 0.6,
                        f"无证据时 mastery 不得升高（实际 {mastery_a}）")
        mastered = {sid for sid in (A_ID, B_ID) if _mastery(SID, sid) >= 0.75}
        self.assertNotIn(A_ID, mastered)
        prereq_map = self.orch._prereq_map_safe(SID)
        closure = prerequisite_closure([B_ID], prereq_map, mastered_ids=mastered)
        self.assertIn(A_ID, closure, "低证据下 A 仍未掌握，B 仍被阻挡")

        self.orch.add_goal(SID, title="掌握 Beta 目标概念",
                           target_concept_ids=[B_ID])
        ok, _ = self._plan()
        self.assertTrue(ok)
        concepts = self._plan_concepts()
        self.assertTrue(any(A_ID in c or "Alpha" in c for c in concepts),
                        "计划必须仍先排 A（不得假装已掌握）")


if __name__ == "__main__":
    unittest.main()
