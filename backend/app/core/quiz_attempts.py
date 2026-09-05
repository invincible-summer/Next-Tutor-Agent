"""Quiz attempt records: make every graded answer retrievable.

Quiz grading happens on the question cards, outside the chat stream, so the
agent historically had no durable, searchable trail of WHAT the student
answered.  This module writes two (both fail-open, never raise):

  - transcript: append-only 【出题记录】/【作答记录】 entries, so the
    recall_history JIT tool can actually find the questions and the student's
    answers, and LLM compaction has real material for its 练习/错题 field.
  - M6 episodic memory: a ``quiz_graded`` LearningEvent so cross-session
    "过往经验" reflects real attempts.  The classifier already supported this
    event type; the production path simply never emitted it (quiz endpoints
    only talked to M2/M4).
"""
from __future__ import annotations

import time
from typing import Any

from .context import append_transcript

_VERDICT_ZH = {"correct": "对", "partial": "部分对", "wrong": "错"}


def record_generated_quiz(session_id: str, quiz: dict[str, Any]) -> None:
    """Append a compact 【出题记录】 for a generated quiz set.

    The model never sees its own questions afterwards (tool results are
    projected to a boundary summary and history strips tool payloads), so
    without this record "解释一下上面那道题" is unanswerable after the fact.
    """
    try:
        if not session_id or not isinstance(quiz, dict):
            return
        questions = [q for q in (quiz.get("questions") or []) if isinstance(q, dict)]
        if not questions:
            return
        topic = str(quiz.get("topic") or quiz.get("reference") or "")[:40]
        parts = []
        for i, q in enumerate(questions, 1):
            stem = str(q.get("stem") or "").strip()[:300]
            answer = str(q.get("answer") or "").strip()[:60]
            kp = str(q.get("knowledge_point") or "").strip()[:30]
            seg = f"{i}. {stem}｜答案:{answer}"
            if kp:
                seg += f"｜考点:{kp}"
            parts.append(seg)
        content = (f"【出题记录】围绕「{topic}」出了 {len(parts)} 题：\n"
                   + "\n".join(parts))
        append_transcript(session_id, 0, [{"role": "system", "content": content}])
    except Exception:
        pass


def merge_quiz_results_from_disk(session: Any) -> bool:
    """Merge quiz answer results from the on-disk session into the in-memory
    one before saving.

    Card answers are recorded via /quiz/* endpoints (load-modify-save) while a
    chat turn may still be streaming; the turn's own save would otherwise
    overwrite those results with its stale in-memory quiz_history.  Matching
    uses the same stem-prefix key as _write_back_answer.  Never raises.
    """
    try:
        sid = getattr(session, "session_id", "")
        if not sid:
            return False
        from .session import load_session
        disk = load_session(sid)
        if disk is None:
            return False

        def _key(q: dict) -> str:
            return str(q.get("stem", "")).strip()[:60]

        changed = False
        for dqh in (getattr(disk, "quiz_history", None) or []):
            if not isinstance(dqh, dict):
                continue
            for dq in (dqh.get("questions") or []):
                if not isinstance(dq, dict) or not isinstance(dq.get("result"), dict):
                    continue
                dk = _key(dq)
                if not dk:
                    continue
                for mqh in (getattr(session, "quiz_history", None) or []):
                    if not isinstance(mqh, dict):
                        continue
                    for mq in (mqh.get("questions") or []):
                        if (isinstance(mq, dict) and _key(mq) == dk
                                and not isinstance(mq.get("result"), dict)):
                            mq["result"] = dq["result"]
                            changed = True
        if changed:
            from .session_learning_card import (SessionLearningCard,
                                                reconcile_quiz_history)
            card = SessionLearningCard.from_dict(session.context_card)
            reconcile_quiz_history(card, session.quiz_history or [])
            session.context_card = card.to_dict()
        return changed
    except Exception:
        return False


def latest_quiz_digest(session: Any, *, max_questions: int = 3) -> str:
    """Bounded digest of the NEWEST quiz set for the per-turn status recap.

    The quiz digest projected into the model context only lives in the turn
    that generated it; history messages strip tool payloads, so the next turn
    otherwise cannot see the questions at all ("仔细讲解一下上一题" fails).
    This renders stems + answers + verdicts + student answers deterministically,
    ~120 chars/question.  "" when no quiz exists yet.
    """
    try:
        sets = getattr(session, "quiz_history", None) or []
        for qh in reversed(sets):
            if not isinstance(qh, dict):
                continue
            questions = [q for q in (qh.get("questions") or [])
                         if isinstance(q, dict)]
            if not questions:
                continue
            topic = str(qh.get("topic") or qh.get("reference") or "")[:30]
            segs: list[str] = []
            for i, q in enumerate(questions[:max_questions], 1):
                # 题干/解析给足全文量级：Agent 要据此逐题讲解点评（此前
                # 80 字截断导致「题目被截断读不到解析」）。
                stem = str(q.get("stem") or "").strip()[:300]
                answer = str(q.get("answer") or "").strip()[:60]
                seg = f"第{i}题[{q.get('type', 'multiple_choice')}] {stem}｜答案:{answer}"
                explanation = str(q.get("explanation") or "").strip()[:260]
                if explanation:
                    seg += f"｜解析:{explanation}"
                res = q.get("result")
                if isinstance(res, dict) and res.get("verdict"):
                    zh = _VERDICT_ZH.get(str(res.get("verdict")),
                                         str(res.get("verdict")))
                    ans = str(res.get("student_answer") or "").strip()[:60]
                    seg += f"｜学生答「{ans}」判{zh}"
                else:
                    seg += "｜未作答"
                segs.append(seg)
            head = f"最近答题卡（{topic}，可逐题讲解/点评）：" if topic else "最近答题卡（可逐题讲解/点评）："
            return head + "；".join(segs)
    except Exception:
        pass
    return ""


def quiz_digest_for_session(session: Any) -> str:
    """Deterministic digest of this session's generated quizzes + graded
    answers, for the compaction summarizer.

    Its「练习与错题」field otherwise has no source material: quiz payloads
    live in quiz_history / tool call data, never in the message text that
    _serialize_for_compact renders.  Bounded (last 5 sets x 5 questions).
    """
    try:
        sets = getattr(session, "quiz_history", None) or []
        lines: list[str] = []
        for qh in sets[-5:]:
            if not isinstance(qh, dict):
                continue
            topic = str(qh.get("topic") or qh.get("reference") or "")[:40]
            lines.append(f"套题（{topic}）：")
            for i, q in enumerate((qh.get("questions") or [])[:5], 1):
                if not isinstance(q, dict):
                    continue
                stem = str(q.get("stem") or "").strip()[:80]
                seg = f"  {i}. {stem}"
                res = q.get("result")
                if isinstance(res, dict) and res.get("verdict"):
                    zh = _VERDICT_ZH.get(str(res.get("verdict")),
                                         str(res.get("verdict")))
                    ans = str(res.get("student_answer") or "").strip()[:40]
                    seg += f"｜学生答「{ans}」判{zh}"
                else:
                    seg += "｜未作答"
                lines.append(seg)
        return "\n".join(lines)[:2000]
    except Exception:
        return ""


def record_quiz_attempt(session_id: str, *, stem: str, verdict: str,
                        student_answer: str, concept: str = "",
                        subject: str = "", student_id: str = "",
                        correct: bool | None = None, note: str = "",
                        attempt_id: str = "") -> None:
    """Persist one graded answer to transcript + M6 episodic + M3 teaching_log.

    ``unknown`` verdicts (grading could not run, e.g. malformed request) are
    skipped entirely — they carry no signal and must not pollute the
    transcript or long-term memory. ``attempt_id`` (W2/A14) keys the ledger
    attempt; the write-back path passes the same id so the two writes fold
    into one append-only attempt record."""
    if verdict == "unknown":
        return
    try:
        from .learning_records import record_verdict
        record_verdict(student_id, session_id, stem=stem, verdict=verdict,
                       student_answer=student_answer, concept=concept,
                       subject=subject, attempt_id=attempt_id,
                       score={"correct": 1.0, "partial": 0.5, "wrong": 0.0}.get(verdict))
    except Exception:
        pass
    # 1. transcript record (recall_history JIT retrieval + compaction material)
    try:
        if session_id and verdict:
            zh = _VERDICT_ZH.get(verdict, verdict)
            content = (f"【作答记录】题目：{str(stem)[:120]}\n"
                       f"学生作答：{str(student_answer)[:200]}\n"
                       f"判定：{zh}" + (f"｜考点：{concept}" if concept else ""))
            append_transcript(session_id, 0, [{"role": "system", "content": content}])
    except Exception:
        pass
    # 2. M6 episodic (quiz_graded event)
    try:
        if student_id and verdict:
            from ..agents.memory import get_memory_service, is_enabled as mem_enabled
            if mem_enabled():
                zh = _VERDICT_ZH.get(verdict, verdict)
                ans = str(student_answer).strip()[:40]
                event_note = note or (f"学生答「{ans}」判{zh}" if ans else f"判{zh}")
                get_memory_service().consume_turn(
                    student_id=student_id,
                    session_id=session_id,
                    events=[{"type": "quiz_graded", "ts": time.time(),
                             "payload": {"concept": concept, "subject": subject,
                                         "correct": correct,
                                         "note": event_note[:80]}}],
                    subject=subject)
    except Exception:
        pass
    # 3. M3 teaching_log: the difficulty dial's only assessed-outcome source.
    # Card grading happens on /quiz/* endpoints, outside any chat turn, so the
    # supervisor's inline peek never saw these verdicts — every concept stayed
    # at seed difficulty ("engaged" only). Normalize to the graph node id so
    # the read side (TeachingContext.concept_key) finds them.
    try:
        if not verdict or not concept:
            return
        from ..agents.teaching_engine import (TeachingMode, TeachingOutcome,
                                              get_teaching_manager,
                                              is_enabled as te_enabled)
        if not te_enabled():
            return
        from ..agents.student_model.store import DEFAULT_STUDENT_ID
        sid = student_id or DEFAULT_STUDENT_ID
        ckey = str(concept)
        try:
            from ..agents.student_model import (get_student_model,
                                                is_enabled as sm_enabled)
            if sm_enabled():
                node = get_student_model(sid).load().graph.match_concept(ckey)
                if node is not None:
                    ckey = node.id
        except Exception:
            pass
        outcome = {"correct": TeachingOutcome.CORRECT,
                   "wrong": TeachingOutcome.WRONG}.get(
                       verdict, TeachingOutcome.PARTIAL)
        get_teaching_manager().record_turn(
            sid, ckey, mode=TeachingMode.PRACTICE, outcome=outcome,
            note=str(concept)[:40])
    except Exception:
        pass
    # 4. M9: committed recall evidence for SRS + task attribution (W4/A08).
    # Card grading happens on /quiz/* endpoints, outside any chat turn — the
    # supervisor's same-turn peek never saw these verdicts, so until now the
    # main grading path fed M9 nothing (no SRS quality update, no task
    # progress). record_quiz_evidence is attempt-idempotent; fail-open.
    try:
        if student_id and verdict and concept:
            from ..agents.learning_orchestration import (
                get_orchestration_service,
                is_enabled as orch_enabled)
            if orch_enabled():
                get_orchestration_service().record_quiz_evidence(
                    student_id=student_id, concept=concept, verdict=verdict,
                    attempt_id=attempt_id, session_id=session_id,
                    subject=subject)
    except Exception:
        pass
