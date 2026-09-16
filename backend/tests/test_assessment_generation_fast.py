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


if __name__ == "__main__":
    unittest.main()
