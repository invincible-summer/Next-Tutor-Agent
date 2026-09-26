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

    def test_budget_accounting_stays_within_wall_time(self) -> None:
        """预阶段 persist 只落计数：active_seconds_used 不得累计开机时长
        （曾因传 0.0 把 monotonic() 当差值累计，秒数高达机器 uptime）。"""
        lesson_id, job_id = self._make_job()
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, self._deps()).run())
        job = store.load_job(OWNER, WS, lesson_id, job_id)
        self.assertLess(job.budget.active_seconds_used, 3600)

    def test_outline_parser_accepts_locked_prompt_contract(self) -> None:
        """提示词原文（§6.5）输出 page_plan/working_title/must_show 等字段名，
        解析器必须直接吃下（真实模型回归；fake LLM 输出内部名曾掩盖此契约）。"""
        raw = {
            "objectives": [{"objective_id": "obj_1", "text": "会判断动量守恒",
                            "evidence_status": "supported", "bloom": "understand"}],
            "prerequisites": [{"concept": "矢量", "status": "confirmed"}],
            "page_plan": [{
                "page": 1, "layout": "title", "working_title": "动量守恒",
                "objective_ids": ["obj_1"], "needs_evidence": [],
                "visual_intent": {"role": "scene", "purpose": "碰撞示意",
                                  "must_show": ["两个滑块"],
                                  "must_not_show": ["真实人物"],
                                  "aspect": "landscape",
                                  "query_hint": "physics collision diagram",
                                  "alt_hint": "两个滑块碰撞示意"},
                "estimated_minutes": 1.5}],
            "glossary": [{"term": "系统", "definition": "研究对象整体"}],
            "budget_note": "导入 1 分钟",
        }
        model = pl._OutlineModel.model_validate(raw)
        page = model.pages[0]
        self.assertEqual(page.title, "动量守恒")
        self.assertEqual(page.order, 1)
        self.assertEqual(page.budget_seconds, 90)
        self.assertEqual(page.visual_intent.required_objects, ["两个滑块"])
        self.assertEqual(page.visual_intent.exclude, ["真实人物"])
        self.assertEqual(page.visual_intent.orientation, "landscape")
        self.assertEqual(page.visual_intent.query_terms,
                         ["physics collision diagram"])
        self.assertEqual(page.visual_intent.alt, "两个滑块碰撞示意")
        self.assertEqual(model.scope_note, "导入 1 分钟")

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


class SpanNormalizationTests(unittest.TestCase):
    """真实 LLM 超限 span 序列的确定性修复（严格校验前）。"""

    def _slide_payload(self, n_spans: int) -> dict:
        return {
            "slide": {
                "slide_id": "s_%012x" % 1, "order": 1,
                "title": "动量守恒的条件",
                "layout": "key_points",
                "learning_objective_ids": [],
                "blocks": [{
                    "id": "blk_%024x" % 1, "kind": "paragraph",
                    "spans": [{"kind": "text", "text": f"第{i}段内容"}
                               for i in range(n_spans)],
                }],
                "segments": [{
                    "segment_id": "seg_%024x" % 1, "role": "explain",
                    "display_text": "讲稿", "spoken_text": "讲稿",
                    "show_block_ids": ["blk_%024x" % 1],
                    "focus_block_ids": [],
                    "pause_after_ms": 0, "source_ids": [],
                    "estimated_ms": 1000,
                }],
                "claims": [], "source_ids": [], "transition": "auto",
                "estimated_seconds": 1,
            },
            "claims": [],
        }

    def test_over_limit_text_spans_merged_and_content_kept(self):
        from app.classroom.validation import normalize_slide_spans
        payload = self._slide_payload(9)   # 上限 8
        normalize_slide_spans(payload)
        spans = payload["slide"]["blocks"][0]["spans"]
        self.assertLessEqual(len(spans), 8)
        merged_text = "".join(sp["text"] for sp in spans)
        self.assertEqual(merged_text,
                         "".join(f"第{i}段内容" for i in range(9)))

    def test_generate_json_with_pre_validate_recovers_nine_spans(self):
        # 集成：stub LLM 固定输出 9-span 段落；无 pre_validate 时两次都失败，
        # 有 pre_validate 时一次通过（不触发修复重试）。
        import asyncio
        from app.classroom import llm_io
        from app.classroom.errors import ClassroomError
        from app.classroom.validation import normalize_slide_spans
        from pydantic import BaseModel

        class _Wrap(BaseModel):
            slide: sc.SlideSpec
            claims: list = []

        payload = self._slide_payload(9)
        raw = json.dumps(payload, ensure_ascii=False)
        calls = {"n": 0}

        class StubLLM:
            async def complete(self, messages, **_):
                calls["n"] += 1
                return raw, None

        with self.assertRaises(ClassroomError):
            asyncio.run(llm_io.generate_json(
                StubLLM(), prompt_id="classroom_slide",
                user_text="u", model_cls=_Wrap))
        self.assertEqual(calls["n"], 2)   # 1 次生成 + 1 次修复重试

        calls["n"] = 0
        model, _ = asyncio.run(llm_io.generate_json(
            StubLLM(), prompt_id="classroom_slide",
            user_text="u", model_cls=_Wrap,
            pre_validate=normalize_slide_spans))
        self.assertEqual(calls["n"], 1)
        block = model.slide.blocks[0]
        self.assertEqual(block.kind, "paragraph")
        self.assertLessEqual(len(block.spans), 8)
        self.assertIn("第8段内容", block.spans[-1].text
                      if hasattr(block.spans[-1], "text") else "")

    def test_alternating_math_text_not_silently_dropped(self):
        # 交错 math/text 无法安全合并到上限时保持原样（严格校验报错，
        # 不静默丢内容）；math+math 可合并。
        from app.classroom.validation import (
            _normalize_span_list, _SPAN_CAPS)
        alt = []
        for i in range(9):
            if i % 2 == 0:
                alt.append({"kind": "math", "latex": f"x_{i}",
                            "spoken": f"x {i}"})
            else:
                alt.append({"kind": "text", "text": f"，其中 t{i}"})
        out = _normalize_span_list(alt, _SPAN_CAPS["paragraph"])
        self.assertGreater(len(out), 8)   # 不做危险合并
        only_math = [{"kind": "math", "latex": f"m{i}",
                      "spoken": f"m {i}"} for i in range(9)]
        out2 = _normalize_span_list(only_math, 8)
        self.assertLessEqual(len(out2), 8)


class LeakThenRepairLLM(FakeClassroomLLM):
    """checkpoint 页讲稿泄露答案；第一次单页修复仍泄露，第二次干净。"""

    LEAK = "合外力为零时系统总动量保持不变，这就是标准答案。"
    CLEAN = "先别翻页，回想一下刚才的推导，下一页再对答案。"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.repair_calls = 0

    async def complete(self, messages, **_):
        system = messages[0]["content"]
        out_json, usage = await super().complete(messages, **_)
        if "任务：单页写作" in system:
            data = json.loads(out_json)
            if (data.get("slide") or {}).get("layout") == "checkpoint":
                for seg in data["slide"]["segments"]:
                    seg["spoken_text"] = self.LEAK
                    seg["display_text"] = self.LEAK
            return json.dumps(data, ensure_ascii=False), usage
        return out_json, usage

    def _repair(self, payload: dict) -> dict:
        # 只改讲稿文本、不动块（保住 checkpoint 布局必需块）：第一次仍泄露，
        # 第二次换干净讲稿——驱动有界修复循环的两轮收敛。
        self.repair_calls += 1
        original = (payload.get("original") or {}).get("slide")             or payload.get("original") or {}
        fixed = json.loads(json.dumps(original))
        text = self.LEAK if self.repair_calls == 1 else self.CLEAN
        for seg in fixed.get("segments", []):
            seg["spoken_text"] = text
            seg["display_text"] = text
        return {"slide": fixed, "claims": []}


async def _leaky_question_author(*, brief, slide, checkpoint_id,
                                 evidence_text):
    """注入正式题模板（答案=LEAK），使泄漏门可命中讲稿。"""
    template = sc.CheckpointTemplate(
        checkpoint_id=checkpoint_id, slide_id=slide.slide_id,
        kind=sc.CheckpointKind.question,
        prompt="系统所受合外力为零时，系统总动量如何变化？",
        verified_question_template={
            "type": "multiple_choice",
            "stem": "系统所受合外力为零时，系统总动量如何变化？",
            "options": {"A": "保持不变", "B": "不断增大"},
            "answer": LeakThenRepairLLM.LEAK,
            "explanation": "由动量定理，合外力为零则总动量不变。",
            "knowledge_point": "动量守恒", "difficulty": "easy"})
    return [template], "question"


class AnswerLeakRepairLoopTests(PipelineTestBase):
    """answer_leak blocker：按页定位 + 有界修复循环自动收敛（不整课重试）。"""

    def test_leaky_checkpoint_narration_auto_repaired_per_slide(self):
        lesson_id, job_id = self._make_job()
        fake = LeakThenRepairLLM()
        job = asyncio.run(self._pipeline(lesson_id, job_id, self._deps(
            fake, checkpoint_author=_leaky_question_author)).run())
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertEqual(lesson.latest_ready_revision, 1)
        # 两轮单页修复：第一次仍泄露，第二次干净——循环生效而非硬失败
        self.assertEqual(fake.repair_calls, 2)
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        for slide in revision.slides:
            for seg in slide.segments:
                self.assertNotIn(LeakThenRepairLLM.LEAK[:12], seg.spoken_text)
        # checkpoint 页讲稿已是干净文本
        template = revision.checkpoint_templates[0]
        host = next(s for s in revision.slides
                    if s.slide_id == template.slide_id)
        self.assertIn(LeakThenRepairLLM.CLEAN[:8],
                      host.segments[0].spoken_text)

    def test_answer_leak_issue_carries_slide_id(self):
        # 定位基础：gate 产出的 issue 挂在 checkpoint 页上（按页修复的前提）
        from app.classroom import validation
        slide = sc.SlideSpec(
            slide_id="s_%012x" % 7, order=7, title="检查",
            layout=sc.SlideLayout.checkpoint,
            learning_objective_ids=[],
            blocks=[sc.ParagraphBlock(
                id="blk_%024x" % 1,
                spans=[sc.SpanText(text="答案是保持不变")])],
            segments=[sc.NarrationSegment(
                segment_id="seg_%024x" % 1, role=sc.SegmentRole.explain,
                display_text="答案是保持不变", spoken_text="答案是保持不变",
                show_block_ids=["blk_%024x" % 1], focus_block_ids=[],
                pause_after_ms=0, source_ids=[], estimated_ms=1000)],
            claims=[], source_ids=[], transition=sc.TransitionKind.auto,
            estimated_seconds=1)
        slide.blocks.append(sc.CheckpointBlock(
            id="blk_%024x" % 2, checkpoint_id="ckp_%024x" % 1))
        template = sc.CheckpointTemplate(
            checkpoint_id="ckp_%024x" % 1, slide_id=slide.slide_id,
            kind=sc.CheckpointKind.question,
            prompt="动量如何变化？",
            verified_question_template={"answer": "保持不变"})
        issues = validation._answer_leak_gate([slide], [template])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "answer_leak")
        self.assertEqual(issues[0].slide_id, slide.slide_id)


class NeverFixLeakLLM(LeakThenRepairLLM):
    """修复永远不收敛（每次都仍泄露）→ 触发确定性净化兜底。"""

    def _repair(self, payload: dict) -> dict:
        self.repair_calls += 1
        original = (payload.get("original") or {}).get("slide")             or payload.get("original") or {}
        fixed = json.loads(json.dumps(original))
        for seg in fixed.get("segments", []):
            seg["spoken_text"] = self.LEAK
            seg["display_text"] = self.LEAK
        return {"slide": fixed, "claims": []}


class StubbornReviewerLLM(LeakThenRepairLLM):
    """复核器固执地把 checkpoint 页判为 answer_leak blocker（修复后也是）
    ——旧实现里这是死锁（stale verdict 永不消失）；现在修复后重跑复核 +
    净化兜底应能收敛发布。"""

    async def complete(self, messages, **_):
        system = messages[0]["content"]
        if "任务：整课复核" in system:
            import json as _json
            slides = _json.loads(messages[-1]["content"]).get("slides", [])
            host = next((s for s in slides
                         if s.get("layout") == "checkpoint"), None)
            issues = []
            if host:
                issues.append({
                    "code": "answer_leak", "severity": "blocker",
                    "slide_id": host["slide_id"],
                    "field_path": "segments",
                    "reason": "讲稿疑似提前给出答案（stub 复核器固定判定）"})
            return _json.dumps({"issues": issues,
                                "summary": "stub"}, ensure_ascii=False),                 {"prompt_tokens": 1, "completion_tokens": 1}
        return await super().complete(messages, **_)


class AnswerLeakFallbackTests(PipelineTestBase):
    """修复轮不收敛时：净化兜底保证课程可发布，讲稿不含答案。"""

    def _publish_with(self, fake) -> None:
        lesson_id, job_id = self._make_job()
        pipe = self._pipeline(lesson_id, job_id, self._deps(
            fake, checkpoint_author=_leaky_question_author))
        job = asyncio.run(pipe.run())
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertEqual(lesson.latest_ready_revision, 1)
        revision = store.load_revision(OWNER, WS, lesson_id, 1)
        for slide in revision.slides:
            for seg in slide.segments:
                self.assertNotIn(LeakThenRepairLLM.LEAK[:12],
                                 seg.spoken_text)
        self.assertTrue(any("净化" in w for w in pipe.warnings))

    def test_repair_never_converges_falls_back_to_sanitized_guide(self):
        fake = NeverFixLeakLLM()
        self._publish_with(fake)
        # 修复轮确实跑满（兜底只在轮次用尽后介入）
        self.assertEqual(fake.repair_calls, 2)

    def test_stubborn_reviewer_no_longer_deadlocks(self):
        self._publish_with(StubbornReviewerLLM())


if __name__ == "__main__":
    unittest.main()
