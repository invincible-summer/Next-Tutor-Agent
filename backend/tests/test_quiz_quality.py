"""Quiz quality gate (P0) + answer-trail (P0b) + reasoning digest (P1b) tests.

Covers:
  - core.quiz_verify: deterministic structural checks, LLM critic, retry
  - tools generate_quiz / fit_quiz end-to-end with the quality gate
  - assessment generator: disable_thinking hardening + critic wiring
  - core.quiz_attempts: transcript records + M6 quiz_graded emission + digest
  - supervisor answer-trail rendering (_recent_quiz_results / derive_snapshot)
  - compaction quiz_digest injection (survives head-truncation)
  - reasoning_summarizer fail-open behavior
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.core.config import settings


class QueueLLM:
    """complete() serves queued responses in order (generation, critic, ...)."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        self.calls.append(messages)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def _q(qid, *, answer="B", stem="题干", explanation="这是足够长的解析内容，超过十五个字的下限。"):
    return {"id": qid, "type": "multiple_choice", "stem": stem,
            "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            "answer": answer, "explanation": explanation,
            "knowledge_point": "浮力", "difficulty": "easy"}


def _gen_json(questions) -> str:
    return json.dumps({"questions": questions}, ensure_ascii=False)


def _critic_json(pairs) -> str:
    verdicts = [{"id": i, "verdict": v, "reason": "r"} for i, v in pairs]
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


def _blueprint_json(n: int = 1) -> str:
    """Round-1 design-pass response consumed by core.quiz_design."""
    return json.dumps({
        "angles_considered": ["概念辨析", "迁移应用"],
        "blueprint": [{"id": i, "angle": "概念辨析", "bloom": "analyze",
                       "q_type": "short_answer", "trap": "常见误区",
                       "idea": f"换情境考查第{i}题"} for i in range(1, n + 1)],
    }, ensure_ascii=False)


class TestWellFormed(unittest.TestCase):
    def test_valid_mc(self):
        from app.core.quiz_verify import is_well_formed
        self.assertTrue(is_well_formed(_q(1)))

    def test_answer_letter_not_in_options(self):
        from app.core.quiz_verify import is_well_formed
        self.assertFalse(is_well_formed(_q(1, answer="E")))

    def test_duplicate_options(self):
        from app.core.quiz_verify import is_well_formed
        q = _q(1)
        q["options"]["C"] = "甲"
        self.assertFalse(is_well_formed(q))

    def test_empty_stem_answer_explanation(self):
        from app.core.quiz_verify import is_well_formed
        self.assertFalse(is_well_formed(_q(1, stem="")))
        self.assertFalse(is_well_formed(_q(1, answer="")))
        self.assertFalse(is_well_formed(_q(1, explanation="太短")))

    def test_fill_blank_needs_no_options(self):
        from app.core.quiz_verify import is_well_formed
        q = _q(1)
        q["type"] = "fill_blank"
        q["answer"] = "9.8 N"
        del q["options"]
        self.assertTrue(is_well_formed(q))


class TestCritic(unittest.TestCase):
    def test_incorrect_verdict_drops_question(self):
        from app.core.quiz_verify import verify_questions
        llm = QueueLLM([_critic_json([(1, "correct"), (2, "incorrect")])])
        kept, dropped, ok = asyncio.run(verify_questions(
            llm, [_q(1), _q(2)], topic="浮力", grade="初中"))
        self.assertTrue(ok)
        self.assertEqual([q["id"] for q in kept], [1])
        self.assertEqual([q["id"] for q in dropped], [2])
        self.assertIn("_drop_reason", dropped[0])

    def test_critic_garbage_is_fail_open(self):
        from app.core.quiz_verify import verify_questions
        kept, dropped, ok = asyncio.run(verify_questions(
            QueueLLM(["not json"]), [_q(1)], topic="t", grade="g"))
        self.assertFalse(ok)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_critic_exception_is_fail_open(self):
        from app.core.quiz_verify import verify_questions
        kept, dropped, ok = asyncio.run(verify_questions(
            QueueLLM([RuntimeError("boom")]), [_q(1)], topic="t", grade="g"))
        self.assertFalse(ok)
        self.assertEqual(len(kept), 1)


class TestQuestionVerifiedFlag(unittest.TestCase):
    """W2/A05：内容级验证状态归一——只有 critic 独立重解通过才为 True。

    basic 结构校验、off、缺失、critic 故障放行一律 None（缺失不默认高
    置信）；answer_verified=True 但 critic=skipped（basic 模式）不得冒充
    内容已验证（A17 的四分标签属 W3，这里先钉两级）。"""

    def test_critic_ok_is_verified(self):
        from app.core.quiz_verify import question_verified
        self.assertIs(question_verified(
            {"mode": "critic", "critic": "ok", "answer_verified": True}), True)

    def test_basic_mode_is_not_content_verified(self):
        from app.core.quiz_verify import question_verified
        self.assertIsNone(question_verified(
            {"mode": "basic", "critic": "skipped", "answer_verified": True}))
        self.assertIsNone(question_verified(
            {"mode": "off", "critic": "skipped", "answer_verified": False}))

    def test_critic_error_fail_open_is_not_verified(self):
        from app.core.quiz_verify import question_verified
        self.assertIsNone(question_verified(
            {"mode": "critic", "critic": "error", "answer_verified": False}))

    def test_missing_metadata_is_none(self):
        from app.core.quiz_verify import question_verified
        self.assertIsNone(question_verified(None))
        self.assertIsNone(question_verified({}))


class TestVerificationLabel(unittest.TestCase):
    """W3/A17：验证标签四分派生（审计/投影输入；question_verified 仍只认两级）。"""

    def test_four_way_labels(self):
        from app.core.quiz_verify import verification_label
        self.assertEqual(verification_label(
            {"mode": "critic", "critic": "ok"}), "content_checked")
        self.assertEqual(verification_label(
            {"mode": "critic", "critic": "error"}), "ambiguous")
        self.assertEqual(verification_label(
            {"mode": "basic", "critic": "skipped"}), "structural_valid")
        self.assertEqual(verification_label(
            {"mode": "off", "critic": "skipped"}), "unchecked")
        self.assertEqual(verification_label(None), "unchecked")
        self.assertEqual(verification_label({}), "unchecked")
        self.assertEqual(verification_label(
            {"mode": "critic", "critic": "skipped"}), "unchecked")

    def test_label_refines_not_overrides_gate(self):
        from app.core.quiz_verify import question_verified
        for v in ({"mode": "basic"}, {"mode": "off"}, {"mode": "critic",
                                                      "critic": "error"}):
            self.assertIsNone(question_verified(v))
        self.assertIs(question_verified({"mode": "critic", "critic": "ok"}), True)


def _rubric_criteria():
    return [
        {"id": "c1", "description": "正确写出归一化分母 P(A)", "weight": 1.0,
         "critical": True},
        {"id": "c2", "description": "代入数值并完成除法计算", "weight": 1.0},
    ]


class TestRubricFreeze(unittest.TestCase):
    """W3/D04：量规随题冻结——生成时校验、以最终稳定题号挂载、失败降级为缺省。"""

    def test_freeze_valid_rubric(self):
        from app.core.quiz_verify import freeze_rubric
        q = _q(1)
        q["rubric_criteria"] = _rubric_criteria()
        q["equivalent_solutions"] = ["0.40"]
        rubric = freeze_rubric(q, "q_abc12345_1")
        self.assertIsNotNone(rubric)
        self.assertEqual(rubric["rubric_id"], "q_abc12345_1")
        self.assertEqual(rubric["version"], 1)
        self.assertEqual(len(rubric["criteria"]), 2)
        self.assertTrue(rubric["criteria"][0]["critical"])
        self.assertFalse(rubric["criteria"][1]["critical"])
        self.assertEqual(rubric["equivalent_solutions"], ["0.40"])
        self.assertIn("frozen_at", rubric)

    def test_invalid_rubric_degrades_to_none(self):
        from app.core.quiz_verify import freeze_rubric
        for bad in (None, [], "junk", [{"description": "太短"}],
                    [{"id": "c", "description": ""}]):
            q = _q(1)
            q["rubric_criteria"] = bad
            self.assertIsNone(freeze_rubric(q, "q_x_1"), bad)

    def test_weight_and_duplicates_normalized(self):
        from app.core.quiz_verify import freeze_rubric
        q = _q(1)
        q["rubric_criteria"] = [
            {"id": "c1", "description": "第一步条件判断正确", "weight": -2},
            {"id": "c1", "description": "重复 id 应被丢弃", "weight": 1},
            {"description": "无 id 自动补号", "weight": "junk"},
        ]
        rubric = freeze_rubric(q, "q_x_1")
        self.assertEqual([c["id"] for c in rubric["criteria"]], ["c1", "c2"])
        self.assertTrue(all(c["weight"] == 1.0 for c in rubric["criteria"]))

    def test_pipeline_freezes_rubric_onto_stable_id(self):
        from app.core.quiz_verify import generate_verified_questions
        q = _q(1)
        q["rubric_criteria"] = _rubric_criteria()
        llm = QueueLLM([_gen_json([q]), _critic_json([(1, "correct")])])
        questions, meta = asyncio.run(generate_verified_questions(
            llm, make_prompt=lambda: "p", parse=lambda raw: json.loads(raw)["questions"],
            topic="浮力", grade="初中", temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 1)
        rubric = questions[0]["rubric"]
        self.assertEqual(rubric["rubric_id"], questions[0]["id"])
        self.assertEqual(len(rubric["criteria"]), 2)

    def test_pipeline_without_rubric_stays_usable(self):
        from app.core.quiz_verify import generate_verified_questions
        llm = QueueLLM([_gen_json([_q(1)]), _critic_json([(1, "correct")])])
        questions, _ = asyncio.run(generate_verified_questions(
            llm, make_prompt=lambda: "p", parse=lambda raw: json.loads(raw)["questions"],
            topic="浮力", grade="初中", temperature=0.4, max_tokens=1000))
        self.assertNotIn("rubric", questions[0])

    def test_generation_prompts_carry_rubric_contract(self):
        from app.prompts.registry import get
        for pid in ("assessment_generate", "assessment_generate_auto"):
            text = get(pid).text
            self.assertIn("rubric_criteria", text)
        from app.tools.quiz import _QUIZ_PROMPT
        from app.tools.fit_quiz import _FIT_PROMPT
        for text in (_QUIZ_PROMPT, _FIT_PROMPT):
            self.assertIn("rubric_criteria", text)

    def test_question_roundtrips_rubric(self):
        from app.agents.assessment.question import Question
        q = _q(1)
        q["rubric"] = {"rubric_id": "q_x_1", "version": 1,
                       "criteria": _rubric_criteria(), "equivalent_solutions": []}
        lifted = Question.from_quiz_dict(q, concept="条件概率")
        self.assertEqual(lifted.rubric["rubric_id"], "q_x_1")
        d = lifted.to_dict()
        self.assertEqual(d["rubric"]["version"], 1)
        # 旧题无 rubric 键 → 空 dict，不报错
        self.assertEqual(Question.from_quiz_dict(_q(2)).rubric, {})


class TestGenerateVerified(unittest.TestCase):
    def test_full_pipeline_drops_ill_formed_and_assigns_stable_ids(self):
        from app.core.quiz_verify import generate_verified_questions
        good, bad_letter = _q(1), _q(2, answer="E")
        llm = QueueLLM([_gen_json([good, bad_letter, _q(3)]),
                        _critic_json([(1, "correct"), (3, "correct")])])
        questions, meta = asyncio.run(generate_verified_questions(
            llm, make_prompt=lambda: "p", parse=lambda raw: json.loads(raw)["questions"],
            topic="浮力", grade="初中", temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 2)
        self.assertEqual(meta["dropped_ill_formed"], 1)
        self.assertTrue(meta["answer_verified"])
        # W2/A14：每套唯一前缀 + 套内序号——跨套不再共用裸数字 id。
        qid1, qid2 = questions[0]["id"], questions[1]["id"]
        self.assertRegex(qid1, r"^q_[0-9a-f]{8}_1$")
        self.assertRegex(qid2, r"^q_[0-9a-f]{8}_2$")
        set_uid = qid1.rsplit("_", 1)[0]
        self.assertEqual(qid2.rsplit("_", 1)[0], set_uid)

    def test_all_flagged_triggers_one_regeneration(self):
        from app.core.quiz_verify import generate_verified_questions
        llm = QueueLLM([
            _gen_json([_q(1)]), _critic_json([(1, "incorrect")]),   # attempt 1: dropped
            _gen_json([_q(1)]), _critic_json([(1, "correct")]),     # attempt 2: kept
        ])
        questions, meta = asyncio.run(generate_verified_questions(
            llm, make_prompt=lambda: "p", parse=lambda raw: json.loads(raw)["questions"],
            topic="浮力", grade="初中", temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 1)
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(meta["dropped_by_critic"], 1)

    def test_mode_off_skips_all_checks(self):
        from app.core.quiz_verify import generate_verified_questions
        llm = QueueLLM([_gen_json([_q(1, answer="E")])])
        with mock.patch.object(settings, "quiz_verify_mode", "off"):
            questions, meta = asyncio.run(generate_verified_questions(
                llm, make_prompt=lambda: "p", parse=lambda raw: json.loads(raw)["questions"],
                topic="t", grade="g", temperature=0.4, max_tokens=1000))
        self.assertEqual(len(questions), 1)  # ill-formed kept under off
        self.assertFalse(meta["answer_verified"])
        self.assertEqual(len(llm.calls), 1)  # no critic call


class TestToolsEndToEnd(unittest.TestCase):
    def test_avoid_stems_and_focus_enter_prompt(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_blueprint_json(1), _gen_json([_q(1)]),
                        _critic_json([(1, "correct")])])
        tool = GenerateQuizTool(llm, avoid_stems=["旧题干：盐酸滴定氢氧化钠"])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            result = asyncio.run(tool.run(topic="酸碱中和滴定", grade="高中",
                                          focus="滴定步骤"))
        self.assertEqual(result.status, "success")
        prompt = llm.calls[1][0]["content"]  # calls[0] 是蓝图轮，calls[1] 才是生成 prompt
        self.assertIn("旧题干：盐酸滴定氢氧化钠", prompt)
        self.assertIn("禁止重复", prompt)
        self.assertIn("滴定步骤", prompt)

    def test_prompt_defines_difficulty_semantics_and_progression(self):
        # 难度三档必须有可执行定义（easy 一步应用 / medium 一次转化 /
        # hard 多步变式），且 count>=2 时套内递进——否则 LLM 一律按课本
        # 例题级理解「基础/中等/挑战」，是学生「全是最基础题」的根源之一。
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_blueprint_json(1), _gen_json([_q(1)]),
                        _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            asyncio.run(GenerateQuizTool(llm).run(topic="切线放缩", grade="高中",
                                                  difficulty="medium", count=1))
        prompt = llm.calls[1][0]["content"]
        self.assertIn("一步直接应用", prompt)
        self.assertIn("一次转化", prompt)
        self.assertIn("多步推理", prompt)
        self.assertIn("套内递进", prompt)

    def test_generate_quiz_passes_gate(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_blueprint_json(2), _gen_json([_q(1), _q(2, answer="E")]),
                        _critic_json([(1, "correct")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            result = asyncio.run(GenerateQuizTool(llm).run(topic="浮力", grade="初中", count=2))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.data["questions"]), 1)
        self.assertTrue(result.data["answer_verified"])
        self.assertIn("verification", result.data)

    def test_generate_quiz_partial_when_nothing_survives(self):
        from app.tools.quiz import GenerateQuizTool
        llm = QueueLLM([_blueprint_json(1),
                        _gen_json([_q(1)]), _critic_json([(1, "incorrect")]),
                        _gen_json([_q(1)]), _critic_json([(1, "incorrect")])])
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            result = asyncio.run(GenerateQuizTool(llm).run(topic="浮力", grade="初中"))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.data["questions"], [])

    def test_fit_quiz_passes_gate(self):
        from app.tools.fit_quiz import FitQuizTool
        llm = QueueLLM([_gen_json([_q(1)]), _critic_json([(1, "correct")])])
        result = asyncio.run(FitQuizTool(llm).run(reference="参考题：一木块漂浮", grade="初中"))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.data["questions"]), 1)
        self.assertTrue(result.data["answer_verified"])


class TestAssessmentGenerator(unittest.TestCase):
    def _run(self, responses):
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal
        ctx = AssessmentContext(concept="浮力", grade="初中", base_difficulty=2)
        with mock.patch.object(settings, "quiz_design_mode", "two_pass"):
            return asyncio.run(generate_question(
                AssessmentGoal(purpose="check"), ctx, llm=QueueLLM(responses)))

    def test_critic_ok_returns_question(self):
        q = self._run([_blueprint_json(1), _gen_json([_q(1)]),
                       _critic_json([(1, "correct")])])
        self.assertIsNotNone(q)
        self.assertEqual(q.answer, "B")

    def test_critic_flag_returns_none(self):
        q = self._run([_blueprint_json(1), _gen_json([_q(1)]),
                       _critic_json([(1, "incorrect")])])
        self.assertIsNone(q)

    def test_ill_formed_returns_none_without_critic(self):
        q = self._run([_blueprint_json(1), _gen_json([_q(1, answer="E")])])
        self.assertIsNone(q)


class _FakeSession:
    def __init__(self, quiz_history):
        self.quiz_history = quiz_history
        self.session_id = "sess_qa_test"


class TestQuizAttempts(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.sid = "sess_qa_" + os.urandom(3).hex()
        self.student = "student_qa_" + os.urandom(3).hex()

    def test_generated_quiz_recorded_in_transcript(self):
        from app.core.context import transcript_path
        from app.core.quiz_attempts import record_generated_quiz
        record_generated_quiz(self.sid, {"topic": "浮力", "questions": [_q(1)]})
        text = transcript_path(self.sid).read_text(encoding="utf-8")
        self.assertIn("【出题记录】", text)
        self.assertIn("浮力", text)

    def test_attempt_recorded_to_transcript_and_m6(self):
        from app.core.context import transcript_path
        from app.core.quiz_attempts import record_quiz_attempt
        from app.agents.memory import store as mem_store
        record_quiz_attempt(
            self.sid, stem="木块为何漂浮", verdict="wrong",
            student_answer="因为木头轻", concept="浮力", subject="物理",
            student_id=self.student, correct=False)
        text = transcript_path(self.sid).read_text(encoding="utf-8")
        self.assertIn("【作答记录】", text)
        self.assertIn("因为木头轻", text)
        episodes = mem_store.read_episodes(self.student)
        self.assertEqual(episodes, [])  # legacy episodic store is compatibility-read-only

    def test_attempt_recallable_via_recall_history(self):
        from app.core.quiz_attempts import record_quiz_attempt
        from app.tools.recall_history import RecallHistoryTool
        record_quiz_attempt(
            self.sid, stem="铁块为何下沉", verdict="correct",
            student_answer="密度大于水", concept="浮力",
            student_id="", correct=True)
        result = asyncio.run(RecallHistoryTool(self.sid).run(query="铁块下沉"))
        self.assertEqual(result.status, "success")
        self.assertIn("密度大于水", result.text)

    def test_digest_renders_verdicts(self):
        from app.core.quiz_attempts import quiz_digest_for_session
        q = _q(1)
        q["result"] = {"verdict": "partial", "student_answer": "半个答案"}
        session = _FakeSession([{"topic": "浮力", "questions": [q]}])
        digest = quiz_digest_for_session(session)
        self.assertIn("浮力", digest)
        self.assertIn("半个答案", digest)
        self.assertIn("部分对", digest)

    def test_latest_quiz_digest_renders_stems_for_next_turn(self):
        # 「仔细讲解一下上一题」场景：下一轮 status recap 必须带题干与作答
        from app.core.quiz_attempts import latest_quiz_digest
        q1 = _q(1)
        q1["result"] = {"verdict": "wrong", "student_answer": "C"}
        session = _FakeSession([{"topic": "贝叶斯公式", "questions": [q1, _q(2)]}])
        d = latest_quiz_digest(session)
        self.assertIn("贝叶斯公式", d)
        self.assertIn("答案:B", d)
        self.assertIn("学生答「C」判错", d)
        self.assertIn("未作答", d)
        self.assertEqual(latest_quiz_digest(_FakeSession([])), "")


class TestSupervisorAnswerTrail(unittest.TestCase):
    def test_recent_quiz_results_includes_student_answer(self):
        from app.agents.supervisor import _recent_quiz_results
        q = _q(1)
        q["result"] = {"verdict": "wrong", "student_answer": "C"}
        session = _FakeSession([{"questions": [q]}])
        recap = _recent_quiz_results(session)
        self.assertIn("第1题错", recap)
        self.assertIn("学生答「C」", recap)

    def test_derive_snapshot_filters_weak_by_verdict(self):
        from app.agents.supervisor import derive_snapshot
        q_right = _q(1)
        q_right["knowledge_point"] = "答对的点"
        q_right["result"] = {"verdict": "correct", "student_answer": "B"}
        q_wrong = _q(2)
        q_wrong["knowledge_point"] = "答错的点"
        q_wrong["result"] = {"verdict": "wrong", "student_answer": "C"}
        q_pending = _q(3)
        q_pending["knowledge_point"] = "未作答的点"
        from app.core.session import TutorSession
        session = TutorSession(grade="高中")
        session.quiz_history = [{"questions": [q_right, q_wrong, q_pending]}]
        snap = derive_snapshot(session)
        self.assertIn("答错的点", snap.recent_weak_points)
        self.assertNotIn("答对的点", snap.recent_weak_points)
        self.assertNotIn("未作答的点", snap.recent_weak_points)


class TestCompactionDigest(unittest.TestCase):
    def _history(self, pairs: int, *, big: bool = False):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "preamble"}]
        for i in range(pairs):
            body = ("很长的讲解内容" * 300) if big else f"第{i}轮讲解"
            msgs.append({"role": "user", "content": f"问题{i}"})
            msgs.append({"role": "assistant", "content": body})
        return msgs

    def test_digest_injected_into_summarizer_input(self):
        from app.core.context import compact_history
        llm = QueueLLM(["4. 练习与错题：浮力题答错"])
        new_msgs, summary = asyncio.run(compact_history(
            self._history(6), llm, keep_recent=4,
            quiz_digest="套题（浮力）：\n  1. 题干｜学生答「C」判错"))
        self.assertTrue(summary)
        summarizer_input = llm.calls[0][1]["content"]
        self.assertIn("【本会话出题与作答记录", summarizer_input)
        self.assertIn("学生答「C」判错", summarizer_input)
        self.assertTrue(new_msgs[2]["content"].startswith("[对话压缩摘要"))

    def test_digest_survives_head_truncation(self):
        from app.core.context import compact_history
        llm = QueueLLM(["摘要"])
        # 24 轮大消息：old 区序列化后 >20000 字，触发头截分支
        _msgs, summary = asyncio.run(compact_history(
            self._history(24, big=True), llm, keep_recent=4,
            quiz_digest="套题（浮力）"))
        self.assertTrue(summary)
        summarizer_input = llm.calls[0][1]["content"]
        # 对话原文被头截，但 digest 在截断之后才拼接，必须完整保留
        self.assertIn("[已截断较早部分", summarizer_input)
        self.assertTrue(summarizer_input.startswith("【本会话出题与作答记录"))


class TestMcVerdictAndMerge(unittest.TestCase):
    """Regression: /quiz/record MC without options must grade correct/wrong
    (not unknown), and merge_quiz_results_from_disk must protect card answers
    from being clobbered by an in-flight chat turn's save."""

    def setUp(self):
        self.sid = "student_mc_" + os.urandom(3).hex()

    def tearDown(self):
        from app.agents.student_model.store import _resolve
        for ext in (".json", ".events.jsonl"):
            try:
                _resolve(self.sid, ext).unlink()
            except OSError:
                pass

    def test_mc_without_options_grades_deterministically(self):
        from app.agents.assessment import (AssessmentContext, Question,
                                           get_assessment_manager)
        q = Question(concept="浮力", q_type="multiple_choice",
                     stem="s", answer="B")  # 无 options（/quiz/record 旧行为）
        ctx = AssessmentContext(concept="浮力", grade="高中")
        mgr = get_assessment_manager()
        right = asyncio.run(mgr.evaluate_and_record(q, "B", ctx, student_id=self.sid))
        wrong = asyncio.run(mgr.evaluate_and_record(q, "C", ctx, student_id=self.sid))
        self.assertEqual(right.verdict, "correct")
        self.assertEqual(wrong.verdict, "wrong")

    def test_unknown_verdict_not_recorded(self):
        from app.core.context import transcript_path
        from app.core.quiz_attempts import record_quiz_attempt
        record_quiz_attempt("sess_unknown_x", stem="s", verdict="unknown",
                            student_answer="C", student_id=self.sid)
        self.assertFalse(transcript_path("sess_unknown_x").exists())

    def test_merge_protects_card_answers_from_turn_save(self):
        from app.core.quiz_attempts import merge_quiz_results_from_disk
        from app.core.session import (TutorSession, delete_session,
                                      load_session, new_session_id,
                                      save_session)
        session = TutorSession(grade="高中")
        session.session_id = new_session_id("merge_test")
        session.quiz_history = [{"questions": [{
            "stem": "题干X", "answer": "B", "type": "multiple_choice"}]}]
        save_session(session)
        try:
            # 答题卡在对话轮进行中写盘（另一个 load-modify-save 周期）
            disk = load_session(session.session_id)
            disk.quiz_history[0]["questions"][0]["result"] = {
                "verdict": "wrong", "student_answer": "C"}
            save_session(disk)
            # 对话轮结束：内存里的 quiz_history 是旧快照（无 result）
            self.assertNotIn("result", session.quiz_history[0]["questions"][0])
            changed = merge_quiz_results_from_disk(session)
            self.assertTrue(changed)
            save_session(session)  # 修复前这一步会把作答结果覆写掉
            again = load_session(session.session_id)
            res = again.quiz_history[0]["questions"][0].get("result")
            self.assertIsNotNone(res)
            self.assertEqual(res["student_answer"], "C")
        finally:
            delete_session(session.session_id)


    def test_write_back_syncs_message_tool_payload(self):
        # 判定结果要同步进 assistant 消息的 toolCalls 载荷——前端刷新后据此
        # 恢复答题卡的已答锁定状态，防止重复作答。
        from app.api.v1.quiz import _write_back_answer
        from app.core.session import (TutorSession, delete_session,
                                      load_session, new_session_id,
                                      save_session)
        session = TutorSession(grade="高中")
        session.session_id = new_session_id("wb_sync")
        q = {"stem": "题干Y", "answer": "B", "type": "multiple_choice"}
        session.quiz_history = [{"questions": [dict(q)]}]
        session.messages = [
            {"role": "user", "content": "出题"},
            {"role": "assistant", "content": "作答吧",
             "toolCalls": [{"name": "generate_quiz",
                            "result": {"tool": "generate_quiz", "status": "success",
                                       "data": {"questions": [dict(q)]}, "text": ""}}]},
        ]
        save_session(session)
        try:
            _write_back_answer(session.session_id, stem="题干Y", verdict="wrong",
                               student_answer="C")
            again = load_session(session.session_id)
            res = again.quiz_history[0]["questions"][0].get("result")
            self.assertIsNotNone(res)
            payload_q = again.messages[1]["toolCalls"][0]["result"]["data"]["questions"][0]
            self.assertEqual(payload_q["result"]["student_answer"], "C")
        finally:
            delete_session(session.session_id)


class TestGradeRecordFlag(StorageSandboxTestCase):
    """record=false（MC 点评）必须只产出点评，不写掌握度/作答记录。"""

    def test_record_false_writes_nothing(self):
        import app.api.v1.quiz as quiz_api

        class GradeLLM:
            async def stream(self, messages, tools=None, temperature=None,
                             max_tokens=None):
                yield {"kind": "answer", "delta": "[对] 选择正确，中和点判断准确。"}
                yield {"kind": "done", "finish_reason": "stop", "usage": {}}

        # W1（A01）：session_id 现在先过归属校验——给调用者一份本人会话。
        from app.core.session import TutorSession, delete_session, save_session
        save_session(TutorSession(session_id="sess_norecord",
                                  student_id="st_norecord", title="t"))
        req = quiz_api.GradeRequest(
            stem="题干Z", q_type="multiple_choice", student_answer="B",
            correct_answer="B", knowledge_point="滴定", session_id="sess_norecord",
            record=False)
        try:
            with mock.patch.object(quiz_api, "get_llm", return_value=GradeLLM()):
                resp = asyncio.run(quiz_api.grade_answer(req, student_id="st_norecord"))

            async def drain():
                out = []
                async for chunk in resp.body_iterator:
                    out.append(chunk if isinstance(chunk, str) else chunk.decode())
                return "".join(out)

            body = asyncio.run(drain())
            self.assertIn('"verdict": "correct"', body)
            # record=false：不产生 transcript、不写 M6 episode
            from app.core.context import transcript_path
            self.assertFalse(transcript_path("sess_norecord").exists())
        finally:
            delete_session("sess_norecord")


class TestReasoningSummarizer(unittest.TestCase):
    def test_returns_digest(self):
        from app.agents.reasoning_summarizer import summarize_reasoning
        llm = QueueLLM(["提炼后的过程说明"])
        out = asyncio.run(summarize_reasoning(llm, "很长的内部推理" * 100))
        self.assertEqual(out, "提炼后的过程说明")

    def test_fail_open_on_error(self):
        from app.agents.reasoning_summarizer import summarize_reasoning
        out = asyncio.run(summarize_reasoning(QueueLLM([RuntimeError("x")]), "推理"))
        self.assertEqual(out, "")

    def test_empty_input(self):
        from app.agents.reasoning_summarizer import summarize_reasoning
        out = asyncio.run(summarize_reasoning(QueueLLM([]), ""))
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
