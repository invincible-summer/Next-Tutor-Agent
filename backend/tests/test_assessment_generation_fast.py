"""CAT question generation latency/failure guards.

These tests pin the assessment-center fast path without changing the richer
two-pass behavior used by Chat and the general quiz tools.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _question() -> str:
    return json.dumps({"questions": [{
        "id": 1,
        "type": "multiple_choice",
        "stem": "质量为 2 kg 的物体受合力 6 N，求加速度。",
        "options": {"A": "1 m/s²", "B": "3 m/s²", "C": "6 m/s²", "D": "12 m/s²"},
        "answer": "B",
        "explanation": "由牛顿第二定律 $a=F/m$，代入 $F=6 N$ 与 $m=2 kg$，得到 $a=3 m/s²$。",
        "knowledge_point": "牛顿第二定律",
        "difficulty": "easy",
    }]}, ensure_ascii=False)


def _audit() -> str:
    return json.dumps({"items": [{
        "question_ref": "1",
        "proposed_status": "passed",
        "answer_check": "由 F=ma 得 a=3 m/s²，与 B 一致。",
        "alignment": "符合基础难度。",
        "brief_basis": "结构与答案一致。",
        "illustration_check": "not_required",
    }]}, ensure_ascii=False)


class _LLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0), {}


class AssessmentGenerationFastPathTest(unittest.TestCase):
    def test_cat_fast_path_skips_blueprint_but_keeps_critic(self):
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal

        llm = _LLM([_question(), _audit()])
        q = asyncio.run(generate_question(
            AssessmentGoal(concept="牛顿第二定律", purpose="adaptive",
                           q_type="multiple_choice", difficulty=2,
                           illustration_request="none"),
            AssessmentContext(concept="牛顿第二定律", grade="高中",
                              subject="物理", base_difficulty=2),
            llm=llm, use_blueprint=False))

        self.assertIsNotNone(q)
        self.assertEqual(len(llm.calls), 2)
        self.assertNotIn("任务设计者", llm.calls[0]["messages"][0]["content"])
        self.assertIn("出题审核员", llm.calls[1]["messages"][0]["content"])

    def test_auto_type_accepts_model_chosen_supported_type(self):
        """practice 自动建议 short_answer 时，模型自选的合格题型不得被丢弃。

        live 验收曾因 strict type 过滤把一道完全合格的选择题判为
        invalid_question_json，CAT 只有一次采样机会，直接掉进保底自检草稿。
        """
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal

        llm = _LLM([_question(), _audit()])
        q = asyncio.run(generate_question(
            AssessmentGoal(concept="牛顿第二定律", purpose="practice",
                           q_type="", difficulty=2, illustration_request="none"),
            AssessmentContext(concept="牛顿第二定律", grade="高中",
                              subject="物理", base_difficulty=2),
            llm=llm, use_blueprint=False))

        self.assertIsNotNone(q)
        self.assertFalse(q.id.startswith("q_draft_"))
        self.assertEqual(q.q_type, "multiple_choice")
        self.assertIn("题型", llm.calls[0]["messages"][0]["content"])

    def test_explicit_type_still_rejects_mismatched_output(self):
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal

        llm = _LLM([_question(), _audit()])
        q = asyncio.run(generate_question(
            AssessmentGoal(concept="牛顿第二定律", purpose="practice",
                           q_type="short_answer", difficulty=2,
                           illustration_request="none"),
            AssessmentContext(concept="牛顿第二定律", grade="高中",
                              subject="物理", base_difficulty=2),
            llm=llm, use_blueprint=False))

        # 单次采样被类型过滤拒绝后落到保底自检草稿，而不是把选择题冒充简答题。
        self.assertIsNotNone(q)
        self.assertTrue(q.id.startswith("q_draft_"))
        self.assertEqual(q.q_type, "short_answer")
        self.assertIn("题型硬性要求", llm.calls[0]["messages"][0]["content"])
        self.assertEqual(len(llm.calls), 1)

    def test_quiz_client_uses_light_model_and_single_retry_lane(self):
        from app.core import config
        from app.core import llm_async

        with patch.object(config.settings, "quiz_model", "deepseek-flash"), \
                patch.object(config.settings, "quiz_sdk_max_retries", 0), \
                patch.object(config.settings, "quiz_retry_max", 2), \
                patch.object(config.settings, "quiz_retry_base_delay", 0.5), \
                patch.object(llm_async, "AsyncLLMClient") as client:
            llm_async.get_llm("quiz")

        kwargs = client.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek-flash")
        self.assertEqual(kwargs["sdk_max_retries"], 0)
        self.assertEqual(kwargs["retry_max"], 2)
        self.assertEqual(kwargs["retry_base_delay"], 0.5)

    def test_critic_switch_off_delivers_normal_question_without_critic_call(self):
        """用户关闭 critic 后：单次生成调用、正常 q_ 前缀、诚实 unreviewed。"""
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal

        llm = _LLM([_question()])  # 没有 critic 响应：多打一次调用会崩
        from app.agents.assessment import generator
        with patch.object(generator, "effective_quiz_verify_mode",
                          return_value="basic"):
            q = asyncio.run(generate_question(
                AssessmentGoal(concept="牛顿第二定律", purpose="adaptive",
                               q_type="multiple_choice", difficulty=2,
                               illustration_request="none"),
                AssessmentContext(concept="牛顿第二定律", grade="高中",
                                  subject="物理", base_difficulty=2),
                llm=llm, use_blueprint=False))
        self.assertIsNotNone(q)
        self.assertTrue(q.id.startswith("q_"))
        self.assertFalse(q.id.startswith("q_draft_"))
        self.assertEqual(len(llm.calls), 1)
        self.assertFalse(q.verification.get("answer_verified"))
        self.assertEqual(q.verification.get("status"), "unreviewed")

    def test_critic_error_fail_open_delivers_normal_question(self):
        """critic 自身失败（超时/不可解析）时按 fail-open 交付正常题，不再落草稿。"""
        from app.agents.assessment.generator import generate_question
        from app.agents.assessment.state import AssessmentContext, AssessmentGoal

        llm = _LLM([_question(), "not-json-at-all"])
        q = asyncio.run(generate_question(
            AssessmentGoal(concept="牛顿第二定律", purpose="adaptive",
                           q_type="multiple_choice", difficulty=2,
                           illustration_request="none"),
            AssessmentContext(concept="牛顿第二定律", grade="高中",
                              subject="物理", base_difficulty=2),
            llm=llm, use_blueprint=False))
        self.assertIsNotNone(q)
        self.assertTrue(q.id.startswith("q_"))
        self.assertFalse(q.id.startswith("q_draft_"))
        self.assertFalse(q.verification.get("answer_verified"))
        self.assertEqual(q.verification.get("status"), "unreviewed")


class QuizVerifyModePolicyTest(unittest.TestCase):
    def test_effective_mode_downgrades_only_the_critic_lane(self):
        from app.core import config
        from app.core import quiz_illustration_policy as policy

        with patch.object(policy, "account_allows_quiz_critic", return_value=True):
            self.assertEqual(policy.effective_quiz_verify_mode("u1"), "critic")
        with patch.object(policy, "account_allows_quiz_critic", return_value=False):
            self.assertEqual(policy.effective_quiz_verify_mode("u1"), "basic")
        # 环境级降档不可被用户开关逆转
        with patch.object(config.settings, "quiz_verify_mode", "off"), \
                patch.object(policy, "account_allows_quiz_critic", return_value=True):
            self.assertEqual(policy.effective_quiz_verify_mode("u1"), "off")
        with patch.object(config.settings, "quiz_verify_mode", "basic"), \
                patch.object(policy, "account_allows_quiz_critic", return_value=False):
            self.assertEqual(policy.effective_quiz_verify_mode("u1"), "basic")


class CatDraftRetryTest(unittest.TestCase):
    """保底自检草稿不得满足外层重试循环：一次 critic 链失败必须触发降档重采样。"""

    def _run(self, outcomes: list):
        import asyncio
        from types import SimpleNamespace
        from app.agents.assessment import adaptive_test as cat
        from app.agents.assessment import generator as generator_module
        from app.agents.assessment.question import Question, QuestionType
        from app.api.v1 import assessment as assessment_api

        def make_question(draft: bool):
            prefix = "q_draft_" if draft else "q_"
            return Question(
                id=prefix + "x" * 24, concept="圆周运动",
                knowledge_points=["圆周运动"],
                q_type=QuestionType.MULTIPLE_CHOICE, difficulty=2,
                stem="物体做匀速圆周运动时，下列说法正确的是（  ）",
                options={"A": "线速度不变", "B": "角速度不变",
                         "C": "向心加速度不变", "D": "合力为零"},
                answer="B",
                explanation="匀速圆周运动角速度恒定，线速度方向时刻变化。",
                verification={"status": "unreviewed" if draft else "passed"})

        calls = {"n": 0}

        async def fake_generate(goal, ctx, *, llm, student_id="",
                                budget=None, use_blueprint=True):
            index = min(calls["n"], len(outcomes) - 1)
            calls["n"] += 1
            outcome = outcomes[index]
            if outcome is None:
                return None
            return make_question(outcome == "draft")

        instance = cat.CatInstance(
            assessment_id="asmt_test", concept="圆周运动",
            illustration_request="required", count_limit=1, difficulty=2)

        async def scenario():
            from app.core import config
            from app.core import quiz_illustration_policy as policy_module
            with patch.object(generator_module, "generate_question",
                              fake_generate), \
                    patch.object(assessment_api, "get_llm", lambda *_a, **_k: object()), \
                    patch.object(assessment_api, "get_journal",
                                 return_value=SimpleNamespace(
                                     state=lambda: SimpleNamespace(tasks={}))), \
                    patch.object(policy_module, "resolve_illustration_policy",
                                 return_value="required"), \
                    patch.object(config.settings,
                                 "assessment_generation_max_attempts", 2):
                return await assessment_api._generate_cat_question(
                    "usr_test_cat", instance, "multiple_choice")

        return asyncio.run(scenario()), calls["n"]

    def test_draft_result_triggers_difficulty_fallback_retry(self):
        task, calls = self._run(["draft", "ok"])
        # 第一次只拿到保底草稿时不得提前 break：必须降档重采样出正常题。
        self.assertEqual(calls, 2)
        self.assertIsNotNone(task)
        self.assertFalse(task.question_id.startswith("q_draft_"))

    def test_draft_is_accepted_only_on_final_attempt(self):
        task, calls = self._run(["draft", "draft"])
        self.assertEqual(calls, 2)
        self.assertIsNotNone(task)
        self.assertTrue(task.question_id.startswith("q_draft_"))

    def test_empty_result_still_retries(self):
        task, calls = self._run([None, "ok"])
        self.assertEqual(calls, 2)
        self.assertFalse(task.question_id.startswith("q_draft_"))


if __name__ == "__main__":
    unittest.main()
