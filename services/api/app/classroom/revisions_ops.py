"""revision 操作的纯函数实现（D05；零 LLM 派生）。"""
from __future__ import annotations

from typing import Any

from ..schemas import classroom as sc
from .errors import ClassroomError


def find_editable_block(base: sc.LessonRevision, slide_id: str, block_id: str):
    slide = next((s for s in base.slides if s.slide_id == slide_id), None)
    block = next((b for b in slide.blocks if b.id == block_id), None) if slide else None
    if block is None or block.kind == "checkpoint":
        raise ClassroomError("content_invalid", "目标组件不存在或为受保护的随堂题")
    return slide, block


def apply_block_change(base: sc.LessonRevision, slide_id: str,
                       block_id: str, replacement) -> sc.LessonRevision:
    slide, block = find_editable_block(base, slide_id, block_id)
    if replacement.id != block.id or replacement.kind != block.kind:
        raise ClassroomError("content_invalid", "组件 ID 和类型不得改变")
    if block.kind == "image" and replacement.asset_id != block.asset_id:
        raise ClassroomError("content_invalid", "更换图片请使用换图操作")
    # 组件编辑不允许伪造来源；原有来源只能保留或缩小。
    old_data = block.diagram if block.kind == "diagram" else block
    new_data = replacement.diagram if replacement.kind == "diagram" else replacement
    if not set(getattr(new_data, "source_ids", [])) <= set(getattr(old_data, "source_ids", [])):
        raise ClassroomError("content_invalid", "组件编辑不得添加来源引用")
    updated = slide.model_copy(update={"blocks": [
        replacement if b.id == block_id else b for b in slide.blocks]})
    # 重新验证跨组件引用与布局，仍使用原有讲稿/题目/来源。
    updated = sc.SlideSpec.model_validate(updated.model_dump(mode="json", by_alias=True))
    return base.model_copy(update={"slides": [
        updated if s.slide_id == slide_id else s for s in base.slides]})


def _renumber(slides: list[sc.SlideSpec]) -> list[sc.SlideSpec]:
    for index, slide in enumerate(slides, start=1):
        slide.order = index
    return slides


def apply_theme_change(base: sc.LessonRevision,
                       theme_id: str) -> sc.LessonRevision:
    """换主题：只改 brief.theme_id，内容/来源/资产原样（§4.3 零 LLM）。"""
    brief_dump = base.brief.model_dump(mode="json", by_alias=True)
    brief_dump["theme_id"] = theme_id
    brief = sc.LessonBrief.model_validate(brief_dump)
    return base.model_copy(update={"brief": brief})


def apply_edit_changes(base: sc.LessonRevision,
                       changes: list[Any]) -> sc.LessonRevision:
    """edit_content：整页替换 / 删除页 / 重排序（顺序应用，最后统一重编号）。"""
    slides = list(base.slides)
    for change in changes:
        if change.op == "replace_block":
            draft = apply_block_change(base.model_copy(update={"slides": slides}),
                                       change.slide_id, change.block_id, change.block)
            slides = draft.slides
        elif change.op == "replace_slide":
            positions = [i for i, s in enumerate(slides)
                         if s.slide_id == change.slide_id]
            if not positions:
                raise ClassroomError("content_invalid",
                                     f"替换目标页 {change.slide_id} 不存在")
            replacement = change.slide
            replacement.order = slides[positions[0]].order
            slides[positions[0]] = replacement
        elif change.op == "delete_slide":
            slides = [s for s in slides if s.slide_id != change.slide_id]
        elif change.op == "reorder_slides":
            by_id = {s.slide_id: s for s in slides}
            if sorted(change.page_ids) != sorted(by_id.keys()):
                raise ClassroomError("content_invalid",
                                     "page_ids 必须是当前页集合的排列")
            slides = [by_id[sid] for sid in change.page_ids]
    if not slides:
        raise ClassroomError("content_invalid", "删除后至少保留一页")
    return base.model_copy(update={"slides": _renumber(slides)})


def apply_replace_image(base: sc.LessonRevision, slide_id: str,
                        block_id: str, *, asset: sc.AssetRecord,
                        alt: str = "", keep_caption: bool = True,
                        ) -> sc.LessonRevision:
    """replace_image：换掉目标图片块的 asset（页面其余内容不变）。"""
    slides = list(base.slides)
    target_index = next((i for i, s in enumerate(slides)
                         if s.slide_id == slide_id), None)
    if target_index is None:
        raise ClassroomError("content_invalid", "目标页不存在")
    slide = slides[target_index]
    blocks = []
    replaced = False
    for block in slide.blocks:
        if block.id == block_id and getattr(block, "kind", "") == "image":
            replacement = block.model_copy(update={
                "asset_id": asset.asset_id,
                "alt": (alt or asset.alt or block.alt)[:500]})
            blocks.append(replacement)
            replaced = True
        else:
            blocks.append(block)
    if not replaced:
        raise ClassroomError("content_invalid", "目标图片块不存在")
    slides[target_index] = slide.model_copy(update={"blocks": blocks})
    assets = list(base.assets)
    if asset.asset_id not in {a.asset_id for a in assets}:
        assets.append(asset)
    return base.model_copy(update={"slides": _renumber(slides),
                                   "assets": assets})
