"""课堂问答上下文（plan.md §12.4，阶段 H01）。

职责：
- ``resolve_classroom_turn``：验证 classroom_ref（run 归属/固定 revision/
  页段存在于 spec），幂等创建/恢复 run 绑定的答疑 session，读取当前页
  公开讲稿与冻结来源摘录，构造 ``ClassroomTurnContext``。不读未揭晓的
  checkpoint 答案（§12.4.3）。
- ``classroom_material_block``：所有执行路径（v2 supervisor / legacy /
  fallback）共用的边界材料区格式化 helper；客户端不传 slide JSON 或
  系统提示词，用户消息原文保持原样。
- ``authorized_lesson_file_ids``：仍授权的教材 file_id 集合，供
  agent_tools 的可信检索范围 override（§12.4 检索 overlay 限于当前仍
  授权的 lesson 来源；被撤销来源只经冻结摘录解释，不重新取原文）。

qa_session 创建的崩溃安全顺序（§12.4.2「在 run 锁内预留 session ID，
并用可恢复操作记录完成绑定」）：先在 run 文件锁内写入预分配的
session_id（不推 state_revision），再落 session 文件；两步之间崩溃时，
下一次调用用同一预留 ID 重建 session，不产生第二份会话。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core import classroom_store as store
from ..core.session import TutorSession, load_session, new_session_id, \
    save_session
from ..schemas import classroom as sc
from ..schemas.chat import ClassroomRef
from .errors import ClassroomError

# 材料区预算（§15.4 问答上下文：当前页完整、前后页短摘要）
_MAX_EXCERPT_CHARS = 1200
_MAX_TOTAL_EXCERPT_CHARS = 8000
_MAX_TAUGHT_SUMMARY_CHARS = 1600


@dataclass(frozen=True)
class ClassroomTurnContext:
    run_id: str
    lesson_id: str
    workspace_id: str
    lesson_revision: int
    lesson_title: str
    language: str
    slide_id: str
    slide_order: int
    slide_title: str
    segment_id: str
    material_block: str
    qa_session: TutorSession
    file_ids: frozenset[str]


def _spec_of(student_id: str, run: sc.ClassroomRun) -> sc.LessonRevision:
    lesson = store.load_lesson(student_id, run.workspace_id, run.lesson_id)
    if lesson is None or lesson.lifecycle == sc.LessonLifecycle.archived:
        raise ClassroomError("source_not_found", "课程已归档或不存在")
    spec = store.load_revision(student_id, run.workspace_id, run.lesson_id,
                               run.lesson_revision)
    if spec is None:
        raise ClassroomError("source_not_found", "课程固定版本不存在")
    return spec


def ensure_qa_session(student_id: str, run: sc.ClassroomRun,
                      lesson_title: str, grade: str,
                      language: str) -> TutorSession:
    """幂等创建/恢复 run 绑定的答疑 session（§12.4.1/§12.4.2）。

    首条课堂提问才创建普通本人 chat session，标题「课堂答疑 · <课程名>」，
    绑定 run 的 workspace；grade/output_language 从冻结 brief 继承。
    """
    def _load_or_create(session_id: str) -> TutorSession:
        existing = load_session(session_id)
        if existing is not None:
            if existing.student_id and existing.student_id != student_id:
                raise ClassroomError("source_not_found", "会话不存在")
            existing.student_id = student_id
            return existing
        session = TutorSession(
            session_id=session_id, grade=grade,
            output_language="zh" if language.startswith("zh") else "en",
            title=f"课堂答疑 · {lesson_title[:60]}",
            student_id=student_id)
        session.workspace_id = run.workspace_id
        save_session(session)
        from ..core.workspace import add_session_to_workspace
        add_session_to_workspace(run.workspace_id, session.session_id)
        return session

    current = store.load_run(student_id, run.workspace_id, run.lesson_id,
                             run.run_id) or run
    if current.qa_session_id:
        return _load_or_create(current.qa_session_id)

    reserved = new_session_id(f"课堂答疑 · {lesson_title[:20]}")
    # run 锁内预留 ID（记账写入不推 state_revision，避免播放端 CAS 409）
    store.update_run(student_id, run.workspace_id, run.lesson_id, run.run_id,
                     lambda r: setattr(r, "qa_session_id", reserved),
                     bump_revision=False)
    run.qa_session_id = reserved
    return _load_or_create(reserved)


def resolve_classroom_turn(student_id: str, ref: ClassroomRef,
                           ) -> ClassroomTurnContext:
    """验证 classroom_ref 并构造课堂答疑上下文（§12.4.3 前置校验）。

    ref 的 workspace_id/lesson_id 只是定位提示：run 始终在调用者自己的
    owner 根下加载（路径本身编码归属），外来 ID 得到的只有 404 语义。
    """
    run = _locate_run(student_id, ref)
    if run is None:
        raise ClassroomError("source_not_found", "课堂 run 不存在")
    if ref.workspace_id and run.workspace_id != ref.workspace_id:
        raise ClassroomError("source_not_found", "课堂 run 不存在")
    if ref.lesson_id and run.lesson_id != ref.lesson_id:
        raise ClassroomError("source_not_found", "课堂 run 不存在")
    if run.lesson_revision != ref.lesson_revision:
        raise ClassroomError("revision_conflict",
                             "课程版本与课堂记录不一致")
    spec = _spec_of(student_id, run)

    slide = None
    for s in spec.slides:
        if s.slide_id == ref.slide_id:
            slide = s
            break
    if slide is None:
        raise ClassroomError("content_invalid", "页面不存在于固定版本")
    segment_id = ref.segment_id or run.cursor.segment_id
    if not any(seg.segment_id == segment_id for seg in slide.segments):
        raise ClassroomError("content_invalid", "段不存在于该页")

    lesson = store.load_lesson(student_id, run.workspace_id, run.lesson_id)
    lesson_title = lesson.title if lesson else spec.brief.topic[:60]
    qa = ensure_qa_session(student_id, run, lesson_title,
                           spec.brief.grade, spec.brief.language)
    block = classroom_material_block(spec, slide, segment_id)
    _preserve_resume_anchor(student_id, run)
    return ClassroomTurnContext(
        run_id=run.run_id, lesson_id=run.lesson_id,
        workspace_id=run.workspace_id, lesson_revision=run.lesson_revision,
        lesson_title=lesson_title, language=spec.brief.language,
        slide_id=slide.slide_id, slide_order=slide.order,
        slide_title=slide.title, segment_id=segment_id,
        material_block=block, qa_session=qa,
        file_ids=authorized_lesson_file_ids(spec))


def _preserve_resume_anchor(student_id: str, run: sc.ClassroomRun) -> None:
    """首个插问保存原课堂 anchor（§12.5）：只有最初一次被保留。

    后续追问不覆盖（anchor 指向学生最初被打断的段）；写入是记账性
    操作，不推 state_revision。anchor 恢复时从该段开头重讲，最多重复
    约 30 秒（§12.2）。
    """
    if run.resume_anchor is not None:
        return
    anchor = sc.Cursor(**run.cursor.model_dump())

    def _set(r: sc.ClassroomRun) -> None:
        if r.resume_anchor is None:
            r.resume_anchor = anchor

    try:
        store.update_run(student_id, run.workspace_id, run.lesson_id,
                         run.run_id, _set, bump_revision=False)
        run.resume_anchor = anchor
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "resume anchor update failed", exc_info=True)


def _locate_run(student_id: str,
                ref: ClassroomRef) -> sc.ClassroomRun | None:
    if ref.workspace_id and ref.lesson_id:
        return store.load_run(student_id, ref.workspace_id, ref.lesson_id,
                              ref.run_id)
    if not store.owner_root(student_id).exists():
        return None
    for entry in sorted((store.owner_root(student_id) / "workspaces")
                        .iterdir()):
        if not entry.is_dir():
            continue
        for lesson_id in store.list_lesson_ids(student_id, entry.name):
            found = store.load_run(student_id, entry.name, lesson_id,
                                   ref.run_id)
            if found is not None:
                return found
    return None


def _excerpt_of(record: sc.SourceRecord) -> str:
    text = (record.excerpt or "").strip()
    if len(text) > _MAX_EXCERPT_CHARS:
        text = text[:_MAX_EXCERPT_CHARS] + "…"
    return text


def classroom_material_block(spec: sc.LessonRevision, slide: sc.SlideSpec,
                             segment_id: str) -> str:
    """边界材料区（§6.5 数据边界 + §12.4 同一格式化 helper）。

    只含公开讲稿、可见块文本与冻结来源摘录；不含 checkpoint 答案、
    rubric 或任何未揭晓材料（§10.5）。讲稿事实与学生问题分开，绝不把
    整页讲稿伪装成用户消息。
    """
    lines: list[str] = ["<classroom_context>"]
    lines.append(f"课程：{spec.brief.topic}（版本 {spec.revision}）")
    lines.append(f"当前页：第 {slide.order} 页 · {slide.title}")

    current = None
    taught: list[str] = []
    ordered = sorted(spec.slides, key=lambda s: s.order)
    for s in ordered:
        for seg in s.segments:
            if s.slide_id == slide.slide_id and seg.segment_id == segment_id:
                current = seg
                break
            if s.order < slide.order or (
                    s.slide_id == slide.slide_id and current is None):
                taught.append(seg.display_text.strip())
    if current is not None:
        lines.append(f"当前讲稿段：{current.display_text.strip()}")
    summary = " ".join(taught)[:_MAX_TAUGHT_SUMMARY_CHARS]
    if summary:
        lines.append("已讲内容摘要（截至当前段）：")
        lines.append(summary)

    visible: list[str] = []
    for block in slide.blocks:
        if block.id not in (current.show_block_ids if current else []):
            continue
        text = _block_plain_text(block)
        if text:
            visible.append(text)
    if visible:
        lines.append("本页当前可见要点：")
        lines.extend(f"- {t}" for t in visible[:8])

    prev_slide = next((s for s in ordered if s.order == slide.order - 1),
                      None)
    next_slide = next((s for s in ordered if s.order == slide.order + 1),
                      None)
    neighbors = []
    if prev_slide is not None:
        neighbors.append(f"上一页：{prev_slide.title}")
    if next_slide is not None:
        neighbors.append(f"下一页：{next_slide.title}")
    if neighbors:
        lines.append("；".join(neighbors))

    used: set[str] = set(slide.source_ids)
    for seg in slide.segments:
        used.update(seg.source_ids)
    budget = _MAX_TOTAL_EXCERPT_CHARS
    for record in spec.source_snapshot:
        if record.source_id not in used or budget <= 0:
            continue
        excerpt = _excerpt_of(record)
        if not excerpt:
            continue
        lines.append(
            f'<material_excerpt source="{record.source_id}" '
            f'title="{record.title[:80]}">')
        lines.append(excerpt)
        lines.append("</material_excerpt>")
        budget -= len(excerpt)

    lines.append(
        "课堂答疑规则：围绕学生当前页的问题讲解；未揭晓的随堂题答案与"
        "解析不得提前透露；学生点击「没听懂/举个例子」是请求补充讲解，"
        "不代表已掌握该内容。")
    lines.append("</classroom_context>")
    return "\n".join(lines)


def _block_plain_text(block: sc.SlideBlock) -> str:
    def _spans(spans: list[sc.InlineSpan]) -> str:
        parts: list[str] = []
        for span in spans:
            if span.kind == "math":
                parts.append(f"${span.latex}$")
            else:
                parts.append(span.text)
        return "".join(parts).strip()

    kind = block.kind
    if kind == "paragraph" or kind == "callout":
        return _spans(block.spans)[:200]
    if kind == "bullets":
        return "；".join(_spans(items)[:60] for items in block.items[:5])
    if kind == "formula":
        return f"{block.latex}（{block.spoken}）"
    if kind == "steps":
        return "；".join(
            f"{step.label}:{_spans(step.spans)[:60]}" for step in block.steps)
    if kind == "image":
        return f"[图] {block.caption}"[:120]
    if kind == "table":
        return f"[表] {' / '.join(block.headers)}"
    return ""


def authorized_lesson_file_ids(spec: sc.LessonRevision) -> frozenset[str]:
    """仍以文件形式授权的教材 file_id（web 来源无 file_id，不在此列）。

    撤销来源不返回：其内容只经材料区的冻结摘录解释（§12.4.4）。
    """
    ids: set[str] = set()
    for record in spec.source_snapshot:
        if record.kind == sc.SourceKind.web:
            continue
        locator = record.locator
        if isinstance(locator, sc.FileLocator):
            ids.add(locator.file_id)
    return frozenset(ids)
