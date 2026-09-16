from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.student_model.evaluation import schema as S
from app.core import quiz_illustration_enrichment as enrichment
from app.core.quiz_illustration import (
    IllustrationValidationError,
    normalize_illustration,
    normalize_svg,
)


SVG_WITH_MARKER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-label="velocity diagram">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" viewBox="0 0 10 10">
    <path d="M 0 0 L 10 5 L 0 10 Z" style="fill:#000;stroke:#000;stroke-width:1"/>
  </marker>
</defs>
<line x1="80" y1="200" x2="520" y2="200" marker-end="url(#arrow)" style="stroke:#000;stroke-width:2"/>
<text x="300" y="180" text-anchor="middle" style="fill:#000">v</text>
</svg>"""


def _task() -> S.TaskSnapshot:
    return S.TaskSnapshot(
        question_id="q_enrich_1",
        question_revision=1,
        q_type=S.QuestionType.SHORT_ANSWER,
        stem="小车沿水平直线向右做匀速运动，速度大小为 v。说明速度方向。",
        answer="速度方向水平向右。",
        explanation="匀速直线运动的速度方向与运动方向一致。",
        rubric=[S.FrozenCriterion(id="c1", description="指出速度方向", weight=1.0)],
    )


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0), {"completion_tokens": 24}


class TestSvgCompatibility(unittest.TestCase):
    def test_defs_marker_and_inline_style_are_normalized(self):
        normalized = normalize_svg(SVG_WITH_MARKER, alt="向右速度箭头", caption="速度示意图")
        self.assertIn("<defs>", normalized.svg)
        self.assertIn("marker-end=\"url(#arrow)\"", normalized.svg)
        self.assertNotIn(" style=", normalized.svg)
        illustration = normalize_illustration({
            "kind": "svg",
            "alt": "向右速度箭头",
            "caption": "速度示意图",
            "svg": SVG_WITH_MARKER,
        })
        self.assertEqual(illustration.sanitizer_version, 2)
        self.assertTrue(illustration.content_hash.startswith("sha256:"))

    def test_external_marker_reference_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace("url(#arrow)", "url(https://example.com/a.svg#arrow)")
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)

    def test_style_element_stays_rejected(self):
        raw = SVG_WITH_MARKER.replace("<defs>", "<defs><style>line{stroke:red}</style>")
        with self.assertRaises(IllustrationValidationError):
            normalize_svg(raw)


class TestIllustrationEnrichment(unittest.IsolatedAsyncioTestCase):
    async def test_required_enrichment_generates_audits_and_caches_without_regenerating_question(self):
        generated = json.dumps({
            "illustration": {
                "kind": "svg",
                "alt": "小车速度箭头水平向右",
                "caption": "速度方向示意",
                "svg": SVG_WITH_MARKER,
            }
        }, ensure_ascii=False)
        audited = json.dumps({"status": "passed", "issues": []})
        llm = FakeLLM([generated, audited])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            enrichment._locks.clear()
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=llm)
            self.assertEqual(result["status"], "ready")
            self.assertIsNotNone(result["illustration"])
            self.assertEqual(llm.calls, 2)
            self.assertLessEqual(result["metrics"]["generation_calls"], 3)

            cached = await enrichment.generate_assessment_illustration(
                student_id="usr_enrich", task=_task(), policy="required", llm=FakeLLM([]))
            self.assertEqual(cached["status"], "ready")
            self.assertEqual(cached["metrics"]["cache_hit"], 1)
            self.assertEqual(cached["metrics"]["generation_calls"], 0)

    async def test_required_null_is_failure_not_text_question_regeneration(self):
        llm = FakeLLM([json.dumps({"illustration": None})])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(enrichment, "_STUDENTS_DIR", Path(tmp)):
            enrichment._locks.clear()
            result = await enrichment.generate_assessment_illustration(
                student_id="usr_required_null", task=_task(), policy="required", llm=llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "illustration_required_missing")
        self.assertEqual(llm.calls, 1)


if __name__ == "__main__":
    unittest.main()
