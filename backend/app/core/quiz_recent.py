"""最近习题库：跨会话聚合最近生成的题目（每学生上限 100 道）。

出题结果原本只散落在各会话的 quiz_history 里，测评中心要做「最近习题」
总览就得扫描全部会话文件。本模块在出题成功时追加一份轻量快照
（题干/题型/难度/来源会话/时间），答题卡判分后按 (session_id, 题干前缀)
回填 verdict，供 GET /quiz/recent 分页展示。

存储镜像 teaching_log：students/<id>.quiz_recent.json，原子写 + 文件锁 +
路径穿越守卫；全部公开函数 fail-open，绝不阻断出题或判分主流程。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

# 每学生保留的最近习题上限：超出后按时间丢弃最旧的（FIFO）。
_CAP = 100
# 题干快照长度：列表展示 + 判分回填匹配都用前缀，与 quiz_attempts 的 60 字
# 匹配键保持一致。
_STEM_SNAPSHOT = 160
_STEM_KEY = 60


def _resolve(student_id: str) -> Path:
    bare = Path(student_id).name
    if bare.endswith(".quiz_recent.json"):
        bare = bare[: -len(".quiz_recent.json")]
    return _STUDENTS_DIR / f"{bare}.quiz_recent.json"


def _load(student_id: str) -> dict[str, Any]:
    path = _resolve(student_id)
    if not path.exists():
        return {"questions": [], "updated_at": 0.0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"questions": [], "updated_at": 0.0}
        data.setdefault("questions", [])
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {"questions": [], "updated_at": 0.0}


def _save(student_id: str, data: dict[str, Any]) -> None:
    try:
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = time.time()
        atomic_write_text(_resolve(student_id),
                          json.dumps(data, ensure_ascii=False, indent=2))
    except OSError:
        pass


def record_recent_quiz(session_id: str, student_id: str, quiz: dict[str, Any]) -> None:
    """出题成功时追加题目快照（每题一条），总量钳位到最近 100 道。"""
    try:
        if not student_id or not isinstance(quiz, dict):
            return
        questions = [q for q in (quiz.get("questions") or []) if isinstance(q, dict)]
        if not questions:
            return
        topic = str(quiz.get("topic") or quiz.get("reference") or "")[:40]
        grade = str(quiz.get("grade") or "")[:10]
        now = time.time()
        try:
            from .learning_records import record_question
            for q in questions:
                record_question(student_id, session_id, q,
                                topic=str(quiz.get("topic") or quiz.get("reference") or ""),
                                grade=grade)
        except Exception:
            pass
        with file_lock(_resolve(student_id)):
            data = _load(student_id)
            items = data.setdefault("questions", [])
            base = items[-1]["id"] + 1 if items and isinstance(items[-1].get("id"), int) else 1
            for i, q in enumerate(questions):
                stem = str(q.get("stem") or "").strip()
                if not stem:
                    continue
                items.append({
                    "id": base + i,
                    "ts": now,
                    "session_id": session_id,
                    "topic": topic or str(q.get("knowledge_point") or "")[:40],
                    "grade": grade,
                    "type": str(q.get("type") or "multiple_choice"),
                    "difficulty": str(q.get("difficulty") or "medium"),
                    "stem": stem[:_STEM_SNAPSHOT],
                    "knowledge_point": str(q.get("knowledge_point") or "")[:60],
                    "correct_answer": str(q.get("answer") or "")[:200],
                    "explanation": str(q.get("explanation") or "")[:400],
                    "verdict": "",
                    "student_answer": "",
                    "source_status": "active",
                })
            data["questions"] = items[-_CAP:]
            _save(student_id, data)
    except Exception:
        return


def record_recent_verdict(session_id: str, student_id: str, *, stem: str,
                          verdict: str, student_answer: str = "",
                          attempt_id: str = "") -> None:
    """答题卡判分回填：按 (session_id, 题干前缀) 匹配最新一条未判记录。

    ``attempt_id``（W2/A14）透传给账本：与 record_quiz_attempt 的第二路
    写入合并为同一条 attempt 记录（同作答幂等），而不是两份。"""
    try:
        if not student_id or not session_id or not verdict:
            return
        key = str(stem).strip()[:_STEM_KEY]
        if not key:
            return
        try:
            from .learning_records import record_verdict
            record_verdict(student_id, session_id, stem=stem, verdict=verdict,
                           student_answer=student_answer, attempt_id=attempt_id)
        except Exception:
            pass
        with file_lock(_resolve(student_id)):
            data = _load(student_id)
            items = data.get("questions") or []
            for q in reversed(items):
                if (q.get("session_id") == session_id
                        and str(q.get("stem", "")).strip()[:_STEM_KEY] == key):
                    q["verdict"] = verdict
                    if student_answer:
                        q["student_answer"] = str(student_answer)[:200]
                    break
            else:
                return
            _save(student_id, data)
    except Exception:
        return


def list_recent_questions(student_id: str, *, limit: int = _CAP) -> list[dict[str, Any]]:
    """最近习题，新→旧。limit 钳位到存储上限。"""
    try:
        items = _load(student_id).get("questions") or []
        out = [q for q in reversed(items) if isinstance(q, dict)]
        return out[: max(1, min(int(limit), _CAP))]
    except Exception:
        return []


def mark_session_source_deleted(student_id: str, session_id: str) -> int:
    """Retain independent learning records but disable deleted-chat jumps."""
    changed = 0
    try:
        with file_lock(_resolve(student_id)):
            data = _load(student_id)
            for item in data.get("questions") or []:
                if item.get("session_id") == session_id:
                    item["source_status"] = "deleted"
                    item["source_message"] = "来源对话已删除，无法查看"
                    changed += 1
            if changed:
                _save(student_id, data)
    except Exception:
        return 0
    return changed


def detach_session_source(student_id: str, session_id: str) -> int:
    """Irreversibly unlink cached quiz rows from a permanently erased chat."""
    changed = 0
    try:
        with file_lock(_resolve(student_id)):
            data = _load(student_id)
            for item in data.get("questions") or []:
                if item.get("session_id") == session_id:
                    item["session_id"] = ""
                    item["source_status"] = "deleted"
                    item["source_message"] = "来源对话已永久删除，无法查看"
                    changed += 1
            if changed:
                _save(student_id, data)
    except Exception:
        return 0
    return changed


def mark_session_source_active(student_id: str, session_id: str) -> int:
    changed = 0
    try:
        with file_lock(_resolve(student_id)):
            data = _load(student_id)
            for item in data.get("questions") or []:
                if item.get("session_id") == session_id:
                    item["source_status"] = "active"
                    item.pop("source_message", None)
                    changed += 1
            if changed:
                _save(student_id, data)
    except Exception:
        return 0
    return changed
