"""Error notebook over the independent learning-result ledger."""
from __future__ import annotations
from typing import Any

_ERROR_VERDICTS = ("wrong", "partial")
_MAX_ITEMS = 200


def collect_error_notebook(student_id: str, *, limit: int = _MAX_ITEMS) -> list[dict[str, Any]]:
    try:
        from .learning_records import list_records
        out = []
        records = list_records(student_id)
        if not records:
            # Compatibility for pre-ledger installations; new grading writes
            # the independent ledger before/alongside this cache.
            from .quiz_recent import list_recent_questions
            records = [{
                "session_id": q.get("session_id", ""),
                "source_status": q.get("source_status", "active"),
                "source_message": q.get("source_message", ""),
                "created_at": q.get("ts", 0), **q,
            } for q in list_recent_questions(student_id, limit=100)]
        if not records:
            # One-time compatibility read for installations that predate both
            # the independent ledger and quiz_recent snapshots. New writes no
            # longer depend on session files, but old quiz_history must remain
            # visible after upgrading.
            from .session import list_sessions, load_session
            from ..agents.student_model.store import DEFAULT_STUDENT_ID
            for meta in list_sessions():
                if (meta.get("student_id") or DEFAULT_STUDENT_ID) != student_id:
                    continue
                session = load_session(str(meta.get("session_id") or ""))
                if session is None:
                    continue
                for qh in session.quiz_history or []:
                    if not isinstance(qh, dict):
                        continue
                    for q in qh.get("questions") or []:
                        if not isinstance(q, dict) or not isinstance(q.get("result"), dict):
                            continue
                        records.append({
                            "session_id": session.session_id,
                            "source_status": "active", "source_kind": "chat",
                            "created_at": session.updated_at,
                            "topic": qh.get("topic") or qh.get("reference") or "",
                            "knowledge_point": q.get("knowledge_point") or "",
                            "stem": q.get("stem") or "", "type": q.get("type") or "",
                            "difficulty": q.get("difficulty") or "",
                            "correct_answer": q.get("answer") or "",
                            "explanation": q.get("explanation") or "",
                            "student_answer": q["result"].get("student_answer") or "",
                            "verdict": q["result"].get("verdict") or "",
                        })
        for q in records:
            if str(q.get("verdict") or "") not in _ERROR_VERDICTS:
                continue
            status = str(q.get("source_status") or "active")
            source_kind = str(q.get("source_kind") or "chat")
            out.append({
                "session_id": q.get("session_id", "") if status == "active" and source_kind == "chat" else "",
                "source_session_id": q.get("session_id", ""),
                "source_kind": source_kind,
                "source_status": status,
                "source_message": q.get("source_message") or (
                    "来源对话已删除，无法查看" if status == "deleted" else
                    "独立测评记录" if source_kind == "assessment" else ""),
                "session_title": "", "topic": str(q.get("topic") or "")[:40],
                "knowledge_point": str(q.get("knowledge_point") or "")[:60],
                "stem": str(q.get("stem") or "")[:300], "type": str(q.get("type") or "multiple_choice"),
                "difficulty": str(q.get("difficulty") or ""), "verdict": str(q.get("verdict") or ""),
                "student_answer": str(q.get("student_answer") or "")[:200],
                "correct_answer": str(q.get("correct_answer") or "")[:200],
                "explanation": str(q.get("explanation") or "")[:400],
                "score": q.get("score"), "ts": float(q.get("created_at") or 0.0),
            })
            if len(out) >= max(1, min(int(limit), _MAX_ITEMS)):
                break
        return out
    except Exception:
        return []
