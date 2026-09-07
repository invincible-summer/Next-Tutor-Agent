"""Unit tests for the personalized /student/learning-path assembly.

The old implementation called next_learnable(None) over the whole ontology,
which with a sparse mastery view always returned the same global
difficulty-1 nodes (函数定义域/原子结构...) regardless of the student. The
personalized assembly must instead prioritize: M9 plan > continuation of
recently taught concepts > M9 goal subjects > stage-appropriate foundations,
and must leave attempted-but-unmastered skills to the review list.

M9 state and the teaching log are mocked at their module boundaries so the
tests stay disk-free; the SkillGraph is the real M5-merged one.

P6-A2：考纲 seed 已删——图谱由每测试独立播种的教材图谱 fixture 提供
（tmp _CUSTOM_DIR，含 高中 数学链 函数→导数→积分 + 一个 小学 根节点）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.agents.knowledge import store as kn_store
from app.agents.student_model.manager import StudentModel
from app.api.v1.student import _personalized_next


def _orch(plan=None, subjects=None):
    return SimpleNamespace(plan=plan or [],
                           goal=SimpleNamespace(subjects=subjects or []))


def _seed_lp_graph(sid: str) -> None:
    """高中 数学 函数→导数→积分 链 + 小学/本科 根节点（验证学段过滤）。"""
    payload = {
        "topic": "数学教材", "topic_key": "tb-lp", "subject": "数学",
        "level": "高中", "source": "textbook:f-lp",
        "nodes": [
            {"id": "lp.math.func", "name": "函数", "subject": "数学",
             "level": "高中", "difficulty": 1, "kind": "concept"},
            {"id": "lp.math.deriv", "name": "导数", "subject": "数学",
             "level": "高中", "difficulty": 3, "kind": "concept"},
            {"id": "lp.math.integ", "name": "积分", "subject": "数学",
             "level": "高中", "difficulty": 4, "kind": "concept"},
            {"id": "lp.primary.add", "name": "加法", "subject": "数学",
             "level": "小学", "difficulty": 1, "kind": "concept"},
            {"id": "lp.under.la", "name": "线性代数", "subject": "数学",
             "level": "本科", "difficulty": 2, "kind": "concept"},
        ],
        "edges": [
            {"source": "lp.math.func", "target": "lp.math.deriv",
             "type": "prerequisite"},
            {"source": "lp.math.deriv", "target": "lp.math.integ",
             "type": "prerequisite"},
        ],
        "contents": [],
    }
    kn_store.save_custom_graph(sid, "tb-lp", payload)


def _root_id(sm: StudentModel, *, level: str = "", subject: str = "") -> str:
    """A no-prerequisite (always-ready) node id from the merged graph."""
    for n in sm.graph.nodes.values():
        if n.prerequisites:
            continue
        if level and n.level != level:
            continue
        if subject and n.subject != subject:
            continue
        return n.id
    raise AssertionError("no root node found")


class TestPersonalizedNext(unittest.TestCase):
    def setUp(self):
        # fresh model per test: BKT records are mutable state, and the merge
        # is cheap enough that isolation beats sharing
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(kn_store, "_CUSTOM_DIR",
                                        Path(self._tmp.name))
        self._patch.start()
        self._sid = f"test_lp_{self._testMethodName}"
        _seed_lp_graph(self._sid)
        self.sm = StudentModel(self._sid).load()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _run(self, *, orch=None, teaching=None):
        mview = self.sm.mastery_view()
        with mock.patch(
            "app.agents.learning_orchestration.LearningOrchestrationService._load",
            return_value=orch if orch is not None else _orch(),
        ), mock.patch(
            "app.agents.teaching_engine.teaching_log.load_teaching_log",
            return_value=teaching or {},
        ):
            return _personalized_next(self.sm, "test_lp_personalized", mview,
                                      limit=6)

    def test_fallback_is_stage_appropriate_not_global(self):
        out = self._run()
        self.assertTrue(out)
        self.assertTrue(all("reason" in n and n["reason"] for n in out))
        base = [n for n in out if n["reason"].endswith("基础")]
        self.assertTrue(base, "fallback suggestions should carry 基础 reason")
        # 默认学段为本科（StudentProfile 缺省）：fallback 池只含 本科/无学段 节点
        for n in base:
            lv = self.sm.graph.nodes[n["skill_id"]].level
            self.assertIn(lv, ("", "本科"),
                          f"{n['name']} level {lv} is not stage-appropriate")
        # the old static behaviour: global difficulty-1 nodes of ANY stage
        self.assertFalse(any(self.sm.graph.nodes[n["skill_id"]].level == "小学"
                             for n in base))

    def test_auto_grade_falls_back_to_undergraduate(self):
        # 学段「自动」（空串）：按本科过滤——stage 池只含 本科/无学段 节点，
        # reason 为「本科基础」；高中/小学根只可能以「拓展学习」兜底出现。
        self.sm.profile.grade = ""
        out = self._run()
        base = [n for n in out if n["reason"] == "本科基础"]
        self.assertTrue(base, "auto grade should recommend 本科 foundations")
        for n in base:
            lv = self.sm.graph.nodes[n["skill_id"]].level
            self.assertIn(lv, ("", "本科"),
                          f"{n['name']} level {lv} is not 本科-appropriate")
        self.assertIn("lp.under.la", [n["skill_id"] for n in base])

    def test_plan_concepts_come_first(self):
        rid = _root_id(self.sm, level="高中")
        plan = [SimpleNamespace(week_index=0, focus="专项突破",
                                concepts=[SimpleNamespace(concept_id=rid)])]
        out = self._run(orch=_orch(plan=plan))
        self.assertEqual(out[0]["skill_id"], rid)
        self.assertEqual(out[0]["reason"], "学习计划·专项突破")

    def test_goal_subjects_ranked_before_fallback(self):
        out = self._run(orch=_orch(subjects=["数学"]))
        goal_hits = [n for n in out if n["reason"].startswith("目标学科")]
        self.assertTrue(goal_hits)
        self.assertTrue(all(self.sm.graph.nodes[n["skill_id"]].subject == "数学"
                            for n in goal_hits))
        # goal-subject suggestions rank ahead of the generic stage fallback
        reasons = [n["reason"] for n in out]
        first_base = next((i for i, r in enumerate(reasons)
                           if r.endswith("基础") or r == "拓展学习"), len(reasons))
        first_goal = reasons.index(goal_hits[0]["reason"])
        self.assertLess(first_goal, first_base)

    def test_continuation_from_recent_teaching(self):
        # find a 高中 node X whose descendant D is ready once X is mastered
        sm = self.sm
        pair = None
        for n in sm.graph.nodes.values():
            if n.level != "高中":
                continue
            for did in sm.graph.descendants_of(n.id):
                d = sm.graph.nodes[did]
                if set(d.prerequisites) == {n.id}:
                    pair = (n, d)
                    break
            if pair:
                break
        self.assertIsNotNone(pair, "need a 高中 X->D single-prereq pair")
        x, d = pair
        # master X (BKT: repeated correct observations push p_known past 0.6)
        for _ in range(4):
            sm.mastery.record_observation(x.id, True)
        p = sm.mastery_view().get(x.id, {}).get("p_known", 0.0)
        self.assertGreaterEqual(p, 0.6)
        teaching = {x.id: [SimpleNamespace(ts=100.0)]}
        out = self._run(teaching=teaching)
        cont = [n for n in out if n["reason"] == f"承接「{x.name}」"]
        self.assertTrue(cont, f"expected continuation for {x.name}")
        self.assertEqual(cont[0]["skill_id"], d.id)

    def test_attempted_skill_not_resuggested(self):
        rid = _root_id(self.sm, level="高中")
        # one wrong attempt: p>0 but <0.6 -> review territory, not "next"
        self.sm.mastery.record_observation(rid, False)
        out = self._run(orch=_orch(
            plan=[SimpleNamespace(week_index=0, focus="",
                                  concepts=[SimpleNamespace(concept_id=rid)])]))
        self.assertNotIn(rid, [n["skill_id"] for n in out])

    def test_deterministic(self):
        a = self._run()
        b = self._run()
        self.assertEqual([n["skill_id"] for n in a], [n["skill_id"] for n in b])


if __name__ == "__main__":
    unittest.main()
