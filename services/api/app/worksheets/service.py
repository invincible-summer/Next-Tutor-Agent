"""Worksheet persistence, generation, editing and export."""
from __future__ import annotations

import base64
import binascii
import hashlib
import html
import re
import time
import uuid
from typing import Any

from app.core.atomic import file_lock
from app.core.llm_async import get_llm
from app.agents.assessment.generator import generate_question
from app.agents.assessment.question import QuestionType
from app.agents.assessment.state import AssessmentContext, AssessmentGoal

from . import storage
from app.schemas.worksheet import (
    WorksheetCreateRequest, WorksheetDocument, WorksheetExport,
    WorksheetGenerateRequest, WorksheetGenerateResponse, WorksheetImage,
    WorksheetQuestion, WorksheetQuestionPatchRequest, WorksheetRefineRequest,
    WorksheetPatchRequest,
)


class WorksheetError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def _owned_workspace(owner: str, workspace_id: str):
    """Return the caller's learning area or raise a closed-scope error.

    A worksheet is a scoped authoring artifact.  Keeping the ownership check
    here (rather than trusting the workspace name sent by the browser) means
    every client, including older mobile builds, gets the same boundary.
    """
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise WorksheetError("worksheet_learning_area_required", 422)
    try:
        from app.core.workspace import load_workspace, _owner_of

        workspace = load_workspace(workspace_id)
        if workspace is None or _owner_of(workspace) != owner:
            raise WorksheetError("worksheet_learning_area_invalid", 422)
        return workspace
    except WorksheetError:
        raise
    except Exception as exc:
        raise WorksheetError("worksheet_learning_area_invalid", 422) from exc


def _etag(document: dict) -> str:
    payload = dict(document)
    payload.pop("etag", None)
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:24]


def _strip_background(stem: str) -> str:
    """Remove accidental background labels; guidance never becomes question text."""
    lines = []
    for line in stem.splitlines():
        if re.match(r"^\s*(?:命题背景|题目背景|背景说明|背景|命题指导|生成提示)\s*[:：]", line):
            continue
        if re.match(r"^\s*>\s*(?:命题背景|背景)\s*[:：]", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _clean_stem(stem: str, guidance: str = "") -> str:
    value = _strip_background(stem)
    if guidance.strip():
        value = value.replace(guidance.strip(), "").strip()
    return value


def _recalculate(document: dict) -> dict:
    for index, question in enumerate(document.get("questions", []), 1):
        question["number"] = index
    document["total_score"] = sum(int(q.get("score", 0) or 0) for q in document.get("questions", []))
    document["updated_at"] = time.time()
    document["etag"] = _etag(document)
    return document


def _public(document: dict) -> dict:
    value = WorksheetDocument.model_validate(document).model_dump(mode="json")
    guidance = str(document.get("guidance_prompt") or "")
    for question in value.get("questions", []):
        question["stem"] = _clean_stem(str(question.get("stem") or ""), guidance)
    return value


def create(owner: str, body: WorksheetCreateRequest) -> dict:
    workspace = _owned_workspace(owner, body.workspace_id)
    points = [str(item).strip()[:120] for item in body.knowledge_points if str(item).strip()][:12]
    now = time.time()
    worksheet_id = "ws_" + uuid.uuid4().hex[:24]
    document = {
        "id": worksheet_id, "title": body.title.strip() or "未命名试卷",
        # The ID is the authority; never persist a browser-supplied label that
        # could drift from the selected learning area's canonical name.
        "learning_area": workspace.name,
        "workspace_id": body.workspace_id.strip(),
        "subject": body.subject.strip(), "grade": body.grade.strip(), "unit": body.unit.strip(),
        "duration_minutes": body.duration_minutes, "total_score": 0,
        "instructions": body.instructions.strip(), "guidance_prompt": body.guidance_prompt.strip(),
        "goal": body.goal.strip(),
        "knowledge_points": points,
        "reference_textbook": bool(body.reference_textbook and points),
        "status": "draft", "questions": [], "idempotency": {},
        "created_at": now, "updated_at": now, "etag": "",
    }
    document["etag"] = _etag(document)
    with file_lock(storage.owner_dir(owner)):
        storage.write(owner, worksheet_id, document)
    return _public(document)


def _load(owner: str, worksheet_id: str) -> dict:
    try:
        document = storage.read(owner, worksheet_id)
    except ValueError:
        document = None
    if not document or document.get("deleted"):
        raise WorksheetError("worksheet_not_found", 404)
    return document


def list_documents(owner: str) -> dict:
    rows = storage.list_documents(owner)
    return {"items": [
        {"id": row["id"], "title": row.get("title", ""), "subject": row.get("subject", ""),
         "question_count": len(row.get("questions", [])), "total_score": row.get("total_score", 0),
         "status": row.get("status", "draft"), "updated_at": row.get("updated_at", 0)}
        for row in rows
    ], "total": len(rows)}


def get(owner: str, worksheet_id: str) -> dict:
    with file_lock(storage.owner_dir(owner)):
        return _public(_load(owner, worksheet_id))


def patch(owner: str, worksheet_id: str, body: WorksheetPatchRequest) -> dict:
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        if body.etag != document.get("etag"):
            raise WorksheetError("worksheet_revision_conflict")
        values = body.model_dump(exclude={"etag"})
        next_workspace_id = values.get("workspace_id")
        if next_workspace_id is None:
            next_workspace_id = document.get("workspace_id")
        workspace = _owned_workspace(owner, next_workspace_id)
        if "knowledge_points" in values and values["knowledge_points"] is not None:
            values["knowledge_points"] = [
                str(item).strip()[:120] for item in values["knowledge_points"] if str(item).strip()
            ][:12]
        for key, value in values.items():
            if value is not None:
                document[key] = value.strip() if isinstance(value, str) else value
        document["learning_area"] = workspace.name
        document["knowledge_points"] = [
            str(item).strip()[:120] for item in (document.get("knowledge_points") or []) if str(item).strip()
        ][:12]
        document["reference_textbook"] = bool(
            document.get("reference_textbook") and document.get("knowledge_points"))
        _recalculate(document)
        storage.write(owner, worksheet_id, document)
        return _public(document)


def delete(owner: str, worksheet_id: str) -> dict:
    with file_lock(storage.owner_dir(owner)):
        _load(owner, worksheet_id)
        storage.delete(owner, worksheet_id)
    return {"deleted": True}


def _type_distribution(body: WorksheetGenerateRequest) -> list[str]:
    values: list[str] = []
    for key, count in body.type_distribution.items():
        if key in {QuestionType.MULTIPLE_CHOICE, QuestionType.FILL_BLANK, QuestionType.SHORT_ANSWER}:
            values.extend([key] * max(0, min(50, int(count))))
    if values:
        return (values + [body.question_type] * body.count)[:body.count]
    return [body.question_type] * body.count


def _workspace_textbook_ids(owner: str, workspace_id: str) -> list[str]:
    """Resolve the selected learning area's textbook records.

    Workspaces persist selected textbook *file* ids while the grounding API
    accepts textbook ids (a group may contain several volumes). Keep this
    translation here so worksheet generation never searches outside the
    selected learning area or a public textbook namespace.
    """
    if not workspace_id.strip():
        return []
    try:
        from app.core.workspace import load_workspace, _owner_of
        from app.core.textbook import PUBLIC_STUDENT_ID, textbook_for_file

        workspace = load_workspace(workspace_id.strip())
        if workspace is None:
            return []
        if _owner_of(workspace) != owner:
            return []
        ids: list[str] = []
        for file_id in getattr(workspace, "selected_file_ids", []) or []:
            record = textbook_for_file(owner, str(file_id))
            if record is None:
                record = textbook_for_file(PUBLIC_STUDENT_ID, str(file_id))
            textbook_id = str((record or {}).get("id") or "").strip()
            if textbook_id and textbook_id not in ids:
                ids.append(textbook_id)
        return ids[:8]
    except Exception:
        return []


async def _worksheet_grounding(owner: str, document: dict,
                               topics: list[str]) -> dict[str, Any]:
    """Run one optional, scoped textbook retrieval for a worksheet.

    The UI deliberately disables this path when no knowledge point is chosen.
    ``strict_textbook`` makes an explicit request visible when a selected
    textbook is unavailable instead of silently producing an ungrounded paper.
    """
    if not bool(document.get("reference_textbook")) or not topics:
        return {}
    textbook_ids = _workspace_textbook_ids(owner, str(document.get("workspace_id") or ""))
    if not textbook_ids:
        raise WorksheetError("worksheet_textbook_scope_empty", 422)
    try:
        from app.api.v1.assessment_grounding import (
            build_assessment_grounding, bundle_to_context_fields,
        )

        bundle = await build_assessment_grounding(
            student_id=owner,
            concept="、".join(topics)[:600],
            textbook_ids=textbook_ids,
            strict_textbook=True,
        )
        fields = bundle_to_context_fields(bundle)
        if not fields.get("grounding_sources"):
            raise WorksheetError("worksheet_textbook_no_match", 422)
        return fields
    except WorksheetError:
        raise
    except Exception as exc:
        raise WorksheetError("worksheet_textbook_unavailable", 422) from exc


def _fallback_question(document: dict, q_type: str, score: int, difficulty: int, topic: str, guidance: str) -> dict:
    stem = f"请围绕“{topic or document.get('subject') or document.get('title') or '本单元知识'}”完成一道{q_type}，并写出关键依据。"
    if guidance:
        # Guidance is intentionally not copied into the stem.
        stem += ""
    return {
        "id": "q_" + uuid.uuid4().hex[:24], "number": 0, "type": q_type,
        "stem": stem, "options": ({"A": "待补充", "B": "待补充"} if q_type == "multiple_choice" else {}),
        "answer": "待教师确认", "explanation": "这是生成服务不可用时的可编辑草稿，请补充答案与解析。",
        "score": score, "difficulty": difficulty, "knowledge_points": [topic] if topic else [],
        "image": None, "version": 1, "updated_at": time.time(),
    }


async def generate(owner: str, worksheet_id: str, body: WorksheetGenerateRequest) -> WorksheetGenerateResponse:
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        _owned_workspace(owner, document.get("workspace_id", ""))
        key = body.idempotency_key.strip()
        if key and key in document.get("idempotency", {}):
            return WorksheetGenerateResponse(worksheet=_public(document), generated=0, errors=["idempotent_replay"])
        goal = str(document.get("goal") or "").strip()
        guidance = body.guidance_prompt.strip() or document.get("guidance_prompt", "")
        selected_topics = [str(item).strip()[:120] for item in body.knowledge_points if str(item).strip()]
        if not selected_topics:
            selected_topics = [str(item).strip()[:120] for item in document.get("knowledge_points", []) if str(item).strip()]
        topics = list(selected_topics) or [document.get("unit") or document.get("subject") or document.get("title") or "综合知识"]
        if goal:
            guidance = (f"出卷目标：{goal}\n{guidance}").strip()
        if document.get("learning_area"):
            guidance = (f"学习区：{document['learning_area']}\n{guidance}").strip()
        types = _type_distribution(body)
        snapshot = dict(document)
    grounding_fields = await _worksheet_grounding(owner, snapshot, selected_topics)
    errors: list[str] = []
    generated: list[dict] = []
    try:
        llm = get_llm("quiz")
    except Exception:
        llm = None
    if grounding_fields.get("grounding_required") and llm is None:
        raise WorksheetError("worksheet_generation_unavailable", 503)
    for index, q_type in enumerate(types):
        topic = topics[index % len(topics)]
        value = None
        if llm is not None:
            try:
                value = await generate_question(
                    AssessmentGoal(
                        concept=topic, purpose="practice", difficulty=body.difficulty,
                        count=1, q_type=q_type, generation_hint=guidance,
                    ),
                    AssessmentContext(
                        concept=topic, subject=document.get("subject", ""),
                        grade=document.get("grade", "本科") or "本科",
                        base_difficulty=body.difficulty,
                        grounding_required=bool(grounding_fields.get("grounding_required", False)),
                        grounding_mode=str(grounding_fields.get("grounding_mode") or "generic"),
                        grounding_tier=str(grounding_fields.get("grounding_tier") or "not_found"),
                        grounding_query=str(grounding_fields.get("grounding_query") or ""),
                        grounding_sources=[dict(source) for source in grounding_fields.get("grounding_sources", []) if isinstance(source, dict)],
                    ),
                    llm=llm, student_id=owner,
                )
            except Exception as exc:
                errors.append(f"{index + 1}:{type(exc).__name__}")
        if value is None:
            if grounding_fields.get("grounding_required"):
                raise WorksheetError("worksheet_generation_failed", 502)
            errors.append(f"{index + 1}:draft_fallback")
            generated.append(_fallback_question(document, q_type, body.score, body.difficulty, topic, guidance))
            continue
        generated.append({
            "id": value.id or "q_" + uuid.uuid4().hex[:24], "number": 0,
            "type": value.q_type if value.q_type in {"multiple_choice", "fill_blank", "short_answer"} else q_type,
            "stem": _clean_stem(value.stem, guidance), "options": dict(value.options),
            "answer": value.answer, "explanation": value.explanation,
            "score": body.score, "difficulty": body.difficulty,
            "knowledge_points": list(value.knowledge_points or [topic])[:8],
            "image": None, "version": 1, "updated_at": time.time(),
        })
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        document.setdefault("questions", []).extend(generated)
        document["status"] = "ready" if document["questions"] else "draft"
        if key:
            document.setdefault("idempotency", {})[key] = True
        _recalculate(document)
        storage.write(owner, worksheet_id, document)
        return WorksheetGenerateResponse(worksheet=_public(document), generated=len(generated), errors=errors)


def patch_question(owner: str, worksheet_id: str, question_id: str, body: WorksheetQuestionPatchRequest) -> dict:
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        if body.etag != document.get("etag"):
            raise WorksheetError("worksheet_revision_conflict")
        question = next((item for item in document.get("questions", []) if item.get("id") == question_id), None)
        if question is None:
            raise WorksheetError("worksheet_question_not_found", 404)
        for key, value in body.model_dump(exclude={"etag"}).items():
            if value is not None:
                question[key] = _clean_stem(value, document.get("guidance_prompt", "")) if key == "stem" else value
        question["version"] = int(question.get("version", 1)) + 1
        question["updated_at"] = time.time()
        _recalculate(document)
        storage.write(owner, worksheet_id, document)
        return _public(document)


def delete_question(owner: str, worksheet_id: str, question_id: str, etag: str) -> dict:
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        if etag != document.get("etag"):
            raise WorksheetError("worksheet_revision_conflict")
        before = len(document.get("questions", []))
        document["questions"] = [item for item in document.get("questions", []) if item.get("id") != question_id]
        if len(document["questions"]) == before:
            raise WorksheetError("worksheet_question_not_found", 404)
        document["status"] = "ready" if document["questions"] else "draft"
        _recalculate(document)
        storage.write(owner, worksheet_id, document)
        return _public(document)


async def refine_question(owner: str, worksheet_id: str, question_id: str, body: WorksheetRefineRequest) -> dict:
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        _owned_workspace(owner, document.get("workspace_id", ""))
        if body.etag != document.get("etag"):
            raise WorksheetError("worksheet_revision_conflict")
        question = next((item for item in document.get("questions", []) if item.get("id") == question_id), None)
        if question is None:
            raise WorksheetError("worksheet_question_not_found", 404)
        snapshot = dict(question)
    selected_topics = [str(item).strip() for item in snapshot.get("knowledge_points", []) if str(item).strip()]
    grounding_fields = await _worksheet_grounding(owner, document, selected_topics)
    goal = str(document.get("goal") or "").strip()
    grounding_hint = ""
    if grounding_fields.get("grounding_sources"):
        grounding_hint = "\n本题必须基于已检索教材证据修改，不得引入证据外事实。"
    try:
        value = await generate_question(
            AssessmentGoal(
                concept=(snapshot.get("knowledge_points") or [document.get("subject") or "综合知识"])[0],
                q_type=snapshot.get("type", "multiple_choice"), difficulty=int(snapshot.get("difficulty", 3)),
                generation_hint=(
                    f"出卷目标：{goal}\n当前题目：{snapshot.get('stem', '')}\n"
                    f"修改要求：{body.instruction}{grounding_hint}"
                ).strip(),
            ),
            AssessmentContext(
                concept=(snapshot.get("knowledge_points") or ["综合知识"])[0],
                subject=document.get("subject", ""), grade=document.get("grade", "本科") or "本科",
                grounding_required=bool(grounding_fields.get("grounding_required", False)),
                grounding_mode=str(grounding_fields.get("grounding_mode") or "generic"),
                grounding_tier=str(grounding_fields.get("grounding_tier") or "not_found"),
                grounding_query=str(grounding_fields.get("grounding_query") or ""),
                grounding_sources=[dict(source) for source in grounding_fields.get("grounding_sources", []) if isinstance(source, dict)],
            ),
            llm=get_llm("quiz"), student_id=owner,
        )
    except Exception:
        value = None
    if value is not None:
        return patch_question(owner, worksheet_id, question_id, WorksheetQuestionPatchRequest(
            stem=_clean_stem(value.stem, body.instruction), options=value.options, answer=value.answer,
            explanation=value.explanation, score=snapshot.get("score", 5), difficulty=value.difficulty,
            knowledge_points=value.knowledge_points, etag=body.etag,
        ))
    raise WorksheetError("worksheet_refine_failed", 502)


def attach_image(owner: str, worksheet_id: str, question_id: str, *, data_url: str, alt: str = "", etag: str | None = None) -> dict:
    if not data_url.startswith("data:image/"):
        raise WorksheetError("worksheet_image_invalid", 422)
    try:
        header, encoded = data_url.split(",", 1)
        mime = header.split(";", 1)[0].removeprefix("data:image/")
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WorksheetError("worksheet_image_invalid", 422) from exc
    if mime not in {"png", "jpeg", "jpg", "webp"} or len(raw) > 12 * 1024 * 1024:
        raise WorksheetError("worksheet_image_invalid", 422)
    with file_lock(storage.owner_dir(owner)):
        document = _load(owner, worksheet_id)
        if etag and etag != document.get("etag"):
            raise WorksheetError("worksheet_revision_conflict")
        question = next((item for item in document.get("questions", []) if item.get("id") == question_id), None)
        if question is None:
            raise WorksheetError("worksheet_question_not_found", 404)
        previous_asset = (question.get("image") or {}).get("asset_id")
        asset_id = "wsa_" + uuid.uuid4().hex[:24]
        suffix = "jpg" if mime in {"jpeg", "jpg"} else mime
        storage.save_asset(owner, worksheet_id, asset_id, raw, suffix)
        normalized_mime = "image/jpeg" if mime in {"jpeg", "jpg"} else f"image/{mime}"
        question["image"] = {"asset_id": asset_id, "url": f"/tools/worksheets/{worksheet_id}/assets/{asset_id}", "data_url": data_url, "alt": alt[:160], "mime_type": normalized_mime}
        if previous_asset:
            storage.remove_asset(owner, worksheet_id, str(previous_asset))
        question["version"] = int(question.get("version", 1)) + 1
        _recalculate(document)
        storage.write(owner, worksheet_id, document)
        return _public(document)


def _markdown(document: dict, variant: str) -> str:
    title = document.get("title") or "未命名试卷"
    header = [f"# {title}", "", f"学科：{document.get('subject') or '____'}　年级：{document.get('grade') or '____'}　单元：{document.get('unit') or '____'}", f"考试时间：{document.get('duration_minutes', 45)} 分钟　满分：{document.get('total_score', 0)} 分", "姓名：____________　班级：____________　日期：____________", "", document.get("instructions") or "请认真审题，按题目要求作答。", ""]
    blocks = []
    for question in document.get("questions", []):
        stem = _clean_stem(str(question.get("stem") or ""), str(document.get("guidance_prompt") or ""))
        block = [f"### {question.get('number', 0)}．（{question.get('score', 0)} 分）", "", stem]
        for key, value in (question.get("options") or {}).items():
            block.append(f"{key}. {value}")
        image = question.get("image")
        if image:
            block.extend(["", f"![{image.get('alt', '题目配图')}]({image.get('data_url') or image.get('url', '')})"])
        if variant == "teacher":
            block.extend(["", f"**答案：** {question.get('answer', '')}", "", f"**解析：** {question.get('explanation', '')}"])
        blocks.append("\n".join(block))
    return "\n".join(header + ["\n---\n".join(blocks)])


_MATH_TOKEN_RE = re.compile(
    r"\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|"
    r"(?<!\\)\$([^$\n]+?)\$|\\\(([\s\S]*?)\\\)"
)


def _math_markup(value: str) -> str:
    """Escape worksheet text while preserving supported TeX delimiters.

    The exported HTML embeds KaTeX locally, so printing never depends on a
    network request. Plain Markdown emphasis is intentionally kept literal in
    this small export path; formulas are the critical content that must remain
    executable and readable on paper.
    """
    source = str(value or "")
    chunks: list[str] = []
    cursor = 0
    for match in _MATH_TOKEN_RE.finditer(source):
        chunks.append(html.escape(source[cursor:match.start()]).replace("\n", "<br />"))
        body = next((group for group in match.groups() if group is not None), "")
        display = match.group(1) is not None or match.group(2) is not None
        tex = html.escape(body.strip(), quote=True)
        if display:
            chunks.append(f'<div class="math-display" data-katex="{tex}">{tex}</div>')
        else:
            chunks.append(f'<span class="math-inline" data-katex="{tex}">{tex}</span>')
        cursor = match.end()
    chunks.append(html.escape(source[cursor:]).replace("\n", "<br />"))
    return "".join(chunks)


def _katex_assets() -> tuple[str, str]:
    try:
        from app.classroom.render.assets import load_asset_pack

        pack = load_asset_pack()
        if pack is not None:
            return pack.katex_css, pack.katex_js
    except Exception:
        pass
    return "", ""


def _html(document: dict, variant: str) -> str:
    """Build a print-ready exam sheet instead of escaping Markdown syntax."""
    esc = html.escape
    title = esc(str(document.get("title") or "未命名试卷"))
    subject = esc(str(document.get("subject") or "____"))
    grade = esc(str(document.get("grade") or "____"))
    unit = esc(str(document.get("unit") or "____"))
    instructions = _math_markup(str(document.get("instructions") or "请认真审题，按题目要求作答。"))
    questions: list[str] = []
    for question in document.get("questions", []):
        options = "".join(
            f"<li><strong>{esc(str(key))}.</strong> {_math_markup(str(value))}</li>"
            for key, value in (question.get("options") or {}).items()
        )
        image = question.get("image") or {}
        image_source = image.get("data_url") or image.get("url")
        image_html = (
            f"<img class=\"question-image\" src=\"{esc(str(image_source))}\" "
            f"alt=\"{esc(str(image.get('alt') or '题目配图'))}\" />"
            if image_source else ""
        )
        answers = ""
        if variant == "teacher":
            answers = (
                f"<aside class=\"answer\"><b>答案：</b>{_math_markup(str(question.get('answer', '')))}"
                f"<br /><b>解析：</b>{_math_markup(str(question.get('explanation', '')))}</aside>"
            )
        questions.append(
            f"<section class=\"question\"><h2>{int(question.get('number', 0))}．"
            f"<span>（{int(question.get('score', 0))} 分）</span></h2>"
            f"<div class=\"stem\">{_math_markup(_clean_stem(str(question.get('stem') or ''), str(document.get('guidance_prompt') or '')))}</div>"
            f"{f'<ol class=\"options\">{options}</ol>' if options else ''}{image_html}{answers}</section>"
        )
    body = "".join(questions)
    katex_css, katex_js = _katex_assets()
    if not katex_css or not katex_js:
        raise WorksheetError("worksheet_math_renderer_unavailable", 503)
    math_script = """
<script>
(function () {
  function renderMath() {
    if (!window.katex) return;
    document.querySelectorAll('[data-katex]').forEach(function (node) {
      try {
        window.katex.render(node.getAttribute('data-katex') || '', node, {
          displayMode: node.classList.contains('math-display'),
          throwOnError: false, strict: false, trust: false
        });
      } catch (error) {
        node.textContent = node.getAttribute('data-katex') || '';
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderMath);
  else renderMath();
}());
</script>
"""
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>" + title + "</title><style>" + katex_css + "</style><style>"
        "@page{size:A4;margin:14mm 15mm}*{box-sizing:border-box}body{font-family:Arial,'Noto Serif SC',serif;"
        "color:#1e1e24;background:#f6f5f1;margin:0;line-height:1.8;font-size:14px}"
        ".paper-sheet{max-width:800px;margin:0 auto;background:#fffefa;padding:28px 34px;box-shadow:0 1px 0 rgba(30,30,36,.08)}"
        "h1{text-align:center;font-size:25px;line-height:1.35;margin:0 0 12px;letter-spacing:.02em}"
        ".subject{text-align:center;color:#62636a;margin:0 0 16px;font-size:12px}"
        ".meta{display:grid;grid-template-columns:1fr 1fr;border-top:1.5px solid #1e1e24;"
        "border-bottom:1px solid #9a9aa0;padding:9px 0;margin-bottom:18px;font-size:12px}"
        ".meta span:nth-child(even){text-align:right}.instructions{margin:0 0 26px;padding:10px 12px;background:#f0f5f3;border-left:3px solid #256d66}"
        ".question{break-inside:avoid;border-bottom:1px solid #d9d8d3;padding:0 0 18px;margin:0 0 18px}"
        ".question h2{font-size:15px;line-height:1.5;margin:0 0 7px}.question h2 span{font-weight:400;color:#62636a}"
        ".question .stem{white-space:normal;margin:0 0 7px}.options{margin:5px 0 8px;padding-left:28px}"
        ".math-display{margin:.7em 0;overflow-x:auto;text-align:center}.math-inline{white-space:nowrap}"
        ".question-image{display:block;max-width:100%;max-height:280px;object-fit:contain;margin:10px auto}"
        ".answer{background:#eef7f3;border:1px solid #bcd8cf;border-left:3px solid #2f8f72;border-radius:6px;padding:8px 10px;font-size:13px}"
        "@media print{body{background:#fff}.paper-sheet{max-width:none;padding:0;box-shadow:none}.question{border-color:#c8c8c3}}"
        "</style></head><body><main class=\"paper-sheet\"><h1>" + title + "</h1><p class=\"subject\">"
        + f"学科：{subject}　年级：{grade}　单元：{unit}</p><div class=\"meta\"><span>考试时间：{int(document.get('duration_minutes', 45))} 分钟　满分：{int(document.get('total_score', 0))} 分</span>"
        + "<span>姓名：____________　班级：____________　日期：____________</span></div>"
        + f"<p class=\"instructions\">{instructions}</p>{body}</main></body>"
        + (f"<script>{katex_js}</script>" if katex_js else "")
        + math_script + "</html>"
    )


def export(owner: str, worksheet_id: str, variant: str, fmt: str) -> WorksheetExport:
    document = _load(owner, worksheet_id)
    content = _markdown(document, variant)
    if fmt == "html":
        content = _html(document, variant)
    return WorksheetExport(variant=variant, format=fmt, content=content)
