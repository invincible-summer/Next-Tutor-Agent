"""课堂五种 revision operation（plan.md §14.1/§4.3，D05）。

全部从已发布 base revision 派生（旧版本保持可用，published_revisions
只增不删）：
  - change_theme / edit_content / replace_image / refresh_research：
    零 LLM（refresh_research 只重跑检索），确定性门校验后直接重渲染发布；
  - regenerate_slide：单页经 classroom_slide 重写（含指令），其余页与
    检查点模板原样保留，随后走正常 review→render→publish 质量门。

edit_content 只允许整页替换/删除页/重排序（§14.1：禁止任意 JSON Patch
路径），单次 ≤50 项，目标完整性在受理时校验。
"""
from __future__ import annotations

import json
from typing import Any

from ..core import classroom_store as store
from ..schemas import classroom as sc
from . import idempotency
from . import sources
from .errors import ClassroomError
from .pipeline import ClassroomPipeline  # noqa: F401  (类型引用)
from .revisions_ops import (apply_edit_changes, apply_replace_image,
                            apply_theme_change)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _load_base(student_id: str, workspace_id: str, lesson_id: str,
               base_revision: int) -> sc.LessonRevision:
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.lifecycle != sc.LessonLifecycle.active:
        raise ClassroomError("source_not_found", "课程不存在")
    if base_revision not in lesson.published_revisions:
        raise ClassroomError("source_not_found",
                             f"修订基线版本 {base_revision} 未发布")
    revision = store.load_revision(student_id, workspace_id, lesson_id,
                                   base_revision)
    if revision is None:
        raise ClassroomError("source_not_found", "基线版本内容缺失")
    return revision


def validate_operation(base: sc.LessonRevision, operation: Any, *,
                       student_id: str = "", workspace_id: str = "",
                       lesson_id: str = "") -> None:
    """受理时校验目标完整性（slide/block/资产/主题存在、重排是排列）。"""
    slides = {s.slide_id: s for s in base.slides}
    op_name = operation.op
    if op_name == "edit_content":
        seen_kinds: set[str] = set()
        for change in operation.changes:
            kind = change.op
            if kind == "reorder_slides":
                if "reorder" in seen_kinds or seen_kinds - {"reorder"}:
                    raise ClassroomError(
                        "content_invalid", "重排序必须单独成项")
                if sorted(change.page_ids) != sorted(slides.keys()):
                    raise ClassroomError(
                        "content_invalid",
                        "page_ids 必须恰好是当前页集合的一个排列")
            elif kind == "replace_slide":
                if change.slide_id not in slides:
                    raise ClassroomError(
                        "content_invalid",
                        f"替换目标页 {change.slide_id} 不存在")
                if change.slide.layout != slides[change.slide_id].layout:
                    raise ClassroomError(
                        "content_invalid", "替换页不得变更布局（布局变更走重生成）")
            elif kind == "delete_slide":
                if change.slide_id not in slides:
                    raise ClassroomError(
                        "content_invalid",
                        f"删除目标页 {change.slide_id} 不存在")
            seen_kinds.add(kind)
    elif op_name == "change_theme":
        from .templates import theme_by_id
        if theme_by_id(operation.theme_id) is None:
            raise ClassroomError("content_invalid",
                                 f"未知视觉主题 {operation.theme_id}")
    elif op_name == "regenerate_slide":
        if operation.slide_id not in slides:
            raise ClassroomError("content_invalid",
                                 f"重生成目标页 {operation.slide_id} 不存在")
    elif op_name == "replace_image":
        slide = slides.get(operation.slide_id)
        if slide is None:
            raise ClassroomError("content_invalid", "目标页不存在")
        block = next((b for b in slide.blocks
                      if b.id == operation.block_id), None)
        if block is None or getattr(block, "kind", "") != "image":
            raise ClassroomError("content_invalid",
                                 "替换目标必须是该页的图片块")
        if bool(operation.candidate_id) == bool(operation.asset_id):
            raise ClassroomError("content_invalid",
                                 "candidate_id 与 asset_id 必须二选一")
        if operation.asset_id and operation.asset_id not in \
                {a.asset_id for a in base.assets}:
            # 上传资产（POST L/assets 落盘）也可引用（§14.1）
            from . import service as classroom_service
            if student_id and classroom_service.load_asset_record(
                    student_id, workspace_id, lesson_id,
                    operation.asset_id) is None:
                raise ClassroomError("content_invalid", "指定资产不存在")
            if not student_id and operation.asset_id not in \
                    {a.asset_id for a in base.assets}:
                raise ClassroomError("content_invalid", "指定资产不存在")
    elif op_name == "refresh_research":
        pass  # scope 已由枚举闭合
    else:  # pragma: no cover - 判别联合保证
        raise ClassroomError("content_invalid", "未知操作")


def create_revision_job(student_id: str, workspace_id: str, lesson_id: str,
                        request: sc.CreateRevisionRequest, *,
                        idempotency_key: str) -> dict[str, Any]:
    """POST L/revisions：校验 → 幂等/额度 → 分配版本号 → job(queued)。"""
    from . import capabilities as caps

    allowed, _ = caps.user_allowed(student_id)
    if not allowed:
        raise ClassroomError("classroom_disabled", "课堂功能未开放")
    from .service import load_owned_workspace
    load_owned_workspace(workspace_id, student_id)
    store.assert_owner_writable(student_id)

    base = _load_base(student_id, workspace_id, lesson_id,
                      request.base_revision)
    validate_operation(base, request.operation,
                       student_id=student_id,
                       workspace_id=workspace_id,
                       lesson_id=lesson_id)

    scope = "create_revision"
    body = {"lesson_id": lesson_id,
            "base_revision": request.base_revision,
            "operation": request.operation.model_dump(mode="json",
                                                      by_alias=True)}
    body_hash = idempotency.body_hash_of(body)
    replayed = idempotency.lookup(student_id, scope, idempotency_key,
                                  body_hash)
    if replayed:
        return replayed

    from .worker import assert_queue_capacity
    assert_queue_capacity(student_id)
    idempotency.check_generation_quota(student_id)

    job_id = store.new_id("job")
    target = store.allocate_revision(student_id, workspace_id, lesson_id,
                                     base_revision=request.base_revision)
    now = store.utcnow()
    brief_dump = _dump(base.brief)
    job = sc.GenerationJob(
        job_id=job_id, owner_id=student_id, workspace_id=workspace_id,
        lesson_id=lesson_id, base_revision=request.base_revision,
        target_revision=target, state=sc.JobState.queued,
        brief_hash=store.canonical_hash(brief_dump),
        operation=request.operation, created_at=now, updated_at=now)
    store.save_job(job)
    store.stage_file(
        store.job_root(student_id, workspace_id, lesson_id, job_id),
        "brief.json",
        json.dumps(brief_dump, ensure_ascii=False, indent=1))
    lesson = store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is not None:
        lesson.latest_job_id = job_id
        store.save_lesson(lesson)
        store.index_upsert_lesson(student_id, workspace_id, lesson, job=job)
    idempotency.consume_generation_quota(student_id)

    result = {
        "lesson_id": lesson_id,
        "job_id": job_id,
        "revision": target,
        "status_url": f"/api/v1/workspaces/{workspace_id}/classroom/"
                      f"lessons/{lesson_id}/jobs/{job_id}",
        "events_url": f"/api/v1/workspaces/{workspace_id}/classroom/"
                      f"lessons/{lesson_id}/jobs/{job_id}/events",
    }
    idempotency.remember(student_id, scope, idempotency_key, body_hash,
                         result)
    from . import service as classroom_service
    if classroom_service.enqueue_job is not None:
        try:
            classroom_service.enqueue_job(student_id, workspace_id,
                                          lesson_id, job_id)
        except Exception:
            pass  # 落盘 job 是事实源，启动扫描会恢复
    return result


# ---------------------------------------------------------------------------
# 零 LLM 操作的纯函数实现（revisions_ops 供 service 校验与 pipeline 复用）
# ---------------------------------------------------------------------------

__all__ = ["apply_edit_changes", "apply_replace_image",
           "apply_theme_change", "create_revision_job",
           "validate_operation", "sources"]
