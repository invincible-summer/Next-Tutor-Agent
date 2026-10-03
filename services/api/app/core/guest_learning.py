"""Temporary chat and task-only feedback, without learner journal side effects."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from app.agents.student_model.evaluation.schema import CriterionResult

from .execution_policy import ExecutionPolicy, execution_policy
from .paths import runtime_paths
from .guest_runtime import GuestContext, MAX_QUESTIONS, active_request, commit
from .knowledge_store import KnowledgeStore


def public_textbooks() -> list[dict]:
    from .textbook import load_textbooks
    return [{"id": t["id"], "name": t.get("name", ""),
             "grade": t.get("grade", ""), "subject": t.get("subject", "")}
            for t in load_textbooks("public")
            if t.get("status") in {"ready", "graph_failed"}]


def public_knowledge(textbook_ids: list[str]) -> KnowledgeStore:
    """Resolve public IDs on the server; never create or index guest files."""
    from . import library
    from .structured_chunker import chunks_from_meta
    from .textbook import load_textbooks
    books = {t["id"]: t for t in load_textbooks("public")
             if t.get("status") in {"ready", "graph_failed"}}
    if any(tid not in books for tid in textbook_ids):
        raise HTTPException(404, "教材不存在")
    lib = library.load_library("public")
    overlay = KnowledgeStore(memory_only=True)
    seen: set[str] = set()
    for tid in textbook_ids:
        book = books[tid]
        file_ids = book.get("file_ids") or [book.get("file_id")]
        for fid in file_ids:
            if not isinstance(fid, str) or fid in seen:
                continue
            meta = lib.find_file(fid)
            if meta is None:
                raise HTTPException(404, "教材不存在")
            # File metadata is read from the public index, never from the caller.
            path = runtime_paths().library_data / "public" / (library._key(fid) + ".txt")
            try:
                mtime = path.stat().st_mtime
                cached = library._chunk_cache.get(("public", fid))
                if cached is not None and cached[0] == mtime:
                    chunks = cached[1]
                else:
                    text = path.read_text(encoding="utf-8")
                    chunks = chunks_from_meta(text, source=meta.get("filename", ""), file_id=fid, meta=meta)
                    library._chunk_cache[("public", fid)] = (mtime, chunks)
            except OSError:
                raise HTTPException(409, "教材内容暂不可用，请稍后重试。")
            overlay.files.append({**meta, "source_scope": "public_textbook",
                                  "source_visibility": "public", "kind": "textbook"})
            overlay.chunks.extend(chunks)
            seen.add(fid)
    return overlay


def register_quiz(context: GuestContext, session, payload: dict) -> None:
    from app.agents.assessment.manager import task_snapshot_from_quiz_dict, project_quiz_payload
    context.check()
    pending = []
    for q in payload.get("questions") or []:
        q["question_id"] = "gq_" + uuid.uuid4().hex[:24]
        q["question_revision"] = 1
        pending.append(task_snapshot_from_quiz_dict(
            q, source_session_ref=session.session_id))
    def store():
        for task in pending:
            context.questions[task.question_id] = task.model_copy(deep=True)
        while len(context.questions) > MAX_QUESTIONS:
            oldest = next(iter(context.questions))
            context.questions.pop(oldest)
            context.submissions.pop(oldest, None)
            context.question_locks.pop(oldest, None)
    commit(context, store)
    project_quiz_payload(payload)


def tools_for(session):
    from .llm_async import get_llm
    from .quiz_grounding import KnowledgeSearchQuizGroundingProvider
    from .quiz_illustration_policy import IllustrationPolicyProvider
    from app.tools.knowledge_search import KnowledgeSearchTool
    from app.tools.knowledge_read import KnowledgeReadTool
    from app.tools.quiz import GenerateQuizTool
    from app.tools.fit_quiz import FitQuizTool
    llm = get_llm()
    search = KnowledgeSearchTool(session.knowledge)
    ids = {f["id"] for f in session.knowledge.files}
    grounding = KnowledgeSearchQuizGroundingProvider(
        search, required=bool(ids), reason="guest_public_textbook", file_ids=ids)
    illustrations = IllustrationPolicyProvider(session.student_id, "none")
    avoid = [q.get("stem", "")[:40] for entry in session.quiz_history[-3:]
             for q in entry.get("questions", [])]
    return [search, KnowledgeReadTool(session.knowledge),
            GenerateQuizTool(llm, avoid_stems=avoid, grounding_provider=grounding,
                             illustration_policy_provider=illustrations),
            FitQuizTool(llm, grounding_provider=grounding,
                        illustration_policy_provider=illustrations)]


async def chat_events(context: GuestContext, req, *, knowledge: KnowledgeStore | None = None):
    from app.agents.executor import execute
    from app.core.session import TutorSession
    from app.core.llm_async import get_llm
    from app.core.trace import Trace
    from app.prompts.registry import get
    from app.prompts.tutor import grade_preamble
    from app.agents.teaching_engine.stage_profile import normalize_grade
    if req.workspace_id or req.classroom_ref or req.attachments:
        raise HTTPException(401, "游客仅可进行文字聊天和临时练习。")
    if len(req.message.encode("utf-8")) > 32 * 1024:
        raise HTTPException(413, "消息过长")
    if knowledge is None:
        knowledge = await asyncio.to_thread(public_knowledge, req.public_textbook_ids)
    policy = ExecutionPolicy(persistent=False,
                             register_quiz=lambda s, q: register_quiz(context, s, q))
    async with active_request(context), context.chat_lock:
        context.check()
        if req.session_id:
            if context.session is None or context.session.session_id != req.session_id:
                raise HTTPException(404, "会话不存在")
            session = context.session
        else:
            session = TutorSession(session_id="gst_" + uuid.uuid4().hex[:24],
                                   student_id=context.owner_id,
                                   knowledge=KnowledgeStore(memory_only=True))
            commit(context, lambda: setattr(context, "session", session))
        session.grade = normalize_grade(req.grade)
        session.knowledge = knowledge
        session.output_language = req.output_language
        session.messages = session.messages[-12:]
        session.quiz_history = session.quiz_history[-3:]
        session.messages.append({"role": "user", "content": req.message})
        language = req.output_language or req.lang
        commit(context, lambda: setattr(context, "language", "en" if language == "en" else "zh"))
        system = (get("tutor_system").text + "\n" + grade_preamble(session.grade, session.knowledge.has_knowledge())
                  + "\n" + get("guest_learning").text
                  + f"\noutput_language={language}")
        if session.quiz_history:
            system += "\n[当前页面最近的练习]\n" + json.dumps(
                session.quiz_history[-1], ensure_ascii=False)[:12000]
        messages = [{"role": "system", "content": system}, *session.messages]
        with execution_policy(policy):
            try:
                async for event in execute(messages, session, tools_for(session), None,
                                           get_llm(), Trace(persistent=False)):
                    context.check()
                    if event.get("type") == "thinking":
                        continue
                    if event.get("type") == "error":
                        event = {"type": "error", "message": "临时聊天暂不可用，请稍后重试。"}
                    if event.get("type") == "done":
                        session.messages.append({"role": "assistant",
                                                 "content": event.get("answer", "")})
                        event = {k: v for k, v in event.items()
                                 if k not in {"trace_id", "trace_summary", "thinking"}}
                        event.update(session_id=session.session_id, temporary=True)
                    yield event
            except asyncio.CancelledError:
                raise
            except HTTPException:
                raise
            except Exception:
                yield {"type": "error", "message": "临时聊天暂不可用，请稍后重试。"}


class GenerateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    lang: Literal["zh", "en"] = "zh"
    topic: str = Field(min_length=1, max_length=600)
    grade: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    count: int = Field(3, ge=1, le=5)
    q_type: Literal["multiple_choice", "fill_blank", "short_answer"] | None = None
    public_textbook_ids: list[str] = Field(default_factory=list, max_length=8)


async def generate(context: GuestContext, req: GenerateRequest) -> dict:
    from .session import TutorSession
    from app.agents.teaching_engine.stage_profile import normalize_grade
    session = TutorSession(session_id="gp_" + uuid.uuid4().hex[:24],
                           student_id=context.owner_id, grade=normalize_grade(req.grade),
                           knowledge=await asyncio.to_thread(public_knowledge, req.public_textbook_ids))
    async with active_request(context):
        commit(context, lambda: setattr(context, "language", req.lang))
        with execution_policy(ExecutionPolicy(persistent=False)):
            try:
                tool = next(t for t in tools_for(session) if t.name == "generate_quiz")
                args = req.model_dump(exclude={"public_textbook_ids", "lang"}, exclude_none=True)
                args["illustration_request"] = "none"
                result = await tool.run(**args)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "临时出题暂不可用，请稍后重试。")
            context.check()
            if result.is_error:
                raise HTTPException(503, {"error": {"code": result.error_code,
                                    "message": result.text, "retryable": True}})
            register_quiz(context, session, result.data)
            return {**result.data, "session_id": session.session_id, "temporary": True}


def question(context: GuestContext, qid: str, revision: int):
    context.check()
    task = context.questions.get(qid)
    if task is None:
        raise HTTPException(404, "题目不存在")
    if revision != task.question_revision:
        raise HTTPException(409, "题目已更新，请重新出题。")
    return task


class GradingOutput(BaseModel):
    model_config = {"extra": "forbid"}
    criterion_results: list[CriterionResult] = Field(default_factory=list, max_length=12)
    feedback: str = Field(default="", max_length=1200)


async def submit(context: GuestContext, req) -> dict:
    with execution_policy(ExecutionPolicy(persistent=False)):
        return await _submit(context, req)


async def _submit(context: GuestContext, req) -> dict:
    from app.agents.student_model.evaluation import schema as S
    from app.agents.student_model.evaluation.grading import grade_mc_task, compute_task_result
    from app.agents.student_model.evaluation.llm import EvaluationLLMRunner
    from app.prompts.registry import get
    task = question(context, req.question_id, req.question_revision)
    if req.session_id and req.session_id != task.source_session_ref:
        raise HTTPException(404, "会话不存在")
    if len(req.student_answer.encode("utf-8")) > 32 * 1024:
        raise HTTPException(413, "作答超过 32KiB 上限")
    lock = context.question_locks.setdefault(task.question_id, asyncio.Lock())
    async with active_request(context), lock:
        context.check()
        old = context.submissions.get(task.question_id)
        if old is not None:
            if old["student_answer"] != req.student_answer:
                raise HTTPException(409, {"error": {"code": "question_already_answered",
                                    "message": "这道题已提交，请重新出题。"}})
            return {**old, "duplicate": True}
        if task.q_type == S.QuestionType.MULTIPLE_CHOICE:
            result = grade_mc_task(task, req.student_answer)
            feedback = task.explanation
        else:
            # No learner context, journal, scheduler, or learning interpretation.
            system = get("guest_task_grading").text + "\n" + json.dumps(
                GradingOutput.model_json_schema(), ensure_ascii=False)
            system += "\noutput_language=" + context.language
            try:
                out = await EvaluationLLMRunner().run_structured(
                    system=system, user=json.dumps({"task": task.model_dump(mode="json"),
                                                   "student_answer": req.student_answer}, ensure_ascii=False),
                    output_model=GradingOutput, max_output_tokens=3000)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "批改暂不可用，请重试。")
            context.check()
            if out.parsed is None:
                raise HTTPException(503, "批改暂不可用，请重试。")
            try:
                criteria = [S.CriterionResult.model_validate(c) for c in out.parsed.criterion_results]
            except ValueError:
                raise HTTPException(503, "批改暂不可用，请重试。")
            result = compute_task_result(task, criteria, req.student_answer)
            feedback = out.parsed.feedback
        payload = {"status": "ok", "attempt_id": "gatt_" + uuid.uuid4().hex[:24],
                   "source_id": "", "job_id": "", "question_id": task.question_id,
                   "question_revision": task.question_revision, "student_answer": req.student_answer,
                   "task_result": result.model_dump(mode="json"), "verdict": result.verdict,
                   "evaluation": {"status": "unavailable", "interpretation_id": "",
                                  "reason_code": "guest_temporary"},
                   "feedback": feedback, "pending": False, "duplicate": False,
                   "revealed": {"answer": task.answer, "explanation": task.explanation}}
        def store():
            question(context, task.question_id, task.question_revision)
            context.submissions[task.question_id] = payload
            if context.session is not None:
                for entry in context.session.quiz_history:
                    for q in entry.get("questions", []):
                        if q.get("question_id") == task.question_id:
                            q["result"] = payload
        commit(context, store)
        return payload
