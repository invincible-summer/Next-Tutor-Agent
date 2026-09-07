"""Persistence layer for the Memory Intelligence layer (M6).

Mirrors student_model/store.py and knowledge/store.py. Legacy detailed
episodic/semantic files remain available for compatibility reads and explicit
audit tooling, but production turns no longer write them. Active procedural
strategy aggregates use a separate bounded ``<id>.procedural.json`` file, so
updating current behavior never mutates legacy ``<id>.semantic.json``.

Path-traversal guarded identically (Path(name).name strip). Every function is
defensive: a corrupt/missing file is treated as "no state yet" so a bad file
can never break a chat turn.

Lives at project-root students/ alongside the M2 student blob, M3 teaching
log, and M2 events log -- same .gitignore coverage, same student-scoping.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .schema import (EpisodicMemory, ProceduralMemory, SemanticFact,
                     SEMANTIC_CATEGORIES)
from ...core.atomic import atomic_write_text, file_lock

# students/ lives at the project root (parent of backend/), independent of
# the backend cwd -- same resolution policy as chat_history / student_model.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

# Compatibility-store caps. Production writes use prompt/procedural/habit
# aggregate files; these caps remain for explicit legacy tools/tests.
_MAX_SEMANTIC_FACTS = 60
_MAX_SUPERSEDED_AUDIT = 20  # superseded 事实只保留最近 20 条审计痕迹（P4 记忆卫生）
_MAX_PROCEDURAL = 40
_MAX_EPISODES_REPLAY = 500  # in-memory replay cap for retrieval; jsonl is uncapped
# P4 记忆卫生：episodic 主文件物理上限——超出约 512KB（约千条）时把较旧部分
# 归档到 .episodes_archive.jsonl（仍 append-only 可查），主文件只留最近 500 条。
_EPISODE_COMPACT_BYTES = 512 * 1024


def _resolve(student_id: str, ext: str = ".semantic.json") -> Path:
    """Resolve a bare student id to a Path under students/. Path-traversal
    guard mirroring core/session._resolve and student_model/store._resolve."""
    bare = Path(student_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _STUDENTS_DIR / f"{bare}{ext}"


def _ensure_dir() -> None:
    try:
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# --- episodic memory: append-only JSONL black box ---------------------------

def _episode_id() -> str:
    """Compact unique id: timestamp + short random suffix."""
    import uuid
    return f"ep_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def append_episode(student_id: str, episode: EpisodicMemory) -> bool:
    """Append one episodic memory to the student's episodes.jsonl.

    Never raises into a turn: failures are swallowed (best-effort black box,
    like the chat transcript append). Returns True on success.
    """
    if not episode.id:
        episode.id = _episode_id()
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".episodes.jsonl")
        # 锁防 asyncio 线程池并发 append 交错出半行
        with file_lock(path), path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(episode.to_dict(), ensure_ascii=False) + "\n")
        _maybe_compact_episodes(student_id, path)
        return True
    except Exception:
        return False


def _maybe_compact_episodes(student_id: str, path: Path) -> None:
    """P4 记忆卫生：主文件超过约 512KB 时，较旧部分归档到
    .episodes_archive.jsonl（append-only 仍可查），主文件只留最近
    _MAX_EPISODES_REPLAY 条。此前 jsonl 只增不减，长期重度使用会无限膨胀。
    stat 只读元数据，代价可忽略；任何异常静默跳过（下次 append 再试）。"""
    try:
        if path.stat().st_size < _EPISODE_COMPACT_BYTES:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= _MAX_EPISODES_REPLAY:
            return
        archive = _resolve(student_id, ext=".episodes_archive.jsonl")
        with file_lock(path):
            with archive.open("a", encoding="utf-8") as af:
                af.write("\n".join(lines[:-_MAX_EPISODES_REPLAY]) + "\n")
            atomic_write_text(path, "\n".join(lines[-_MAX_EPISODES_REPLAY:]) + "\n")
    except Exception:
        pass


def read_episodes(student_id: str, limit: int = _MAX_EPISODES_REPLAY) -> list[EpisodicMemory]:
    """Read up to `limit` most-recent episodes. Skips bad lines.

    Returns episodes oldest-first (chronological order for retrieval context).
    """
    path = _resolve(student_id, ext=".episodes.jsonl")
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[EpisodicMemory] = []
    for line in lines[-limit:]:
        try:
            out.append(EpisodicMemory.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def remove_episodes_for_session(student_id: str, session_id: str) -> int:
    """Permanently remove attributable prompt-adjacent episodes for a chat.

    Legacy rows without ``session_id`` cannot be guessed and are left intact;
    independent learning results live in ``learning_records.json`` instead.
    """
    removed = 0
    for ext in (".episodes.jsonl", ".episodes_archive.jsonl"):
        path = _resolve(student_id, ext=ext)
        if not path.exists():
            continue
        try:
            with file_lock(path):
                kept: list[str] = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except Exception:
                        kept.append(line)
                        continue
                    if str(row.get("session_id") or "") == session_id:
                        removed += 1
                    else:
                        kept.append(line)
                atomic_write_text(path, ("\n".join(kept) + "\n") if kept else "")
        except Exception:
            continue
    return removed


# --- semantic + procedural: JSON working set (full rewrite) -----------------

def _empty_working_set() -> dict[str, Any]:
    return {"semantic": [], "procedural": [], "updated_at": 0.0}


def _load_working_set(student_id: str) -> dict[str, Any]:
    """Load the raw working-set JSON; missing/corrupt -> empty set."""
    path = _resolve(student_id, ext=".semantic.json")
    if not path.exists():
        return _empty_working_set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_working_set()
        data.setdefault("semantic", [])
        data.setdefault("procedural", [])
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return _empty_working_set()


def _save_working_set(student_id: str, data: dict[str, Any]) -> None:
    try:
        _ensure_dir()
        data["updated_at"] = time.time()
        path = _resolve(student_id, ext=".semantic.json")
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass  # best-effort; never break a turn


# --- semantic facts ---------------------------------------------------------

def load_semantic_facts(student_id: str) -> list[SemanticFact]:
    """Return all active (non-superseded) semantic facts."""
    data = _load_working_set(student_id)
    out: list[SemanticFact] = []
    for d in (data.get("semantic") or []):
        if not isinstance(d, dict):
            continue
        fact = SemanticFact.from_dict(d)
        if not fact.superseded_by:  # skip superseded facts
            out.append(fact)
    return out


def load_all_semantic_facts(student_id: str) -> list[SemanticFact]:
    """Return ALL semantic facts including superseded ones (for audit/debug)."""
    data = _load_working_set(student_id)
    return [SemanticFact.from_dict(d) for d in (data.get("semantic") or [])
            if isinstance(d, dict)]


def save_semantic_facts(student_id: str, facts: list[SemanticFact]) -> None:
    """Persist the full semantic-facts list (overwrites). Trims to cap.

    P4 记忆卫生：active 与 superseded 分开截断——active 保留最近 60 条，
    superseded 审计痕迹只留最近 20 条（此前两类共享同一个 FIFO 预算，
    被取代的旧事实会永久滞留文件）。相对顺序保持不变。
    """
    with file_lock(_resolve(student_id, ext=".semantic.json")):
        data = _load_working_set(student_id)
        active = [f for f in facts if not f.superseded_by]
        retired = [f for f in facts if f.superseded_by]
        keep = {id(f) for f in active[-_MAX_SEMANTIC_FACTS:]} | \
               {id(f) for f in retired[-_MAX_SUPERSEDED_AUDIT:]}
        data["semantic"] = [f.to_dict() for f in facts if id(f) in keep]
        _save_working_set(student_id, data)


def add_or_update_semantic_fact(student_id: str, fact: SemanticFact) -> None:
    """Add a new fact or update an existing matching one (same fact+category+scope).

    The ConflictResolver (semantic.py) decides whether to update-in-place
    (supporting evidence) or supersede the old fact (contradiction). This is
    the low-level write primitive. Never raises.
    """
    if fact.category and fact.category not in SEMANTIC_CATEGORIES:
        return  # reject unknown categories (bad LLM cannot corrupt the store)
    try:
        with file_lock(_resolve(student_id, ext=".semantic.json")):
            facts = load_all_semantic_facts(student_id)
            if not fact.id:
                import uuid as _uuid
                fact.id = f"sf_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:6]}"
            # replace same id, else append
            found = False
            for i, f in enumerate(facts):
                if f.id == fact.id:
                    facts[i] = fact
                    found = True
                    break
            if not found:
                facts.append(fact)
            save_semantic_facts(student_id, facts)
    except Exception:
        pass


def supersede_semantic_fact(student_id: str, old_id: str, new_id: str) -> None:
    """Mark old_id as superseded by new_id (conflict resolution, non-destructive)."""
    try:
        with file_lock(_resolve(student_id, ext=".semantic.json")):
            facts = load_all_semantic_facts(student_id)
            for f in facts:
                if f.id == old_id and not f.superseded_by:
                    f.superseded_by = new_id
            save_semantic_facts(student_id, facts)
    except Exception:
        pass


# --- procedural memory ------------------------------------------------------

def _procedural_path(student_id: str) -> Path:
    return _resolve(student_id, ext=".procedural.json")


def load_procedural(student_id: str) -> list[ProceduralMemory]:
    """Return active strategy aggregates, with legacy fallback on first use.

    Once the dedicated file exists it is authoritative. Before that, old
    ``semantic.json.procedural`` rows are read without modifying the legacy
    file; the next save naturally migrates the bounded snapshot.
    """
    path = _procedural_path(student_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("items") if isinstance(data, dict) else []
            return [ProceduralMemory.from_dict(d) for d in (rows or [])
                    if isinstance(d, dict)]
        except Exception:
            return []
    data = _load_working_set(student_id)
    return [ProceduralMemory.from_dict(d) for d in (data.get("procedural") or [])
            if isinstance(d, dict)]


def save_procedural(student_id: str, items: list[ProceduralMemory]) -> None:
    """Persist bounded active strategy aggregates outside legacy semantic data."""
    path = _procedural_path(student_id)
    try:
        _ensure_dir()
        with file_lock(path):
            atomic_write_text(path, json.dumps({
                "version": 1, "updated_at": time.time(),
                "items": [p.to_dict() for p in items[-_MAX_PROCEDURAL:]],
            }, ensure_ascii=False, indent=2))
    except Exception:
        pass


_MAX_HABIT_PATTERNS = 40


def _habit_path(student_id: str) -> Path:
    return _resolve(student_id, ext=".habit_patterns.json")


def load_habit_patterns(student_id: str) -> list[dict[str, Any]]:
    """Load active bounded habit aggregates, falling back to legacy facts."""
    path = _habit_path(student_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [dict(x) for x in (data.get("items") or [])
                    if isinstance(x, dict)] if isinstance(data, dict) else []
        except Exception:
            return []
    # Compatibility read only: do not rewrite semantic.json here.
    out: list[dict[str, Any]] = []
    for fact in load_semantic_facts(student_id):
        if fact.category == "study_habit":
            out.append({"fact": fact.fact, "confidence": fact.confidence,
                        "evidence_count": fact.evidence_count,
                        "subject": fact.subject,
                        "created_ts": fact.created_ts,
                        "updated_ts": fact.updated_ts})
    return out[-_MAX_HABIT_PATTERNS:]


def save_habit_patterns(student_id: str, items: list[dict[str, Any]]) -> None:
    """Persist bounded habit aggregates without touching legacy semantic data."""
    path = _habit_path(student_id)
    try:
        _ensure_dir()
        with file_lock(path):
            atomic_write_text(path, json.dumps({
                "version": 1, "updated_at": time.time(),
                "items": [dict(x) for x in items[-_MAX_HABIT_PATTERNS:]],
            }, ensure_ascii=False, indent=2))
    except Exception:
        pass
