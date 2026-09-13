"""Independent learning-result ledger.

Question/answer/score/concept/time records are learning outcomes, not chat
content. They survive conversation archive and permanent chat purge. A source
session id is retained only as a navigational reference and is blanked from the
user-facing view when that conversation is permanently gone.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STUDENTS_DIR = _PROJECT_ROOT / "students"


def _safe(value: str) -> str:
    return Path(str(value or "")).name


def _path(student_id: str) -> Path:
    return _STUDENTS_DIR / f"{_safe(student_id)}.learning_records.json"


def _load(student_id: str) -> dict[str, Any]:
    path = _path(student_id)
    if not path.exists():
        return {"version": 1, "records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "records": []}


def _save(student_id: str, data: dict[str, Any]) -> None:
    _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    data["version"] = 1
    data["updated_at"] = time.time()
    # This is the independent learning archive. Unlike the bounded UI cache
    # in quiz_recent, it must not silently discard older questions or scores.
    data["records"] = list(data.get("records") or [])
    atomic_write_text(_path(student_id), json.dumps(data, ensure_ascii=False, indent=2))


def _unique_ids(records: list[dict[str, Any]]) -> bool:
    """Re-key duplicate/empty record_ids in place; True when anything changed.

    Legacy question ids were per-quiz in-set numbers (each quiz restarted at
    1, every CAT question was "1"), so old ledgers can hold the same id
    several times; W2/A14 delivers per-set prefixed ids ("q_<uid>_<i>") for
    new questions, but the healing stays for pre-W2 data. Nothing joins on
    record_id at write time — verdicts match by session+stem (plus attempt
    ids since W2), trash handlers by session — so re-keying is safe.
    """
    seen: set[str] = set()
    changed = False
    for item in records:
        rid = str(item.get("record_id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            continue
        item["record_id"] = "lr_" + uuid.uuid4().hex[:16]
        changed = True
    return changed


def _compact_source_refs(refs: Any) -> list[dict[str, Any]]:
    """Compact, auditable projection of grounded quiz source refs.

    Keeps the locator fields (file/chunk/filename/pages) and a short excerpt;
    the full text stays recoverable via chunk_id.  Bad input -> [] (additive
    key, legacy records simply carry an empty list)."""
    out: list[dict[str, Any]] = []
    if isinstance(refs, list):
        for r in refs[:6]:
            if isinstance(r, dict) and str(r.get("chunk_id") or "").strip():
                out.append({
                    "file_id": str(r.get("file_id") or "")[:80],
                    "chunk_id": str(r.get("chunk_id") or "")[:120],
                    "filename": str(r.get("filename") or "")[:120],
                    "page": r.get("page"),
                    "printed_page": r.get("printed_page"),
                    "excerpt": str(r.get("excerpt") or "")[:200],
                })
    return out


def record_question(student_id: str, session_id: str, question: dict[str, Any], *,
                    topic: str = "", subject: str = "", grade: str = "",
                    source_kind: str = "chat") -> str:
    """Create or reuse a durable question record before grading."""
    if not student_id or not isinstance(question, dict):
        return ""
    question_id = str(question.get("id") or "") or "lr_" + uuid.uuid4().hex[:16]
    stem = str(question.get("stem") or "").strip()
    if not stem:
        return ""
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        healed = _unique_ids(data["records"])
        stem_key = stem[:100]
        for item in data["records"]:
            if (item.get("record_id") == question_id
                    and item.get("session_id") == _safe(session_id)
                    and str(item.get("stem") or "").strip()[:100] == stem_key):
                if healed:
                    _save(student_id, data)
                return question_id
        # Any other occurrence of this id — in another session, or earlier in
        # this one filing a different question — is an in-set numbering
        # collision, not a replay: give this record a fresh unique id.
        taken = {str(item.get("record_id") or "") for item in data["records"]}
        while question_id in taken:
            question_id = "lr_" + uuid.uuid4().hex[:16]
        record = {
            "record_id": question_id,
            "session_id": _safe(session_id),
            "source_kind": source_kind if source_kind in {"chat", "assessment"} else "chat",
            "source_status": "active" if source_kind == "chat" else "independent",
            "created_at": time.time(),
            "updated_at": time.time(),
            "topic": str(topic or question.get("topic") or "")[:100],
            "subject": str(subject or "")[:80],
            "grade": str(grade or "")[:40],
            "knowledge_point": str(question.get("knowledge_point") or question.get("concept") or "")[:120],
            "knowledge_points": list(question.get("knowledge_points") or [])[:20],
            # 布鲁姆认知层级标签（题目生成时由出题器标注；旧记录无此键安全缺省）
            "bloom_level": str(question.get("bloom_level") or "")[:24],
            "stem": stem[:1000],
            "type": str(question.get("type") or question.get("q_type") or "multiple_choice"),
            "difficulty": question.get("difficulty", ""),
            "correct_answer": str(question.get("answer") or "")[:1000],
            "explanation": str(question.get("explanation") or "")[:1500],
            "student_answer": "",
            "verdict": "",
            "score": None,
        }
        # Grounded provenance 审计引用（plan.md §3.2 原则 3）：账本只记可
        # 定位的紧凑 ref（chunk 原文按 chunk_id 可回读），旧题无此键安全
        # 缺省；mastery 证据等级不因客户端声称的 ref 改变。
        gmode = str(question.get("grounding_mode") or "")[:24]
        if gmode:
            record["grounding_mode"] = gmode
            gtier = str(question.get("grounding_tier") or "")[:16]
            if gtier:
                record["grounding_tier"] = gtier
        record["source_refs"] = _compact_source_refs(question.get("source_refs"))
        # W3/D04: 冻结量规的审计引用（量规本体随题目快照留在会话/测评文件，
        # 账本只记 rubric_id/version；旧题与生成失败无此键安全缺省）。
        rubric = question.get("rubric")
        if isinstance(rubric, dict) and str(rubric.get("rubric_id") or "").strip():
            record["rubric_id"] = str(rubric["rubric_id"])[:80]
            try:
                record["rubric_version"] = int(rubric.get("version", 1))
            except (TypeError, ValueError):
                record["rubric_version"] = 1
        data["records"].append(record)
        _save(student_id, data)
    return question_id


def flag_attempt_disputed(student_id: str, attempt_id: str, *,
                          reason: str = "") -> bool:
    """Mark one attempt as disputed by the student (W3/F05 minimal).

    Conservative semantics: a dispute is an auditable objection, NOT an
    erasure — the attempt keeps counting until it is superseded by a re-answer
    or a future review job decides otherwise (§9.2 review contract). The
    marker survives later appends (it is set on the attempt itself).
    """
    if not student_id or not attempt_id:
        return False
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        found = False
        for record in data["records"]:
            for attempt in (record.get("attempts") or []):
                if (isinstance(attempt, dict)
                        and str(attempt.get("attempt_id") or "") == attempt_id):
                    attempt["evidence_status"] = "disputed"
                    attempt["dispute_reason"] = str(reason)[:200]
                    attempt["disputed_at"] = time.time()
                    found = True
        if found:
            _save(student_id, data)
        return found


def record_verdict(student_id: str, session_id: str, *, stem: str,
                   verdict: str, student_answer: str = "", score: float | None = None,
                   concept: str = "", subject: str = "",
                   source_kind: str = "chat",
                   attempt_id: str = "", assessment_id: str = "",
                   evidence_status: str = "accepted",
                   criterion_results: list | None = None,
                   hypotheses: list | None = None) -> str:
    """Append one graded attempt to the matching record and refresh its
    current projection.

    W2/A14: verdicts are an append-only attempt chain per question record —
    a re-grade supersedes the previous attempt (``superseded_by``) instead of
    silently overwriting it, and the top-level verdict/score/student_answer
    stay the "current effective" view for every legacy reader (error notebook,
    quiz_recent, dashboards). The identical submission (same answer+verdict)
    is a replay: nothing is appended and the recorded attempt id is returned —
    this also collapses the double record_verdict call from the write-back +
    record_quiz_attempt path into one recorded attempt. Pre-W2 records get a
    ``legacy`` snapshot attempt synthesized from their current top-level
    values on first write, with provenance unknown (never fabricated high
    confidence; updatePlan.md §8.5 migration discipline). Returns the attempt
    id, or "" when nothing could be recorded.
    """
    if not student_id or not session_id or not stem:
        return ""
    key = str(stem).strip()[:100]
    attempt_id = attempt_id or ("att_" + uuid.uuid4().hex[:16])
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        candidates = [x for x in reversed(data["records"])
                      if x.get("session_id") == _safe(session_id)
                      and str(x.get("stem") or "").strip()[:100] == key]
        if not candidates:
            record_question(student_id, session_id, {
                "stem": stem, "answer": "", "explanation": "",
                "knowledge_point": concept, "type": "short_answer"},
                subject=subject, source_kind=source_kind)
            data = _load(student_id)
            candidates = [x for x in reversed(data["records"])
                          if x.get("session_id") == _safe(session_id)
                          and str(x.get("stem") or "").strip()[:100] == key]
        if not candidates:
            return ""
        item = candidates[0]
        attempts = item.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
            if item.get("verdict"):
                attempts.append({
                    "attempt_id": "att_legacy_" + uuid.uuid4().hex[:12],
                    "verdict": str(item.get("verdict") or ""),
                    "student_answer": str(item.get("student_answer") or "")[:1000],
                    "score": item.get("score"),
                    "evidence_status": "legacy",
                    "provenance": "unknown",
                    "created_at": float(item.get("updated_at")
                                        or item.get("created_at") or 0.0),
                })
            item["attempts"] = attempts
        latest = attempts[-1] if attempts else None
        if (latest is not None
                and str(latest.get("student_answer") or "")[:200]
                == str(student_answer or "")[:200]
                and str(latest.get("verdict") or "") == str(verdict or "")):
            # identical submission replay: keep the recorded attempt, no append
            return str(latest.get("attempt_id") or attempt_id)
        if latest is not None:
            latest["superseded_by"] = attempt_id
        attempt = {
            "attempt_id": attempt_id,
            "verdict": str(verdict or ""),
            "student_answer": str(student_answer or "")[:1000],
            "score": (float(score) if score is not None else None),
            "evidence_status": (evidence_status
                                if evidence_status in {"accepted", "abstained"}
                                else "accepted"),
            "created_at": time.time(),
        }
        # W3/D06：结构化评估明细（量规条目判定/错因假设）随 attempt 追加，
        # §8.2 AssessmentRecord 契约的最小落点；无结构化路径的旧调用不受影响。
        if isinstance(criterion_results, list) and criterion_results:
            attempt["criterion_results"] = [
                {k: (str(v)[:40] if k == "evidence_quote" else str(v)[:60])
                 for k, v in c.items() if isinstance(c, dict)}
                for c in criterion_results[:6] if isinstance(c, dict)]
        if isinstance(hypotheses, list) and hypotheses:
            attempt["hypotheses"] = [
                {"kind": str(h.get("kind") or "other")[:24],
                 "statement": str(h.get("statement") or "")[:80]}
                for h in hypotheses[:2] if isinstance(h, dict)]
        attempts.append(attempt)
        if assessment_id:
            item["assessment_id"] = str(assessment_id)
        item["verdict"] = str(verdict or "")
        item["student_answer"] = str(student_answer or "")[:1000]
        if score is not None:
            item["score"] = float(score)
        if concept:
            item["knowledge_point"] = str(concept)[:120]
        if subject:
            item["subject"] = str(subject)[:80]
        item["updated_at"] = time.time()
        _save(student_id, data)
        return attempt_id


def mark_source_deleted(student_id: str, session_id: str) -> int:
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        changed = 0
        for item in data["records"]:
            if item.get("session_id") == _safe(session_id) and item.get("source_status") != "deleted":
                item["source_status"] = "deleted"
                item["source_message"] = "来源对话已删除，无法查看"
                item["updated_at"] = time.time()
                changed += 1
        if changed:
            _save(student_id, data)
        return changed


def detach_source_session(student_id: str, session_id: str) -> int:
    """Irreversibly remove a permanently deleted chat's source identifier.

    Learning outcomes remain intact, but the ledger can no longer be joined
    back to the erased conversation. This is intentionally separate from
    ``mark_source_deleted`` so an archived (still restorable) chat retains its
    internal association until permanent purge.
    """
    sid = _safe(session_id)
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        changed = 0
        for item in data["records"]:
            if item.get("session_id") == sid:
                item["session_id"] = ""
                item["source_status"] = "deleted"
                item["source_message"] = "来源对话已永久删除，无法查看"
                item["updated_at"] = time.time()
                changed += 1
        if changed:
            _save(student_id, data)
        return changed


def mark_source_active(student_id: str, session_id: str) -> int:
    path = _path(student_id)
    with file_lock(path):
        data = _load(student_id)
        changed = 0
        for item in data["records"]:
            if item.get("session_id") == _safe(session_id) and item.get("source_status") == "deleted":
                item["source_status"] = "active"
                item.pop("source_message", None)
                item["updated_at"] = time.time()
                changed += 1
        if changed:
            _save(student_id, data)
        return changed


def list_records(student_id: str) -> list[dict[str, Any]]:
    try:
        records = list(reversed(_load(student_id).get("records") or []))
        # Display-side guard for legacy duplicate ids (persisted heal happens
        # on the next write via record_question); ids are not persisted here.
        _unique_ids(records)
        return records
    except Exception:
        return []
