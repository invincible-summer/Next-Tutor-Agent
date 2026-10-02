"""Bounded component editing: one structured call, no deck HTML or full slides."""
from __future__ import annotations

import json

from pydantic import ConfigDict, create_model

from ..schemas import classroom as sc
from .errors import ClassroomError
from .llm_io import generate_json
from .revisions_ops import apply_block_change, find_editable_block


def block_context(base: sc.LessonRevision, op: sc.RegenerateBlockOperation) -> dict:
    slide, block = find_editable_block(base, op.slide_id, op.block_id)
    segments = [s for s in slide.segments
                if block.id in s.show_block_ids or block.id in s.focus_block_ids][:3]
    source_ids = set(slide.source_ids)
    block_data = block.diagram if block.kind == "diagram" else block
    source_ids.update(getattr(block_data, "source_ids", []))
    for segment in segments:
        source_ids.update(segment.source_ids)
    for claim in slide.claims:
        if block.id in claim.block_ids:
            source_ids.update(claim.source_ids)
    records = [r for r in base.source_snapshot if r.source_id in source_ids][:3]
    return {
        "instruction": op.instruction,
        "topic": base.brief.topic,
        "page_title": slide.title,
        "objectives": [o.text for o in base.objectives
                       if o.objective_id in slide.learning_objective_ids][:4],
        "related_narration": [s.display_text[:300] for s in segments],
        "block": block.model_dump(mode="json", by_alias=True),
        "block_schema": type(block).model_json_schema(by_alias=True),
        "evidence": [{"source_id": r.source_id, "excerpt": r.excerpt[:1000]}
                     for r in records],
    }


async def regenerate_block(llm, base: sc.LessonRevision,
                           op: sc.RegenerateBlockOperation) -> sc.LessonRevision:
    _, block = find_editable_block(base, op.slide_id, op.block_id)
    payload = json.dumps(block_context(base, op), ensure_ascii=False)
    if len(payload) > 24000:
        raise ClassroomError("content_invalid", "组件内容较长，请先手动精简后再优化")
    result_model = create_model("BlockEditResult", __config__=ConfigDict(extra="forbid"),
                                block=(type(block), ...))
    result, _ = await generate_json(
        llm, prompt_id="classroom_block", prompt_version="1.0.0",
        user_text=payload, model_cls=result_model,
        repair_attempts=0, max_tokens=3000)
    return apply_block_change(base, op.slide_id, op.block_id, result.block)
