"""W3/D08 教学决策适配回归（updatePlan.md §7.2 D08、§8.4、§13.6）。

- 触发门：评估完成/学生新约束/连续困惑才调用（预算纪律）；
- validate_decision：动作/帮助级别白名单、target 候选集、显式约束禁 quiz、
  单一主要行动（schema 即单 action 字段）；
- rules（默认）零调用；shadow 记录对照不改策略；active 受限调整
  （mode/assistance/plan_hints/decision_id，绝不换 review_first）；
- 任何失败 → 规则策略原样（关闭新模型仍能帮助但不污染状态）。

纯内存用例。
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

from app.agents.state import TaskType, TaskUnderstanding  # noqa: E402
from app.agents.teaching_engine.decision_adapter import (  # noqa: E402
    TeachingDecision, apply_decision, candidate_concepts, decide_teaching,
    should_decide, validate_decision)
from app.agents.teaching_engine.policy import TeachingStrategy  # noqa: E402
from app.agents.teaching_engine.state import TeachingContext  # noqa: E402
from app.core.config import settings  # noqa: E402


def _ctx():
    return TeachingContext(concept="条件概率", subject="数学", grade="本科",
                           evaluation_context={"state": "emerging"}, concept_key="math.prob.cond",
                           misconceptions=[], mistakes=["分母算错", "条件读错"])


def _strat():
    return TeachingStrategy(target_concept="条件概率", rationale="规则策略",
                            review_first=["旧的复习节点"])


class _Trace:
    def __init__(self):
        self.entries: list[tuple] = []

    def log(self, event, **kw):
        self.entries.append((event, kw))


class _QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, **kw):
        self.calls += 1
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"total_tokens": 1}


def _decision_json(action="practice", target="条件概率",
                   assistance="key_hints"):
    return json.dumps({"action": action, "target": target,
                       "assistance": assistance,
                       "rationale": "上次判定 partial，先补关键步骤",
                       "expected_observation": "能独立写出归一化分母",
                       "stop_condition": "连续两次独立答对"}, ensure_ascii=False)


class TestTriggerGate(unittest.TestCase):

    def test_fires_on_assessment_constraint_or_confusion(self):
        self.assertTrue(should_decide(recent_outcome="correct"))
        self.assertTrue(should_decide(
            constraints={"response_format": "one_sentence"}))
        self.assertTrue(should_decide(
            constraints={"allow_followup_assessment": False}))
        self.assertTrue(should_decide(misconceptions=1))
        self.assertTrue(should_decide(recent_mistakes=2))

    def test_silent_otherwise(self):
        self.assertFalse(should_decide(recent_outcome="engaged"))
        self.assertFalse(should_decide(constraints={}))
        self.assertFalse(should_decide(recent_mistakes=1))


class TestValidation(unittest.TestCase):

    def test_whitelists_and_candidates(self):
        d = TeachingDecision(action="teleport", target="任意概念",
                             assistance="magic")
        errors = validate_decision(d, ["条件概率"])
        self.assertIn("action_not_whitelisted", errors)
        self.assertIn("assistance_not_whitelisted", errors)
        self.assertIn("target_not_in_candidates", errors)

    def test_explicit_constraint_forbids_quiz(self):
        d = TeachingDecision(action="quiz", target="条件概率",
                             assistance="independent")
        self.assertIn("quiz_forbidden_by_constraint",
                      validate_decision(d, ["条件概率"], allow_quiz=False))
        self.assertEqual(validate_decision(d, ["条件概率"], allow_quiz=True), [])

    def test_none_is_invalid(self):
        self.assertEqual(validate_decision(None, []), ["no_decision"])

    def test_candidate_set_from_ctx(self):
        ctx = _ctx()
        ctx.unmet_prereq_names = ["古典概型"]
        self.assertEqual(candidate_concepts(ctx),
                         ["条件概率", "math.prob.cond", "古典概型"])


class TestDecideAndApply(unittest.TestCase):

    def test_valid_decision_passes(self):
        llm = _QueueLLM([_decision_json()])
        d = asyncio.run(decide_teaching(_ctx(), _strat(), llm=llm,
                                        recent_outcome="partial"))
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "practice")
        self.assertTrue(d.decision_id.startswith("td_"))
        self.assertIn("关键步骤", d.rationale)

    def test_bad_enum_or_target_rejected(self):
        llm = _QueueLLM([_decision_json(action="teleport")])
        self.assertIsNone(asyncio.run(decide_teaching(
            _ctx(), _strat(), llm=llm, recent_outcome="partial")))
        llm2 = _QueueLLM([_decision_json(target="不在候选集里的概念")])
        self.assertIsNone(asyncio.run(decide_teaching(
            _ctx(), _strat(), llm=llm2, recent_outcome="partial")))

    def test_quiz_blocked_when_constraint(self):
        llm = _QueueLLM([_decision_json(action="quiz")])
        self.assertIsNone(asyncio.run(decide_teaching(
            _ctx(), _strat(), llm=llm, recent_outcome="partial",
            constraints={"allow_followup_assessment": False})))

    def test_apply_is_constrained(self):
        d = TeachingDecision(action="practice", target="条件概率",
                             assistance="key_hints", rationale="补关键步骤",
                             decision_id="td_test")
        strat = _strat()
        apply_decision(strat, d)
        self.assertEqual(strat.mode.value, "practice")
        self.assertEqual(strat.assistance, "key_hints")
        self.assertEqual(strat.decision_id, "td_test")
        self.assertTrue(any("关键步骤提示" in h for h in strat.plan_hints))
        self.assertIn("td_test", strat.rationale)
        self.assertEqual(strat.review_first, ["旧的复习节点"])  # 结构不动


class TestSupervisorHook(unittest.TestCase):

    def _understanding(self):
        return TaskUnderstanding(intent=TaskType.EXPLAIN, concept="条件概率",
                                 source="llm")

    def test_rules_mode_never_calls(self):
        from app.agents.supervisor import _apply_teaching_decision
        llm = _QueueLLM([])
        with patch.object(settings, "teaching_decision_mode", "rules"):
            asyncio.run(_apply_teaching_decision(
                _ctx(), _strat(), self._understanding(), "correct",
                _Trace(), llm))
        self.assertEqual(llm.calls, 0)

    def test_shadow_records_without_applying(self):
        from app.agents.supervisor import _apply_teaching_decision
        llm = _QueueLLM([_decision_json()])
        strat = _strat()
        trace = _Trace()
        with patch.object(settings, "teaching_decision_mode", "shadow"):
            asyncio.run(_apply_teaching_decision(
                _ctx(), strat, self._understanding(), "correct",
                trace, llm))
        self.assertEqual(llm.calls, 1)
        self.assertEqual(strat.assistance, "")  # 未应用
        logged = [e for e in trace.entries if e[0] == "teaching_decision"]
        self.assertTrue(logged and logged[0][1]["outcome"] == "recorded_only")

    def test_active_applies_validated_decision(self):
        from app.agents.supervisor import _apply_teaching_decision
        llm = _QueueLLM([_decision_json()])
        strat = _strat()
        trace = _Trace()
        with patch.object(settings, "teaching_decision_mode", "active"):
            asyncio.run(_apply_teaching_decision(
                _ctx(), strat, self._understanding(), "correct",
                trace, llm))
        self.assertEqual(strat.assistance, "key_hints")
        logged = [e for e in trace.entries if e[0] == "teaching_decision"]
        self.assertTrue(logged and logged[0][1]["outcome"] == "applied")

    def test_no_trigger_no_call_even_in_active(self):
        from app.agents.supervisor import _apply_teaching_decision
        quiet_ctx = _ctx()
        quiet_ctx.mistakes = []          # 无连续困惑
        quiet_ctx.misconceptions = []
        llm = _QueueLLM([])
        with patch.object(settings, "teaching_decision_mode", "active"):
            asyncio.run(_apply_teaching_decision(
                quiet_ctx, _strat(), self._understanding(), "engaged",
                _Trace(), llm))
        self.assertEqual(llm.calls, 0)


if __name__ == "__main__":
    unittest.main()
