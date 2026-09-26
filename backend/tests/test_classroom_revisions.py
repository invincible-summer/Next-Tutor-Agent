"""课堂 revision operations 回归（plan.md D05 / §14.1/§4.3）。

五种 operation 全部从已发布 base 派生：旧版本保留可读；换主题/编辑/
换图零 LLM（严格证明：LLM 一旦被调用即让测试失败）；重生成单页走完整
质量门；刷新来源只追加新 web 记录；受理时校验目标完整性与幂等。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.classroom_fake_llm import FakeClassroomLLM  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import revisions as rev  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.classroom.pipeline import (ClassroomPipeline,  # noqa: E402
                                    PipelineDeps)
from app.classroom.research.base import (ExtractOutcome,  # noqa: E402
                                         ExtractedPage, SearchHit)
from app.core import classroom_store as store  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, PipelineTestBase, WS  # noqa: E402


@dataclass
class _OkReport:
    ok: bool = True
    issues: list = field(default_factory=list)


class NoLLM:
    """零 LLM 证明器：一旦被调用即抛错。"""

    async def complete(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("该修订操作必须零 LLM")


class FakeResearchProvider:
    name = "fake"

    async def search(self, query, *, timeliness="basic", owner="",
                     budget=None):
        return [SearchHit(title="动量新证", url="https://example.edu/new",
                          snippet="新的教学参考")]

    async def extract(self, urls, *, owner="", budget=None):
        return ExtractOutcome(pages=[
            ExtractedPage(url="https://example.edu/new",
                          canonical_url="https://example.edu/new",
                          title="动量新证（示例）",
                          text="新检索到的补充证据内容。",
                          retrieved_at=datetime(2026, 9, 26,
                                                tzinfo=timezone.utc))])


def _deps(**overrides) -> PipelineDeps:
    values = dict(llm=FakeClassroomLLM(),
                  layout_check=lambda html: _OkReport())
    values.update(overrides)
    return PipelineDeps(**values)


class RevisionTestBase(PipelineTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        patcher = mock.patch.object(settings, "classroom_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)
    def _published_course(self) -> tuple[str, str, sc.LessonRevision]:
        lesson_id, job_id = self._make_job()
        asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, job_id, _deps()).run())
        base = store.load_revision(OWNER, WS, lesson_id, 1)
        assert base is not None
        return lesson_id, job_id, base

    def _request(self, base_revision: int, operation) -> sc.CreateRevisionRequest:
        return sc.CreateRevisionRequest(base_revision=base_revision,
                                        operation=operation)

    def _run_operation(self, lesson_id: str, request, deps=None,
                       key: str = "") -> sc.GenerationJob:
        import uuid
        result = rev.create_revision_job(
            OWNER, WS, lesson_id, request,
            idempotency_key=key or f"rev-{uuid.uuid4().hex[:12]}")
        job = asyncio.run(ClassroomPipeline(
            OWNER, WS, lesson_id, result["job_id"],
            deps or _deps()).run())
        return job


class FastOperationTests(RevisionTestBase):
    def test_change_theme_zero_llm_keeps_old_revision(self) -> None:
        lesson_id, _job0, base = self._published_course()
        op = sc.ChangeThemeOperation(theme_id="chalk_focus@1")
        deps = PipelineDeps(llm=NoLLM(),
                            layout_check=lambda h: _OkReport())
        job = self._run_operation(lesson_id, self._request(1, op), deps)
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertEqual(lesson.published_revisions, [1, 2])
        self.assertEqual(lesson.latest_ready_revision, 2)
        v2 = store.load_revision(OWNER, WS, lesson_id, 2)
        self.assertEqual(v2.brief.theme_id, "chalk_focus@1")
        self.assertEqual([s.slide_id for s in v2.slides],
                         [s.slide_id for s in base.slides],
                         "换主题不得改动内容")
        v1 = store.load_revision(OWNER, WS, lesson_id, 1)
        self.assertEqual(v1.brief.theme_id, "academic_clear@1",
                         "旧版本保持原样可用")

    def test_edit_content_delete_and_renumber(self) -> None:
        lesson_id, _job0, base = self._published_course()
        last = max(base.slides, key=lambda s: s.order)
        op = sc.EditContentOperation(changes=[
            sc.DeleteSlideChange(slide_id=last.slide_id)])
        job = self._run_operation(lesson_id, self._request(1, op),
                                  PipelineDeps(llm=NoLLM(),
                                               layout_check=lambda h: _OkReport()))
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        v2 = store.load_revision(OWNER, WS, lesson_id, 2)
        self.assertEqual(len(v2.slides), len(base.slides) - 1)
        self.assertEqual([s.order for s in v2.slides],
                         list(range(1, len(v2.slides) + 1)))

    def test_edit_reorder_permutation_required(self) -> None:
        lesson_id, _job0, base = self._published_course()
        ids = [s.slide_id for s in base.slides]
        op = sc.EditContentOperation(changes=[
            sc.ReorderSlidesChange(page_ids=ids[:-1])])  # 少一页 → 非法
        with self.assertRaises(ClassroomError) as ctx:
            rev.create_revision_job(
                OWNER, WS, lesson_id, self._request(1, op),
                idempotency_key="rev-key-x")
        self.assertEqual(ctx.exception.code, "content_invalid")

    def test_edit_replace_unknown_slide_rejected(self) -> None:
        lesson_id, _job0, base = self._published_course()
        replacement = base.slides[0].model_copy(deep=True)
        op = sc.EditContentOperation(changes=[
            sc.ReplaceSlideChange(slide_id="s_" + "9" * 12,
                                  slide=replacement)])
        with self.assertRaises(ClassroomError):
            rev.create_revision_job(
                OWNER, WS, lesson_id, self._request(1, op),
                idempotency_key="rev-key-y")

    def test_replace_image_with_candidate(self) -> None:
        from app.classroom.media import service as media_service
        from app.classroom.media.base import ImageCandidate
        from app.classroom.media.download import ProcessedImage

        lesson_id, _job0, base = self._published_course()
        # 给 base 第一页注入一个图片块（通过直接派生一个带图中间版本）
        slide = base.slides[1]
        asset_id = "ast_" + "a" * 24
        image_block = sc.ImageBlock(
            id="blk_" + "f" * 24, asset_id=asset_id,
            alt="原始示意图", caption="配图说明")
        edited = sc.SlideSpec(**{
            **slide.model_dump(mode="json", by_alias=True),
            "blocks": slide.blocks + [image_block.model_dump(
                mode="json", by_alias=True)]})
        op = sc.EditContentOperation(changes=[
            sc.ReplaceSlideChange(slide_id=slide.slide_id, slide=edited)])
        job = self._run_operation(lesson_id, self._request(1, op),
                                  PipelineDeps(llm=NoLLM(),
                                               layout_check=lambda h: _OkReport()))
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        v2 = store.load_revision(OWNER, WS, lesson_id, 2)

        candidate = ImageCandidate(
            candidate_id="cand_" + "b" * 24, provider="pexels",
            provider_asset_id="123",
            download_url="https://images.pexels.com/photos/9/x.jpeg",
            thumb_url="", page_url="https://www.pexels.com/photo/9/",
            width=1600, height=1000, alt="替换后的新图",
            creator="摄影师", creator_url="", license_url="",
            locale="zh-CN")
        media_service.register_candidates(OWNER, [candidate])
        processed = ProcessedImage(
            data=b"\x89PNG\r\n\x1a\nfake", mime="image/png",
            width=64, height=48, sha256="a" * 64)
        # pipeline 在方法内部 from .media.download import → patch 源模块即可
        with mock.patch(
                "app.classroom.media.download.download_and_sanitize",
                new=mock.AsyncMock(return_value=processed)):
            op2 = sc.ReplaceImageOperation(
                slide_id=slide.slide_id,
                block_id=image_block.id,
                candidate_id=candidate.candidate_id)
            job2 = self._run_operation(
                lesson_id, self._request(2, op2),
                PipelineDeps(llm=NoLLM(),
                             layout_check=lambda h: _OkReport()))
        self.assertEqual(job2.state, sc.JobState.succeeded,
                         msg=str(job2.last_error))
        v3 = store.load_revision(OWNER, WS, lesson_id, 3)
        target = next(s for s in v3.slides
                      if s.slide_id == slide.slide_id)
        block = next(b for b in target.blocks if b.id == image_block.id)
        self.assertNotEqual(block.asset_id, asset_id, "图片块必须换新资产")
        self.assertEqual(block.alt, "替换后的新图")
        self.assertIn(block.asset_id, {a.asset_id for a in v3.assets})

    def test_replace_image_expired_candidate(self) -> None:
        lesson_id, _job0, base = self._published_course()
        op = sc.ReplaceImageOperation(
            slide_id=base.slides[0].slide_id, block_id="blk_" + "0" * 24,
            candidate_id="cand_" + "0" * 24)
        with self.assertRaises(ClassroomError):
            rev.create_revision_job(
                OWNER, WS, lesson_id, self._request(1, op),
                idempotency_key="rev-key-z")

    def test_refresh_research_appends_sources(self) -> None:
        lesson_id, _job0, base = self._published_course()
        op = sc.RefreshResearchOperation(scope="all")
        deps = PipelineDeps(
            llm=FakeClassroomLLM(research_queries=[
                {"purpose": "补最新资料", "query": "动量守恒 最新",
                 "timeliness": "week", "preferred_source": "university"}]),
            research=FakeResearchProvider(),
            layout_check=lambda h: _OkReport())
        job = self._run_operation(lesson_id, self._request(1, op), deps)
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        v2 = store.load_revision(OWNER, WS, lesson_id, 2)
        self.assertGreater(len(v2.source_snapshot),
                           len(base.source_snapshot),
                           "刷新后必须追加新 web 来源")
        self.assertTrue(any(r.kind == sc.SourceKind.web
                            for r in v2.source_snapshot))
        self.assertEqual([s.slide_id for s in v2.slides],
                         [s.slide_id for s in base.slides],
                         "刷新来源不改写页面内容")

    def test_refresh_without_provider_fails(self) -> None:
        lesson_id, _job0, _base = self._published_course()
        op = sc.RefreshResearchOperation(scope="all")
        job = self._run_operation(lesson_id, self._request(1, op),
                                  _deps())  # 无 research provider
        self.assertEqual(job.state, sc.JobState.failed)
        self.assertIn("research_unavailable", job.last_error or "")


class RegenerateSlideTests(RevisionTestBase):
    def test_single_page_regenerated_others_untouched(self) -> None:
        lesson_id, _job0, base = self._published_course()
        target = next(s for s in base.slides
                      if s.layout == sc.SlideLayout.key_points)
        op = sc.RegenerateSlideOperation(
            slide_id=target.slide_id,
            instruction="更贴近生活例子，降低抽象度")
        job = self._run_operation(lesson_id, self._request(1, op), _deps())
        self.assertEqual(job.state, sc.JobState.succeeded,
                         msg=str(job.last_error))
        v2 = store.load_revision(OWNER, WS, lesson_id, 2)
        self.assertEqual(len(v2.slides), len(base.slides))
        for old, new in zip(base.slides, v2.slides):
            if old.slide_id == target.slide_id:
                self.assertNotEqual(
                    new.segments[0].spoken_text, old.segments[0].spoken_text,
                    "目标页讲稿应被重写")
            else:
                self.assertEqual(
                    new.model_dump(mode="json"),
                    old.model_dump(mode="json"),
                    "非目标页必须逐字节不变")

    def test_regenerate_unknown_slide_rejected(self) -> None:
        lesson_id, _job0, _base = self._published_course()
        op = sc.RegenerateSlideOperation(slide_id="s_" + "9" * 12)
        with self.assertRaises(ClassroomError):
            rev.create_revision_job(
                OWNER, WS, lesson_id, self._request(1, op),
                idempotency_key="rev-key-r")


class RevisionJobContractTests(RevisionTestBase):
    def test_base_must_be_published(self) -> None:
        lesson_id, _job0, _base = self._published_course()
        op = sc.ChangeThemeOperation(theme_id="chalk_focus@1")
        with self.assertRaises(ClassroomError) as ctx:
            rev.create_revision_job(
                OWNER, WS, lesson_id, self._request(99, op),
                idempotency_key="rev-key-b")
        self.assertEqual(ctx.exception.code, "source_not_found")

    def test_idempotent_replay_returns_same_job(self) -> None:
        lesson_id, _job0, _base = self._published_course()
        op = sc.ChangeThemeOperation(theme_id="chalk_focus@1")
        first = rev.create_revision_job(
            OWNER, WS, lesson_id, self._request(1, op),
            idempotency_key="same-key")
        second = rev.create_revision_job(
            OWNER, WS, lesson_id, self._request(1, op),
            idempotency_key="same-key")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["revision"], second["revision"])
        lesson = store.load_lesson(OWNER, WS, lesson_id)
        self.assertEqual(lesson.next_revision, first["revision"] + 1,
                         "重放不得重复烧版本号")


if __name__ == "__main__":
    unittest.main()
