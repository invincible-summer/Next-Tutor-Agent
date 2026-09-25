"""课堂生成管线回归（plan.md D02 退出门）。

fake LLM（无网络）从 Brief 跑通九阶段并发布 revision；逐阶段 crash 注入
后可恢复；取消不发布；坏 JSON / 未知来源 / 排版溢出都不会标 ready。
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import pipeline as pl  # noqa: E402
from app.classroom.pipeline import (ClassroomPipeline,  # noqa: E402
                                    PipelineCrash, PipelineDeps)
from app.core import classroom_store as store  # noqa: E402
from app.core import library as lib_mod  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

import asyncio  # noqa: E402

OWNER = "usr_pipeowner1"
WS = "ws_pipe_物理区"

WORKSPACE_TEXT = """动量守恒定律
系统不受外力或所受合外力为零时，系统总动量保持不变。
内力成对出现，不改变系统总动量。
适用条件与常见误区说明。
"""


@dataclass
class FakeLayoutReport:
    ok: bool
    issues: list = field(default_factory=list)


class PipelineTestBase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        lib = lib_mod.load_library(OWNER)
        self.ws_file = lib.add_file("", "动量讲义.txt", WORKSPACE_TEXT)
        lib_mod.save_library(lib)
        ws = ws_mod.Workspace(workspace_id=WS, name="物理",
                              student_id=OWNER,
                              workspace_file_ids=[self.ws_file["id"]])
        ws_mod.save_workspace(ws)

    def _make_job(self, brief: sc.LessonBrief | None = None,
                  *, start_mode: str = "automatic") -> tuple[str, str]:
        brief = brief or self._brief()
        lesson_id = store.new_id("les")
        job_id = store.new_id("job")
        now = store.utcnow()
        store.save_lesson(sc.Lesson(
            lesson_id=lesson_id, owner_id=OWNER, workspace_id=WS,
            title=brief.topic[:120], created_at=now, updated_at=now,
            latest_job_id=job_id))
        target = store.allocate_revision(OWNER, WS, lesson_id)
        store.save_job(sc.GenerationJob(
            job_id=job_id, owner_id=OWNER, workspace_id=WS,
            lesson_id=lesson_id, target_revision=target,
            brief_hash=store.canonical_hash(
                brief.model_dump(mode="json", by_alias=True)),
            start_mode=sc.StartMode(start_mode), created_at=now,
            updated_at=now))
        store.stage_file(
            store.job_root(OWNER, WS, lesson_id, job_id), "brief.json",
            json.dumps(brief.model_dump(mode="json", by_alias=True),
                       ensure_ascii=False))
        store.index_upsert_lesson(OWNER, WS,
                                  store.load_lesson(OWNER, WS, lesson_id))
        return lesson_id, job_id

    def _brief(self, **overrides) -> sc.LessonBrief:
        values = dict(
            topic="动量守恒与系统边界",
            goals=["会判断系统动量是否守恒", "会用守恒定律解碰撞问题"],
            source_selection=sc.SourceSelection(files=[
                sc.SourceFileSelection(file_id=self.ws_file["id"])]),
            source_policy="textbook_plus",
            research=sc.ResearchBrief(enabled=False),
            duration_minutes=15,
            page_plan="auto",
            language="zh",
            grade="高中",
            pedagogy_id="concept_deep@1",
            theme_id="academic_clear@1",
            image_density="none",
            checkpoint_density="standard",
            custom_requirements="",
        )
        values.update(overrides)
        return sc.LessonBrief(**values)

    def _deps(self, fake: FakeClassroomLLM | None = None, **kw):
        return PipelineDeps(llm=fake or FakeClassroomLLM(),
                            layout_check=lambda html: FakeLayoutReport(ok=True),
                            **kw)

    def _pipeline(self, lesson_id: str, job_id: str, deps) -> ClassroomPipeline:
        return ClassroomPipeline(OWNER, WS, lesson_id, job_id, deps)


class FullCourseTests(PipelineTestBase):
    def test_fake_llm_publishes_full_course(self) -> None:
        lesson_id, job_id = self._make_job()
        fake = FakeClassroomLLM()
        job = asyncio.run(self._pipeline(
            lesson_id, job_id, self._deps(fake)).run())
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertEqual(lesson.latest_ready_revision, 1)
        self.assertIn(1, lesson.published_revisions)
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        self.assertIsNotNone(revision)
        self.assertTrue(revision.slides)
        self.assertEqual([s.order for s in revision.slides],
                         list(range(1, len(revision.slides) + 1)))
        # 讲稿完整且无占位
        for slide in revision.slides:
            self.assertTrue(slide.segments)
            for seg in slide.segments:
                self.assertGreater(len(seg.spoken_text), 20)
        # 来源冻结来自授权工作区文件 + 讲稿引用了证据
        self.assertEqual(revision.source_snapshot[0].kind,
                         sc.SourceKind.workspace_file)
        cited = {sid for s in revision.slides for seg in s.segments
                 for sid in seg.source_ids}
        self.assertTrue(cited, "讲稿段应携带教材来源引用")
        # 检查点模板（reflect）已挂到 checkpoint 页
        self.assertTrue(revision.checkpoint_templates)
        template = revision.checkpoint_templates[0]
        host = next(s for s in revision.slides
                    if s.slide_id == template.slide_id)
        self.assertTrue(any(getattr(b, "checkpoint_id", None)
                            == template.checkpoint_id
                            for b in host.blocks))
        # prompt 版本可溯源 + 阶段输入 hash 已记录（checked artifact）
        self.assertIn("classroom_outline", revision.prompt_versions)
        self.assertTrue(job.stage_inputs.get("outline"))

    def test_outline_first_awaits_user(self) -> None:
        lesson_id, job_id = self._make_job(start_mode="outline_first")
        job = asyncio.run(self._pipeline(
            lesson_id, job_id, self._deps()).run())
        self.assertEqual(job.state, sc.JobState.awaiting_outline)
        outline = json.loads(
            (store.stages_dir(OWNER, WS, lesson_id, job_id)
             / "outline.json").read_text(encoding="utf-8"))
        self.assertTrue(outline["plan"]["pages"])
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertIsNone(lesson.latest_ready_revision)


class CrashRecoveryTests(PipelineTestBase):
    def _run(self, lesson_id, job_id, deps):
        return asyncio.run(self._pipeline(lesson_id, job_id, deps).run())

    def test_each_phase_crash_then_recovers(self) -> None:
        for phase in pl.PHASES:
            with self.subTest(phase=phase.value):
                lesson_id, job_id = self._make_job()
                deps = self._deps()
                deps.crash_at = phase.value
                with self.assertRaises(PipelineCrash):
                    self._run(lesson_id, job_id, deps)
                crashed = store.load_job(OWNER, WS, lesson_id, job_id)
                self.assertNotIn(crashed.state,
                                 (sc.JobState.succeeded, sc.JobState.failed),
                                 f"{phase} 崩溃后不得是终态成功/失败")
                deps.crash_at = None
                job = self._run(lesson_id, job_id, deps)
                self.assertEqual(job.state, sc.JobState.succeeded,
                                 msg=f"{phase}: {job.last_error}")
                self.assertEqual(
                    store.load_lesson(OWNER, WS, lesson_id)
                    .latest_ready_revision, 1)

    def test_cancel_after_crash_never_publishes(self) -> None:
        lesson_id, job_id = self._make_job()
        deps = self._deps()
        deps.crash_at = "author_slides"
        with self.assertRaises(PipelineCrash):
            self._run(lesson_id, job_id, deps)
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        job.cancel_requested = True
        store.save_job(job)
        deps.crash_at = None
        final = self._run(lesson_id, job_id, deps)
        self.assertEqual(final.state, sc.JobState.cancelled)
        self.assertIsNone(
            store.load_lesson(OWNER, WS, lesson_id).latest_ready_revision)


class QualityGateTests(PipelineTestBase):
    def _run(self, lesson_id, job_id, deps):
        return asyncio.run(self._pipeline(lesson_id, job_id, deps).run())

    def test_bad_json_fails_not_ready(self) -> None:
        lesson_id, job_id = self._make_job()
        deps = self._deps(FakeClassroomLLM(bad_json=True))
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.failed)
        self.assertIn("content_invalid", job.last_error or "")
        self.assertIsNone(
            store.load_lesson(OWNER, WS, lesson_id).latest_ready_revision)

    def test_unknown_source_fails_evidence_gate(self) -> None:
        lesson_id, job_id = self._make_job()
        deps = self._deps(FakeClassroomLLM(bogus_source=True))
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.failed)
        self.assertIn("content_invalid", job.last_error or "")

    def test_layout_overflow_blocks_publish(self) -> None:
        lesson_id, job_id = self._make_job()
        deps = self._deps()
        deps.layout_check = lambda html: FakeLayoutReport(ok=False)
        job = self._run(lesson_id, job_id, deps)
        self.assertEqual(job.state, sc.JobState.failed)
        self.assertIn("layout_overflow", job.last_error or "")
        self.assertIsNone(
            store.load_lesson(OWNER, WS, lesson_id).latest_ready_revision)

    def test_strict_without_sources_needs_input(self) -> None:
        brief = self._brief(
            source_policy="strict_textbook",
            source_selection=sc.SourceSelection(files=[
                sc.SourceFileSelection(file_id="missing_file0001")]))
        lesson_id, job_id = self._make_job(brief)
        job = self._run(lesson_id, job_id, self._deps())
        self.assertEqual(job.state, sc.JobState.needs_input)
        self.assertIsNone(
            store.load_lesson(OWNER, WS, lesson_id).latest_ready_revision)


if __name__ == "__main__":
    unittest.main()
