"""M10 Skill Runtime contracts, routing decisions and prompt projection."""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase
from app.agents.skill_runtime import (EvidenceLevel, LearningEvidence,
                                      build_task_frame, decide,
                                      evaluate_learning_evidence, registry)
from app.agents.skill_runtime.policy import evaluate_preconditions
from app.agents.skill_runtime.registry import capability_tool_map
from app.agents.skill_runtime.runtime import SkillRuntime
from app.agents.state import (PlanStep, StudentSnapshot, TaskPlan, TaskType,
                              TaskUnderstanding)
from app.core.tool_base import Tool
from app.core.tool_protocol import ok
from app.prompts.tutor import skill_cards_preamble


class _Tool(Tool):
    def __init__(self, name: str):
        self.name = name
        self.description = name
        self.parameters = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        return ok(self.name)


def _understanding(intent: TaskType, *, concept: str = "浮力",
                   requires_tools: bool = False) -> TaskUnderstanding:
    return TaskUnderstanding(intent=intent, subject="物理", concept=concept,
                             requires_tools=requires_tools, confidence=0.8)


class TestSkillRegistry(unittest.TestCase):
    def test_builtin_registry_is_valid_and_versioned(self):
        versions = registry.active_versions()
        expected = {
            "agent.skill.teaching.direct_explain",
            "agent.skill.knowledge.search_materials",
            "agent.skill.assessment.generate_practice",
            "agent.skill.assessment.fit_variants",
            "agent.skill.memory.recall_history",
        }
        self.assertTrue(expected.issubset(versions))
        registry.validate_references()

    def test_capability_projection_matches_current_tools(self):
        caps = capability_tool_map()
        self.assertEqual(caps["knowledge"], {"knowledge_search"})
        self.assertEqual(caps["assessment"], {"generate_quiz", "fit_quiz"})
        self.assertEqual(caps["memory"], {"recall_history"})
        self.assertEqual(caps["teaching"], set())

    def test_material_precondition_is_deterministic(self):
        skill = registry.get("agent.skill.knowledge.search_materials")
        denied = evaluate_preconditions(skill, {"has_materials": False})
        allowed = evaluate_preconditions(skill, {"has_materials": True})
        self.assertFalse(denied.allowed)
        self.assertIn("materials_available", denied.failed)
        self.assertTrue(allowed.allowed)


class TestSkillDecision(unittest.TestCase):
    def test_plain_explanation_selects_direct_teaching(self):
        frame = build_task_frame(
            "讲一下浮力", _understanding(TaskType.EXPLAIN),
            StudentSnapshot(grade="高中"),
        )
        decision = decide(frame)
        self.assertEqual(decision.mode, "execute")
        self.assertEqual(decision.selected_skill_ids,
                         ("agent.skill.teaching.direct_explain",))

    def test_explicit_material_request_selects_grounded_search(self):
        frame = build_task_frame(
            "根据我上传的物理笔记解释浮力", _understanding(TaskType.EXPLAIN),
            StudentSnapshot(grade="高中", has_materials=True, material_count=1),
        )
        decision = decide(frame)
        self.assertEqual(decision.mode, "execute")
        self.assertEqual(decision.selected_skill_ids,
                         ("agent.skill.knowledge.search_materials",))

    def test_workspace_textbook_content_question_selects_grounding_and_teaching(self):
        from app.agents.skill_runtime.decision import gate_plan
        frame = build_task_frame(
            "洛伦兹变化是什么", _understanding(TaskType.EXPLAIN, concept="洛伦兹变换"),
            StudentSnapshot(grade="本科", has_materials=True, material_count=3),
            has_textbook=True,
        )
        decision = decide(frame)
        knowledge = "agent.skill.knowledge.search_materials"
        teaching = "agent.skill.teaching.direct_explain"
        self.assertTrue(frame.material_grounding_required)
        self.assertIn(knowledge, decision.selected_skill_ids)
        self.assertIn(teaching, decision.selected_skill_ids)
        gated = gate_plan(TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="解释概念", skill_ids=[teaching])]), frame, decision)
        self.assertEqual(gated.plan.steps[0].agent_role, "knowledge")
        self.assertEqual(gated.plan.steps[0].skill_ids, [knowledge])

    def test_variant_without_reference_requests_clarification(self):
        frame = build_task_frame(
            "仿照这道题再出两道类似题", _understanding(TaskType.PRACTICE,
                                                   requires_tools=True),
            StudentSnapshot(grade="初中"),
        )
        decision = decide(frame)
        self.assertEqual(decision.mode, "clarify")
        self.assertEqual(decision.clarification_reason,
                         "missing_reference_question")
        fit = next(c for c in decision.candidates
                   if c.skill_id == "agent.skill.assessment.fit_variants")
        self.assertFalse(fit.allowed)
        self.assertIn("reference_question_available", fit.failed_preconditions)

    def test_variant_attachment_selects_fit_skill(self):
        frame = build_task_frame(
            "仿照这道题再出两道类似题", _understanding(TaskType.PRACTICE,
                                                   requires_tools=True),
            StudentSnapshot(grade="初中"), has_attachments=True,
        )
        decision = decide(frame)
        self.assertEqual(decision.selected_skill_ids,
                         ("agent.skill.assessment.fit_variants",))

    def test_chitchat_never_selects_skill(self):
        frame = build_task_frame(
            "你好", _understanding(TaskType.CHITCHAT, concept=""),
            StudentSnapshot(grade="高中"),
        )
        decision = decide(frame)
        self.assertEqual(decision.mode, "direct")
        self.assertFalse(decision.selected_skill_ids)


class TestLearningEvidenceGate(unittest.TestCase):
    def test_exposure_never_updates_mastery(self):
        result = evaluate_learning_evidence(LearningEvidence(
            learning_skill_id="physics.mechanics.buoyancy",
            level=EvidenceLevel.EXPOSURE, source="assistant_explanation",
            confidence=0.95, student_action=False,
        ))
        self.assertFalse(result.allow_mastery_update)
        self.assertEqual(result.reason_code, "no_student_action")

    def test_self_report_is_not_performance_evidence(self):
        result = evaluate_learning_evidence(LearningEvidence(
            learning_skill_id="physics.mechanics.buoyancy",
            level=EvidenceLevel.SELF_REPORT, source="student_message",
            confidence=0.9, student_action=True,
        ))
        self.assertFalse(result.allow_mastery_update)
        self.assertLessEqual(result.max_confidence, 0.25)

    def test_variant_solution_can_update_mastery(self):
        result = evaluate_learning_evidence(LearningEvidence(
            learning_skill_id="physics.mechanics.buoyancy",
            level=EvidenceLevel.VARIANT_TASK, source="graded_variant",
            confidence=0.88, student_action=True, question_id="q1",
        ))
        self.assertTrue(result.allow_mastery_update)
        self.assertEqual(result.reason_code, "performance_evidence_valid")

    def test_unverified_question_caps_grading_confidence(self):
        # W2/A05：题目内容未过 critic 独立重解（basic/off/缺失/fail-open）时，
        # 评分置信压到 0.70 上限——缺失不默认高置信，但仍高于 0.60 拒绝线，
        # 不把确定性 MC 判分整个丢弃。
        from app.agents.skill_runtime import assessment_evidence
        ev = assessment_evidence(
            learning_skill_id="physics.mechanics.buoyancy",
            verdict="correct", student_answer="A",
            grading_confidence=1.0, question_verified=None)
        self.assertEqual(ev.confidence, 0.70)
        self.assertIsNone(ev.question_verified)
        result = evaluate_learning_evidence(ev)
        self.assertTrue(result.allow_mastery_update)
        self.assertEqual(result.max_confidence, 0.70)

    def test_verified_question_keeps_grading_confidence(self):
        from app.agents.skill_runtime import assessment_evidence
        ev = assessment_evidence(
            learning_skill_id="physics.mechanics.buoyancy",
            verdict="correct", student_answer="A",
            grading_confidence=1.0, question_verified=True)
        self.assertEqual(ev.confidence, 1.0)
        result = evaluate_learning_evidence(ev)
        self.assertTrue(result.allow_mastery_update)
        # SAME_FORM 层级 cap 依旧生效：题目已验证不等于独立证据。
        self.assertEqual(result.max_confidence, 0.75)



class TestSkillRuntimeIntegration(unittest.TestCase):
    def test_router_prefers_skill_ids_over_legacy_tool_names(self):
        from app.agents.router import route_full_plan
        tools = [_Tool("knowledge_search"), _Tool("generate_quiz"), _Tool("fit_quiz")]
        plan = TaskPlan(steps=[PlanStep(
            agent_role="assessment", task="出普通练习",
            suggested_tools=["fit_quiz"],
            skill_ids=["agent.skill.assessment.generate_practice"],
        )])
        visible = route_full_plan(plan, tools)
        self.assertEqual([t.name for t in visible], ["generate_quiz"])

    def test_postcondition_validation(self):
        runtime = SkillRuntime([_Tool("knowledge_search"), _Tool("generate_quiz")])
        import hashlib
        excerpt = "浮力是流体向上的托力。"
        row = {"evidence_excerpt": excerpt, "confidence": 0.8,
               "context_hash": hashlib.sha256(excerpt.encode()).hexdigest(),
               "source_visibility": "session_private"}
        good = runtime.validate_result(
            "knowledge_search",
            ok("knowledge_search",
               data={"count": 1, "evidence_bundle": {"selected": [row],
                                                    "context_hashes": [row["context_hash"]]}},
               text=f"<material_excerpt>{excerpt}</material_excerpt>"),
        )
        self.assertIsNotNone(good)
        self.assertTrue(good.valid)
        bad = runtime.validate_result(
            "generate_quiz", ok("generate_quiz", data={"questions": []}),
        )
        self.assertFalse(bad.valid)
        self.assertIn("questions_present", bad.failed)

    def test_prompt_injects_only_selected_cards(self):
        text = skill_cards_preamble([
            "agent.skill.knowledge.search_materials",
        ])
        self.assertIn("agent.skill.knowledge.search_materials", text)
        self.assertIn("materials_available", text)
        self.assertNotIn("agent.skill.assessment.generate_practice", text)


class TestSkillGatedPlan(unittest.TestCase):
    def test_normal_practice_narrows_generate_not_fit(self):
        from app.agents.planner import _rule_plan
        from app.agents.skill_runtime import gate_plan
        snap = StudentSnapshot(grade="初中")
        understanding = _understanding(TaskType.PRACTICE, requires_tools=True)
        frame = build_task_frame("出两道浮力练习题", understanding, snap)
        gated = gate_plan(_rule_plan(understanding, snap), frame, decide(frame))
        ids = [sid for step in gated.plan.steps for sid in step.skill_ids]
        self.assertIn("agent.skill.assessment.generate_practice", ids)
        self.assertNotIn("agent.skill.assessment.fit_variants", ids)

    def test_missing_reference_becomes_clarification_plan(self):
        from app.agents.planner import _rule_plan
        from app.agents.skill_runtime import gate_plan
        snap = StudentSnapshot(grade="初中")
        understanding = _understanding(TaskType.PRACTICE, requires_tools=True)
        frame = build_task_frame("仿照这道题出两道类似题", understanding, snap)
        gated = gate_plan(_rule_plan(understanding, snap), frame, decide(frame))
        self.assertEqual(gated.clarification_reason, "missing_reference_question")
        self.assertEqual(gated.plan.source, "skill_gated_clarify")
        self.assertIn("粘贴或上传完整参考题", gated.plan.steps[0].task)
        self.assertEqual(gated.plan.steps[0].agent_role, "teaching")

    def test_diagnose_without_history_drops_recall_step(self):
        from app.agents.planner import _rule_plan
        from app.agents.skill_runtime import gate_plan
        snap = StudentSnapshot(grade="高中")
        understanding = _understanding(TaskType.DIAGNOSE, requires_tools=True)
        frame = build_task_frame("我为什么总做错浮力题", understanding, snap,
                                 has_history=False)
        gated = gate_plan(_rule_plan(understanding, snap), frame, decide(frame))
        roles = [step.agent_role for step in gated.plan.steps]
        self.assertNotIn("memory", roles)
        self.assertIn("assessment", roles)

    def test_materials_do_not_force_search_without_reference(self):
        from app.agents.planner import _rule_plan
        from app.agents.skill_runtime import gate_plan
        snap = StudentSnapshot(grade="高中", has_materials=True, material_count=1)
        understanding = _understanding(TaskType.EXPLAIN)
        frame = build_task_frame("讲一下浮力", understanding, snap)
        gated = gate_plan(_rule_plan(understanding, snap), frame, decide(frame))
        self.assertEqual([s.agent_role for s in gated.plan.steps], ["teaching"])


class _SequenceLLM:
    def __init__(self):
        self.calls = 0
        self.tool_names_seen: list[list[str]] = []

    async def stream(self, messages, tools=None, temperature=None, max_tokens=None,
                     disable_thinking=False):
        self.tool_names_seen.append([
            schema.get("function", {}).get("name") for schema in (tools or [])
        ])
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "tool_calls", "calls": [
                {"id": "c1", "name": "recall_history", "args": {"query": "浮力错题"}}
            ]}
            yield {"kind": "done", "finish_reason": "tool_calls", "usage": {}}
        elif self.calls == 2:
            yield {"kind": "tool_calls", "calls": [
                {"id": "c2", "name": "generate_quiz",
                 "args": {"topic": "浮力", "grade": "高中", "count": 1}}
            ]}
            yield {"kind": "done", "finish_reason": "tool_calls", "usage": {}}
        else:
            yield {"kind": "answer", "delta": "请先完成这道诊断题。"}
            yield {"kind": "done", "finish_reason": "stop", "usage": {}}


class _SequenceTool(Tool):
    def __init__(self, name: str):
        self.name = name
        self.description = name
        if name == "recall_history":
            self.parameters = {
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
        else:
            self.parameters = {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"}, "grade": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["topic"],
            }

    async def run(self, **kwargs):
        if self.name == "recall_history":
            return ok(self.name, data={"count": 1},
                      text="<history_excerpt>上次浮沉条件判断错误</history_excerpt>")
        return ok(self.name, data={"questions": [{
            "stem": "诊断题", "explanation": "用于判断浮力概念掌握情况。"
        }]})


class _TraceStub:
    run_id = "skill_step_test"

    def __init__(self):
        self.events = []

    def log(self, kind, **data):
        self.events.append((kind, data))

    def llm_call(self, *args, **kwargs):
        pass

    def decision(self, *args, **kwargs):
        pass

    def summary(self):
        return {}


class TestStepwiseSkillExecution(StorageSandboxTestCase):
    def test_gated_executor_exposes_one_plan_step_at_a_time(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        plan = TaskPlan(steps=[
            PlanStep(agent_role="memory", task="回顾错题",
                     skill_ids=["agent.skill.memory.recall_history"]),
            PlanStep(agent_role="assessment", task="生成诊断题",
                     skill_ids=["agent.skill.assessment.generate_practice"]),
        ], source="skill_gated:test")
        llm = _SequenceLLM()
        trace = _TraceStub()
        session = TutorSession(session_id="skill_step_session", grade="高中")
        tools = [_SequenceTool("recall_history"), _SequenceTool("generate_quiz")]

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "诊断我的浮力薄弱点"}],
                session, tools, plan, llm, trace,
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "gated"):
            events = asyncio.run(collect())

        self.assertEqual(llm.tool_names_seen[0], ["recall_history"])
        self.assertEqual(llm.tool_names_seen[1], ["generate_quiz"])
        self.assertEqual(llm.tool_names_seen[2], [])
        self.assertEqual([e["name"] for e in events if e["type"] == "tool_start"],
                         ["recall_history", "generate_quiz"])
        advances = [data for kind, data in trace.events
                    if kind == "skill_plan_advance"]
        self.assertEqual(len(advances), 2)
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("诊断题", events[-1]["answer"])

    def test_gated_executor_skips_advisory_teaching_to_expose_check_skill(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class TeachingThenCheckLLM:
            def __init__(self):
                self.calls = 0
                self.tool_names_seen = []

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                self.tool_names_seen.append([
                    schema.get("function", {}).get("name")
                    for schema in (tools or [])
                ])
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "answer", "delta": "先完成核心概念讲解。"}
                    yield {"kind": "tool_calls", "calls": [{
                        "id": "check1", "name": "generate_quiz",
                        "args": {"topic": "浮力", "grade": "高中", "count": 1},
                    }]}
                    yield {"kind": "done", "finish_reason": "tool_calls", "usage": {}}
                else:
                    yield {"kind": "answer", "delta": "请先完成上面的收尾检测。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        plan = TaskPlan(steps=[
            PlanStep(agent_role="teaching", task="讲解浮力",
                     skill_ids=["agent.skill.teaching.direct_explain"]),
            PlanStep(agent_role="assessment", task="生成一道收尾检测题",
                     skill_ids=["agent.skill.assessment.generate_practice"]),
        ], source="strategy_enriched:test")
        llm = TeachingThenCheckLLM()
        trace = _TraceStub()

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲解浮力"}],
                TutorSession(session_id="advisory_step", grade="高中"),
                [_SequenceTool("generate_quiz")], plan, llm, trace,
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "gated"):
            events = asyncio.run(collect())

        self.assertEqual(llm.tool_names_seen[0], ["generate_quiz"])
        self.assertEqual(llm.tool_names_seen[1], [])
        self.assertTrue(any(kind == "skill_plan_advisory"
                            for kind, _ in trace.events))
        self.assertEqual(events[-1]["type"], "done")


class TestStrategySkillAlignment(StorageSandboxTestCase):
    def test_next_check_enriches_explanation_plan_with_generate_quiz(self):
        from types import SimpleNamespace
        from app.agents.supervisor import _enrich_plan_with_strategy_check

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="讲解浮力",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )], source="rule")
        strategy = SimpleNamespace(
            next_check=SimpleNamespace(concept="浮力", difficulty=2),
            suggested_quiz_difficulty="easy",
        )
        enriched = _enrich_plan_with_strategy_check(
            plan, strategy, _understanding(TaskType.EXPLAIN),
            [_Tool("generate_quiz")], _TraceStub(),
        )
        self.assertEqual(len(enriched.steps), 2)
        self.assertEqual(enriched.steps[-1].skill_ids,
                         ["agent.skill.assessment.generate_practice"])
        self.assertTrue(enriched.steps[-1].auto_invoke)
        self.assertEqual(enriched.steps[-1].tool_args["generate_quiz"]["count"], 1)
        # 难度是建议下限而非钉死：学生当轮明确诉求可以覆盖
        self.assertIn("建议难度", enriched.steps[-1].task)
        self.assertIn("以学生的要求为准", enriched.steps[-1].task)
        cards = skill_cards_preamble([
            sid for step in enriched.steps for sid in step.skill_ids
        ])
        self.assertIn("agent.skill.assessment.generate_practice@1.0.0", cards)

    def test_one_sentence_constraint_suppresses_strategy_check(self):
        from types import SimpleNamespace
        from app.agents.supervisor import _enrich_plan_with_strategy_check

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="讲解惯性",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )], source="rule")
        strategy = SimpleNamespace(
            next_check=SimpleNamespace(concept="惯性", difficulty=1),
            suggested_quiz_difficulty="easy",
        )
        understanding = _understanding(TaskType.EXPLAIN, concept="惯性")
        understanding.response_format = "one_sentence"
        understanding.allow_followup_assessment = False
        unchanged = _enrich_plan_with_strategy_check(
            plan, strategy, understanding, [_Tool("generate_quiz")], _TraceStub())
        self.assertEqual(len(unchanged.steps), 1)
        self.assertEqual(unchanged.steps[0].agent_role, "teaching")

    def test_task_understanding_preserves_explicit_output_contract(self):
        from app.agents.task_understanding import rule_understand
        understanding = rule_understand("请用一句话解释惯性")
        self.assertEqual(understanding.response_format, "one_sentence")
        self.assertFalse(understanding.allow_followup_assessment)
        self.assertEqual(understanding.concept, "惯性")

    def test_no_generate_tool_does_not_advertise_unexecutable_check(self):
        from types import SimpleNamespace
        from app.agents.supervisor import _enrich_plan_with_strategy_check

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="讲解浮力",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )], source="rule")
        strategy = SimpleNamespace(
            next_check=SimpleNamespace(concept="浮力", difficulty=2),
            suggested_quiz_difficulty="easy",
        )
        unchanged = _enrich_plan_with_strategy_check(
            plan, strategy, _understanding(TaskType.EXPLAIN), [], _TraceStub(),
        )
        self.assertEqual(len(unchanged.steps), 1)


class TestEmptyAnswerRecovery(StorageSandboxTestCase):
    def test_reasoning_budget_exhaustion_retries_with_thinking_disabled(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class ReasoningStarvedLLM:
            def __init__(self):
                self.calls = []

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                self.calls.append(disable_thinking)
                if len(self.calls) == 1:
                    yield {"kind": "thinking", "delta": "正在反复分析规则。"}
                    yield {"kind": "answer", "delta": "这是被截断的前半句，"}
                    yield {"kind": "done", "finish_reason": "length", "usage": {}}
                else:
                    yield {"kind": "answer", "delta": "这是被截断的前半句，这里继续完成后半句。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="直接讲解",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )])
        llm = ReasoningStarvedLLM()
        trace = _TraceStub()

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲一下浮力"}],
                TutorSession(session_id="empty_recovery", grade="高中"),
                [], plan, llm, trace,
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            events = asyncio.run(collect())

        self.assertEqual(llm.calls, [False, True])
        self.assertTrue(any(event.get("reason") == "incomplete_answer_after_reasoning"
                            for event in events if event["type"] == "retry"))
        self.assertEqual(events[-1]["answer"], "这是被截断的前半句，这里继续完成后半句。")
        self.assertTrue(any(kind == "incomplete_answer_recovery"
                            for kind, _ in trace.events))

    def test_recovery_prompt_forbids_repeating_previous_turns(self):
        # 恢复重试只约束「本轮前缀不重复」不够：模型会把上一轮历史答案近乎
        # 逐字复读（trace c0ff3bc5008b 实案）。恢复指令必须显式禁止跨轮复述。
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class CapturingLLM:
            def __init__(self):
                self.second_call_messages = None

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                if self.second_call_messages is None and not disable_thinking:
                    yield {"kind": "thinking", "delta": "长时间内部分析。"}
                    yield {"kind": "done", "finish_reason": "length", "usage": {}}
                else:
                    self.second_call_messages = list(messages)
                    yield {"kind": "answer", "delta": "完整答案。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="直接讲解",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )])
        llm = CapturingLLM()

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "要竞赛难度的放缩"}],
                TutorSession(session_id="anti_repeat", grade="高中"),
                [], plan, llm, _TraceStub(),
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            asyncio.run(collect())

        recovery = [m["content"] for m in llm.second_call_messages
                    if m.get("role") == "system" and "输出恢复" in m.get("content", "")]
        self.assertTrue(recovery)
        self.assertIn("不得复述此前轮次", recovery[-1])


    def test_visible_answer_with_private_reasoning_does_not_false_retry(self):
        import asyncio
        from app.agents import executor
        from app.core.session import TutorSession

        class LLM:
            def __init__(self): self.calls = 0
            async def stream(self, messages, tools=None, **kwargs):
                self.calls += 1
                yield {"kind": "thinking", "delta": "简短内部判断"}
                yield {"kind": "answer", "delta": "惯性是物体保持原有运动状态的性质。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        plan = TaskPlan(steps=[PlanStep(
            agent_role="teaching", task="一句话回答",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )])
        llm = LLM(); trace = _TraceStub()
        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "一句话解释惯性"}],
                TutorSession(session_id="no_false_retry"), [], plan, llm, trace)]
        events = asyncio.run(collect())
        self.assertEqual(llm.calls, 1)
        self.assertFalse(any(event.get("type") == "retry" for event in events))
        self.assertEqual(events[-1]["answer"], "惯性是物体保持原有运动状态的性质。")


class TestRequiredPlanFulfillment(StorageSandboxTestCase):
    def test_strategy_check_auto_invokes_when_model_omits_tool_call(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class OmittingLLM:
            def __init__(self):
                self.calls = 0

            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "answer", "delta": "先讲清浮力原理。"}
                else:
                    yield {"kind": "answer", "delta": "请完成上面的检测题。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        plan = TaskPlan(steps=[
            PlanStep(agent_role="teaching", task="讲解浮力",
                     skill_ids=["agent.skill.teaching.direct_explain"]),
            PlanStep(
                agent_role="assessment", task="生成收尾检测",
                skill_ids=["agent.skill.assessment.generate_practice"],
                suggested_tools=["generate_quiz"], auto_invoke=True,
                tool_args={"generate_quiz": {
                    "topic": "浮力", "grade": "高中",
                    "difficulty": "easy", "count": 1,
                }},
            ),
        ], source="strategy_enriched:test_auto")
        trace = _TraceStub()

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "讲解浮力"}],
                TutorSession(session_id="auto_check", grade="高中"),
                [_SequenceTool("generate_quiz")], plan, OmittingLLM(), trace,
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            events = asyncio.run(collect())

        starts = [event for event in events if event["type"] == "tool_start"]
        self.assertEqual([event["name"] for event in starts], ["generate_quiz"])
        self.assertTrue(starts[0]["auto"])
        self.assertEqual(starts[0]["args"]["count"], 1)
        self.assertTrue(any(kind == "skill_plan_auto_invoke"
                            for kind, _ in trace.events))
        self.assertEqual(events[-1]["answer"],
                         "先讲清浮力原理。请完成上面的检测题。")


class TestNativeToolMessages(StorageSandboxTestCase):
    def _plan(self):
        return TaskPlan(steps=[PlanStep(
            agent_role="assessment", task="生成检测题",
            skill_ids=["agent.skill.assessment.generate_practice"],
            auto_invoke=True,
            tool_args={"generate_quiz": {"topic": "浮力", "count": 1}},
        )])

    def test_native_mode_sends_assistant_tool_and_tool_result_pair(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class LLM:
            def __init__(self): self.calls = 0; self.messages_seen = []
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                self.calls += 1; self.messages_seen.append([dict(m) for m in messages])
                if self.calls == 1:
                    yield {"kind": "tool_calls", "calls": [{
                        "id": "call_native_1", "name": "generate_quiz",
                        "args": {"topic": "浮力", "count": 1}}]}
                    yield {"kind": "done", "finish_reason": "tool_calls", "usage": {}}
                else:
                    yield {"kind": "answer", "delta": "请完成检测题。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}
        llm = LLM(); trace = _TraceStub()
        async def collect():
            return [e async for e in executor.execute(
                [{"role": "user", "content": "出题"}],
                TutorSession(session_id="native_pair", grade="高中"),
                [_SequenceTool("generate_quiz")], self._plan(), llm, trace)]
        with patch.object(executor.settings, "tool_message_mode", "native"), \
             patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            events = asyncio.run(collect())
        second = llm.messages_seen[1]
        self.assertEqual(second[-2]["role"], "assistant")
        self.assertEqual(second[-2]["tool_calls"][0]["id"], "call_native_1")
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_call_id"], "call_native_1")
        self.assertEqual(events[-1]["type"], "done")

    def test_provider_400_rolls_native_messages_back_to_legacy(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession

        class Rejected(Exception): status_code = 400
        class LLM:
            def __init__(self): self.calls = 0; self.messages_seen = []
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None, disable_thinking=False):
                self.calls += 1; self.messages_seen.append([dict(m) for m in messages])
                if self.calls == 1:
                    yield {"kind": "tool_calls", "calls": [{
                        "id": "call_reject", "name": "generate_quiz",
                        "args": {"topic": "浮力", "count": 1}}]}
                    yield {"kind": "done", "finish_reason": "tool_calls", "usage": {}}
                elif self.calls == 2:
                    raise Rejected("native rejected")
                    yield  # pragma: no cover
                else:
                    yield {"kind": "answer", "delta": "已回退并完成。"}
                    yield {"kind": "done", "finish_reason": "stop", "usage": {}}
        llm = LLM(); trace = _TraceStub()
        async def collect():
            return [e async for e in executor.execute(
                [{"role": "user", "content": "出题"}],
                TutorSession(session_id="native_fallback", grade="高中"),
                [_SequenceTool("generate_quiz")], self._plan(), llm, trace)]
        with patch.object(executor.settings, "tool_message_mode", "native"), \
             patch.object(executor.settings, "skill_runtime_mode", "shadow"):
            events = asyncio.run(collect())
        third_roles = [m.get("role") for m in llm.messages_seen[2]]
        self.assertNotIn("tool", third_roles)
        self.assertIn("user", third_roles)
        self.assertTrue(any(e.get("reason") == "native_tool_message_fallback"
                            for e in events if e["type"] == "retry"))
        self.assertTrue(any(k == "tool_message_fallback" for k, _ in trace.events))


class TestAssessmentEvidenceAdapter(unittest.TestCase):
    def test_unknown_grade_is_blocked_before_mastery_write(self):
        from unittest.mock import patch
        from app.agents.assessment.manager import AssessmentManager
        from app.agents.assessment.state import AssessmentResult

        result = AssessmentResult(
            question_id="q_unknown", concept="浮力",
            skill_id="physics.mechanics.buoyancy", verdict="unknown", score=0.0,
        )
        with patch("app.agents.student_model.record_quiz_result") as record:
            AssessmentManager()._record(
                result, student_id="student_gate", student_answer="我的答案",
                grading_confidence=0.9, grading_source="assessment_test",
            )
        record.assert_not_called()
        self.assertEqual(result.evidence_level, "SAME_FORM_TASK")
        self.assertFalse(result.evidence_gate["allow_mastery_update"])

    def test_confident_wrong_answer_is_valid_negative_evidence(self):
        from unittest.mock import patch
        from app.agents.assessment.manager import AssessmentManager
        from app.agents.assessment.state import AssessmentResult

        result = AssessmentResult(
            question_id="q_wrong", concept="浮力",
            skill_id="physics.mechanics.buoyancy", verdict="wrong", score=0.0,
            diagnosis_note="混淆浮力和重力",
        )
        with patch("app.agents.student_model.record_quiz_result") as record:
            AssessmentManager()._record(
                result, student_id="student_gate", student_answer="B",
                grading_confidence=1.0,
                grading_source="assessment_multiple_choice",
            )
        record.assert_called_once()
        self.assertFalse(record.call_args.kwargs["correct"])
        self.assertEqual(record.call_args.kwargs["skill_id"],
                         "physics.mechanics.buoyancy")
        self.assertTrue(result.evidence_gate["allow_mastery_update"])
        self.assertEqual(result.evidence_gate["reason_code"],
                         "performance_evidence_valid")

    def test_partial_verdict_reaches_m2_without_binary_collapse(self):
        # W2/A04：partial 不再被 .correct(=False) 塞成二元 wrong——verdict 与
        # gate cap 后的 max_confidence 一并传入 M2（事件处理器据此跳过负向
        # BKT 更新），账本/事件侧保留 0.5 语义。
        from unittest.mock import patch
        from app.agents.assessment.manager import AssessmentManager
        from app.agents.assessment.state import AssessmentResult

        result = AssessmentResult(
            question_id="q_partial", concept="浮力",
            skill_id="physics.mechanics.buoyancy", verdict="partial", score=0.5,
        )
        with patch("app.agents.student_model.record_quiz_result") as record:
            AssessmentManager()._record(
                result, student_id="student_gate", student_answer="写了一半",
                grading_confidence=0.75, grading_source="assessment_llm_grade",
            )
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["verdict"], "partial")
        self.assertFalse(kwargs["correct"])
        self.assertEqual(kwargs["confidence"],
                         result.evidence_gate["max_confidence"])
        # 未传 question_verified → 置信被压到 0.70（A05 缺失不默认高置信）。
        self.assertEqual(kwargs["confidence"], 0.70)


class TestStudentModelWritePath(StorageSandboxTestCase):
    """W2/A04/A05：M2 事件级语义——partial 不动 BKT、同 attempt 幂等。"""

    def test_partial_does_not_move_bkt_but_counts_in_stats(self):
        from app.agents.student_model import get_student_model
        sm = get_student_model("st_m2_partial").load()
        sm.record_quiz_result(concept="条件概率", correct=True,
                              skill_id="sk_m2_partial")
        p_after_correct = sm.mastery.records["sk_m2_partial"].p_known
        # partial：correct=False 但 verdict=partial → BKT 不动（§8.6.3 禁
        # 完整负向更新与未校准线性混合）。
        sm.record_quiz_result(concept="条件概率", correct=False,
                              skill_id="sk_m2_partial", verdict="partial",
                              note="思路对但漏了归一化")
        self.assertEqual(sm.mastery.records["sk_m2_partial"].p_known,
                         p_after_correct)
        # 概念统计仍然计入 attempt（partial 是 M3 REMEDIATION 根因信号，
        # 不丢失）。
        rec = sm.memory.get("sk_m2_partial")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.attempts, 2)
        self.assertEqual(rec.correct, 1)
        # wrong：负向更新照旧。
        sm.record_quiz_result(concept="条件概率", correct=False,
                              skill_id="sk_m2_partial", verdict="wrong")
        self.assertLess(sm.mastery.records["sk_m2_partial"].p_known,
                        p_after_correct)

    def test_same_attempt_id_influences_m2_once(self):
        # W2/A05「相同证据不能重复影响 M2」：同一 attempt_id 的重放（重试/
        # 双写路径）只记一次，BKT 只动一次。
        from app.agents.student_model import get_student_model
        from app.agents.student_model.store import read_events
        sm = get_student_model("st_m2_att").load()
        sm.record_quiz_result(concept="条件概率", correct=True,
                              skill_id="sk_m2_att", confidence=0.9,
                              attempt_id="att_dup_1")
        p1 = sm.mastery.records["sk_m2_att"].p_known
        sm.record_quiz_result(concept="条件概率", correct=True,
                              skill_id="sk_m2_att", attempt_id="att_dup_1")
        self.assertEqual(sm.mastery.records["sk_m2_att"].p_known, p1)
        graded = [e for e in read_events("st_m2_att")
                  if e.type.name == "QUIZ_GRADED"]
        self.assertEqual(len(graded), 1)
        self.assertEqual(graded[0].payload.get("attempt_id"), "att_dup_1")
        self.assertEqual(graded[0].payload.get("confidence"), 0.9)


class TestGatedCompatibility(unittest.TestCase):
    def test_gated_plan_fills_skill_ids_for_legacy_role_only_step(self):
        from app.agents.skill_runtime import gate_plan
        snap = StudentSnapshot(grade="高中")
        understanding = _understanding(TaskType.PRACTICE, requires_tools=True)
        frame = build_task_frame("出一道浮力练习题", understanding, snap)
        legacy_plan = TaskPlan(steps=[PlanStep(
            agent_role="assessment", task="出一道题",
        )], source="legacy")
        gated = gate_plan(legacy_plan, frame, decide(frame))
        self.assertEqual(
            gated.plan.steps[0].skill_ids,
            ["agent.skill.assessment.generate_practice"],
        )

    def test_invalid_runtime_mode_falls_back_to_shadow(self):
        from unittest.mock import patch
        from app.core.config import _resolve_skill_runtime_mode
        with patch.dict("os.environ", {"SKILL_RUNTIME_MODE": "unknown-mode"}):
            self.assertEqual(_resolve_skill_runtime_mode(), "shadow")

class TestOptionalStepRecovery(StorageSandboxTestCase):
    def test_failed_optional_memory_step_advances_to_assessment(self):
        import asyncio
        from unittest.mock import patch
        from app.agents import executor
        from app.core.session import TutorSession
        from app.core.tool_protocol import ErrorCode, err

        class FailingRecall(_SequenceTool):
            async def run(self, **kwargs):
                return err(self.name, ErrorCode.NOT_FOUND, "没有历史错题")

        plan = TaskPlan(steps=[
            PlanStep(agent_role="memory", task="可选回顾错题", optional=True,
                     skill_ids=["agent.skill.memory.recall_history"]),
            PlanStep(agent_role="assessment", task="生成诊断题",
                     skill_ids=["agent.skill.assessment.generate_practice"]),
        ], source="skill_gated:test_optional")
        llm = _SequenceLLM()
        trace = _TraceStub()
        tools = [FailingRecall("recall_history"), _SequenceTool("generate_quiz")]

        async def collect():
            return [event async for event in executor.execute(
                [{"role": "user", "content": "诊断浮力"}],
                TutorSession(session_id="optional_step", grade="高中"),
                tools, plan, llm, trace,
            )]

        with patch.object(executor.settings, "skill_runtime_mode", "gated"):
            events = asyncio.run(collect())

        self.assertEqual(llm.tool_names_seen[:2],
                         [["recall_history"], ["generate_quiz"]])
        advances = [data for kind, data in trace.events
                    if kind == "skill_plan_advance"]
        self.assertTrue(advances[0]["skipped_optional"])
        self.assertEqual(events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
