"""课堂检查点题模板回归（plan.md D03 / §13.2）。

覆盖：standard 密度经既有 generate_verified_questions 出正式题（结构
过滤 + critic 语义保留）；出题失败降级 reflect 讲授模式；density=none
零检查点；题模板只进私有材料、答案不出现在页面/讲稿（answer_leak 门）；
生成期不注册任何学生作答。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import validation  # noqa: E402
from app.classroom.checkpoints import author_question_checkpoint  # noqa: E402
from app.classroom.pipeline import (ClassroomPipeline,  # noqa: E402
                                    PipelineDeps)
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, PipelineTestBase, WS  # noqa: E402


@dataclass
class _OkReport:
    ok: bool = True
    issues: list = field(default_factory=list)


class AuthorQuestionTests(unittest.IsolatedAsyncioTestCase):
    def _slide(self, marker: str) -> sc.SlideSpec:
        return sc.SlideSpec(
            slide_id=f"s_{marker * 12}", order=5, title="动量守恒判定",
            layout=sc.SlideLayout.checkpoint,
            blocks=[{"kind": "paragraph", "id": f"blk_{marker * 24}",
                     "spans": [{"kind": "text", "text": "判断系统动量。"}]}],
            segments=[{"segment_id": f"seg_{marker * 24}",
                       "role": "explain", "display_text": "讲",
                       "spoken_text": "我们来讲判定方法。"}])

    async def test_author_returns_verified_question_template(self) -> None:
        fake = FakeClassroomLLM(with_questions=True)
        brief = sc.LessonBrief(topic="动量守恒", grade="高中")
        templates, status = await author_question_checkpoint(
            llm=fake, brief=brief, slide=self._slide("a"),
            checkpoint_id="ckp_" + "a" * 24,
            evidence_text='<material_excerpt source_id="src_' + "a" * 24
                          + '">合外力为零时系统总动量保持不变。'
                          '</material_excerpt>')
        self.assertEqual(status, "question")
        self.assertEqual(len(templates), 1)
        template = templates[0]
        self.assertEqual(template.kind, sc.CheckpointKind.question)
        self.assertTrue(template.optional)
        self.assertIn("合外力", template.prompt)
        question = template.verified_question_template
        self.assertEqual(question["answer"], "A")
        self.assertIn("explanation", question)

    async def test_author_parse_failure_falls_back(self) -> None:
        fake = FakeClassroomLLM()  # 不带 with_questions：出题轮输出无效
        brief = sc.LessonBrief(topic="动量守恒", grade="高中")
        templates, status = await author_question_checkpoint(
            llm=fake, brief=brief, slide=self._slide("b"),
            checkpoint_id="ckp_" + "b" * 24)
        self.assertEqual(status, "reflect_fallback")
        self.assertEqual(templates, [])


class CheckpointPipelineTests(PipelineTestBase):
    def _run(self, lesson_id, job_id, deps):
        return asyncio.run(
            ClassroomPipeline(OWNER, WS, lesson_id, job_id, deps).run())

    def test_standard_density_publishes_question_checkpoint(self) -> None:
        import json as _json
        lesson_id, job_id = self._make_job()  # 默认 checkpoint_density=standard
        deps = PipelineDeps(llm=FakeClassroomLLM(with_questions=True),
                            layout_check=lambda h: _OkReport())
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        questions = [t for t in revision.checkpoint_templates
                     if t.kind == sc.CheckpointKind.question]
        self.assertEqual(len(questions), 1)
        template = questions[0]
        self.assertEqual(template.verified_question_template["answer"], "A")
        # 答案绝不出现在页面/讲稿（answer_leak 门在 review 已把关）
        corpus = "\n".join(
            [s.title for s in revision.slides]
            + [seg.spoken_text for s in revision.slides
               for seg in s.segments])
        self.assertNotIn("不断增大", corpus)
        # 模板只进私有 spec（spec.private.json）
        spec = _json.loads(
            (store.revision_dir(OWNER, WS, lesson_id, 1)
             / "spec.private.json").read_text(encoding="utf-8"))
        self.assertTrue(any(t.get("kind") == "question"
                            for t in spec["checkpoint_templates"]))

    def test_author_crash_degrades_to_reflect(self) -> None:
        lesson_id, job_id = self._make_job()

        async def broken_author(**_kwargs):
            raise RuntimeError("quiz service down")

        deps = PipelineDeps(llm=FakeClassroomLLM(),
                            layout_check=lambda h: _OkReport(),
                            checkpoint_author=broken_author)
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.succeeded,
                         "optional 检查点出题失败必须可发布（讲授模式）")
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        self.assertTrue(revision.checkpoint_templates)
        self.assertTrue(all(t.kind == sc.CheckpointKind.reflect
                            for t in revision.checkpoint_templates))

    def test_density_none_has_no_checkpoints(self) -> None:
        lesson_id, job_id = self._make_job(
            self._brief(checkpoint_density="none"))
        deps = PipelineDeps(llm=FakeClassroomLLM(),
                            layout_check=lambda h: _OkReport())
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.succeeded)
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        self.assertEqual(revision.checkpoint_templates, [])
        for slide in revision.slides:
            self.assertFalse(
                any(getattr(b, "checkpoint_id", None) for b in slide.blocks))


class AnswerLeakGateTests(unittest.TestCase):
    def test_answer_leak_blocks_publish_gate(self) -> None:
        slide = sc.SlideSpec(
            slide_id="s_" + "c" * 12, order=1, title="答案泄漏测试页",
            layout=sc.SlideLayout.checkpoint,
            blocks=[{"kind": "paragraph", "id": "blk_" + "c" * 24,
                     "spans": [{"kind": "text",
                                "text": "先自己判断，稍后核对。"}]}],
            segments=[{"segment_id": "seg_" + "c" * 24, "role": "explain",
                       "display_text": "提示", "spoken_text": "答案保持不变。"}])
        template = sc.CheckpointTemplate(
            checkpoint_id="ckp_" + "c" * 24,
            slide_id=slide.slide_id,
            kind=sc.CheckpointKind.question, prompt="系统总动量如何变化？",
            verified_question_template={"answer": "保持不变"},
            optional=True)
        issues = validation.structural_gate([slide], [template])
        self.assertTrue(any(i.code == "answer_leak" for i in issues),
                        "答案出现在讲稿必须被结构门拦截")
        self.assertTrue(validation.has_blocker(issues))


if __name__ == "__main__":
    unittest.main()
