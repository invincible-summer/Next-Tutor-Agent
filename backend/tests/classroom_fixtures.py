"""课堂测试共享构造器：最小合法模型工厂（plan.md §19.1）。

只包含代码构造的确定性数据；真实教材/用户对话/云端返回一律不进 fixtures。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas import classroom as sc


def utcnow() -> datetime:
    return datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


def strict_update(model, updates: dict):
    """model_copy(update=) 不触发校验；测试约束时用这个真正走 validate。"""
    data = model.model_dump(mode="python", by_alias=True)
    data.update(updates)
    return type(model).model_validate(data)


def hex_id(prefix: str, n: int = 1) -> str:
    return f"{prefix}_{n:024x}"


def slide_hex(n: int = 1) -> str:
    return f"s_{n:012x}"


def make_span(text: str = "系统合外力为零") -> sc.SpanText:
    return sc.SpanText(text=text)


def make_para_block(n: int = 1, text: str = "动量守恒需要先选定系统。") -> sc.ParagraphBlock:
    return sc.ParagraphBlock(id=hex_id("blk", n), spans=[make_span(text)])


def make_formula_block(n: int = 2) -> sc.FormulaBlock:
    return sc.FormulaBlock(
        id=hex_id("blk", n),
        latex=r"\Delta \vec{P}=\vec{F}_{ext}\,\Delta t",
        spoken="系统总动量的变化等于合外力冲量。",
    )


def make_segment(n: int = 1, *, block_ids: list[str] | None = None,
                 spoken: str = "先把两个碰撞的小车看成一个系统，它们之间的相互作用是内力。"
                ) -> sc.NarrationSegment:
    return sc.NarrationSegment(
        segment_id=hex_id("seg", n),
        role=sc.SegmentRole.explain,
        display_text=spoken,
        spoken_text=spoken,
        show_block_ids=block_ids or [hex_id("blk", 1)],
        focus_block_ids=block_ids or [hex_id("blk", 1)],
        pause_after_ms=500,
        source_ids=[hex_id("src", 1)],
        estimated_ms=8000,
    )


def make_slide(n: int = 1, *, title: str = "动量守恒有一个前提",
               layout: sc.SlideLayout = sc.SlideLayout.derivation,
               blocks: list[Any] | None = None,
               segments: list[sc.NarrationSegment] | None = None,
               ) -> sc.SlideSpec:
    blocks = blocks or [make_formula_block(1), make_para_block(2)]
    block_ids = [b.id for b in blocks]
    segments = segments or [make_segment(1, block_ids=block_ids[:1]),
                            make_segment(2, block_ids=block_ids[1:2])]
    return sc.SlideSpec(
        slide_id=slide_hex(n),
        order=n,
        title=title,
        learning_objective_ids=["objective_1"],
        layout=layout,
        blocks=blocks,
        segments=segments,
        claims=[],
        source_ids=[hex_id("src", 1)],
        transition=sc.TransitionKind.auto,
        estimated_seconds=30,
    )


def make_file_source(n: int = 1, *, excerpt: str = "动量守恒定律：……"
                     ) -> sc.SourceRecord:
    return sc.SourceRecord(
        source_id=hex_id("src", n),
        kind=sc.SourceKind.textbook,
        title="大学物理 第一册",
        locator=sc.FileLocator(
            namespace="usr_test",
            file_id="file_abc",
            chunk_ids=["chunk_1"],
            page=88,
            printed_page="80",
            section_path=["第3章", "3.4 动量守恒"],
            content_hash="a" * 64,
        ),
        excerpt=excerpt,
        excerpt_hash="b" * 64,
        retrieved_at=utcnow(),
    )


def make_brief(*, topic: str = "动量守恒与系统边界",
               files: bool = True) -> sc.LessonBrief:
    selection = sc.SourceSelection(
        files=[sc.SourceFileSelection(
            file_id="file_abc",
            chapters=[sc.ChapterSelection(title="3.4 动量守恒")],
        )] if files else [],
    )
    return sc.LessonBrief(
        topic=topic,
        goals=["会判断系统动量是否守恒"],
        source_selection=selection,
        source_policy=sc.SourcePolicy.strict_textbook if files
        else sc.SourcePolicy.web_topic,
        research=sc.ResearchBrief(enabled=False),
        duration_minutes=15,
        page_plan="auto",
        language=sc.LessonLanguage.zh,
        grade="本科",
        pedagogy_id="concept_deep@1",
        theme_id="academic_clear@1",
        image_density=sc.ImageDensity.balanced,
        checkpoint_density=sc.CheckpointDensity.standard,
        voice_preferences=sc.VoicePreferences(),
        custom_requirements="",
    )


def make_revision(n: int = 1, *, slides: list[sc.SlideSpec] | None = None,
                  brief: sc.LessonBrief | None = None,
                  sources: list[sc.SourceRecord] | None = None) -> sc.LessonRevision:
    slides = slides or [make_slide(1)]
    brief = brief or make_brief()
    sources = sources or [make_file_source(1)]
    return sc.LessonRevision(
        revision=n,
        schema_version=1,
        brief=brief,
        source_snapshot=sources,
        slides=slides,
        checkpoint_templates=[],
        objectives=[sc.Objective(objective_id="objective_1",
                                 text="会判断系统动量是否守恒",
                                 evidence_status=sc.ObjectiveEvidenceStatus.supported)],
        glossary=[sc.GlossaryEntry(term="系统", definition="被研究对象的整体")],
        assets=[],
        renderer_version="1.0.0",
        prompt_versions={"classroom_slide": "1.0.0"},
        review_report=sc.ReviewReport(issues=[], summary="ok"),
        content_hash="c" * 64,
        created_at=utcnow(),
    )
