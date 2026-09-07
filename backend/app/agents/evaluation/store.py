"""Persistence layer for the Evaluation & Improvement Intelligence layer (M7).

Mirrors memory/store.py (M6) and teaching_engine/teaching_log.py (M3):
  - Turn traces: append-only JSONL (black box, uncapped) like the chat
    transcript, the M2 events log, and the M6 episodes log.
  - Strategy effectiveness + proposals + advisor state:
    JSON working set (full rewrite per save), bounded by caps.

Path-traversal guarded identically (Path(name).name strip). Every function is
defensive: a corrupt/missing file is treated as "no state yet" so a bad file
can never break a chat turn.

Lives at project-root students/ alongside the M2 student blob, M3 teaching
log, M6 memory -- same .gitignore coverage, same student-scoping.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .schema import (ImprovementProposal, PROPOSAL_STATUSES,
                     PROPOSAL_TARGETS, StrategyEffectiveness, TurnTrace,
                     MAX_PROPOSALS, MAX_STRATEGY_RECORDS, MAX_TRACES_REPLAY)
from ...core.atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_STUDENTS_DIR = _PROJECT_ROOT / "students"


def _resolve(student_id: str, ext: str = ".evaluation.json") -> Path:
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


# --- Turn traces: append-only JSONL black box -------------------------------

def _trace_id() -> str:
    return f"tt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def append_trace(student_id: str, trace: TurnTrace) -> bool:
    """Append one TurnTrace to the student's eval_traces.jsonl.

    Never raises into a turn: failures are swallowed (best-effort black box).
    Returns True on success.
    """
    if not trace.id:
        trace.id = _trace_id()
    try:
        _ensure_dir()
        path = _resolve(student_id, ext=".eval_traces.jsonl")
        # 锁防 asyncio 线程池并发 append 交错出半行
        with file_lock(path), path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_traces(student_id: str, limit: int = MAX_TRACES_REPLAY) -> list[TurnTrace]:
    """Read up to `limit` most-recent traces. Skips bad lines.

    Returns traces oldest-first (chronological order for analysis).
    """
    path = _resolve(student_id, ext=".eval_traces.jsonl")
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[TurnTrace] = []
    for line in lines[-limit:]:
        try:
            out.append(TurnTrace.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


# --- Working set: strategy effectiveness + proposals + experiments -----------

def _empty_working_set() -> dict[str, Any]:
    return {
        "strategies": [],
        "proposals": [],
        "advisor": {"traces_since_last": 0, "last_ts": 0.0},
        "updated_at": 0.0,
    }


def _load_working_set(student_id: str) -> dict[str, Any]:
    """Load the raw working-set JSON; missing/corrupt -> empty set."""
    path = _resolve(student_id, ext=".evaluation.json")
    if not path.exists():
        return _empty_working_set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_working_set()
        data.setdefault("strategies", [])
        data.setdefault("proposals", [])
        data.setdefault("advisor", {"traces_since_last": 0, "last_ts": 0.0})
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return _empty_working_set()


def _save_working_set(student_id: str, data: dict[str, Any]) -> None:
    try:
        _ensure_dir()
        data["updated_at"] = time.time()
        path = _resolve(student_id, ext=".evaluation.json")
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass  # best-effort; never break a turn


# --- strategy effectiveness -------------------------------------------------

def load_strategies(student_id: str) -> list[StrategyEffectiveness]:
    data = _load_working_set(student_id)
    return [StrategyEffectiveness.from_dict(d) for d in (data.get("strategies") or [])
            if isinstance(d, dict)]


def save_strategies(student_id: str, items: list[StrategyEffectiveness]) -> None:
    with file_lock(_resolve(student_id, ext=".evaluation.json")):
        data = _load_working_set(student_id)
        data["strategies"] = [s.to_dict() for s in items[-MAX_STRATEGY_RECORDS:]]
        _save_working_set(student_id, data)


def upsert_strategy(student_id: str, strat: StrategyEffectiveness) -> None:
    """Insert or replace a StrategyEffectiveness by (strategy, subject)."""
    try:
        with file_lock(_resolve(student_id, ext=".evaluation.json")):
            items = load_strategies(student_id)
            for i, s in enumerate(items):
                if s.strategy == strat.strategy and s.subject == strat.subject:
                    items[i] = strat
                    break
            else:
                items.append(strat)
            save_strategies(student_id, items)
    except Exception:
        pass


# --- improvement proposals -------------------------------------------------

def load_proposals(student_id: str) -> list[ImprovementProposal]:
    data = _load_working_set(student_id)
    return [ImprovementProposal.from_dict(d) for d in (data.get("proposals") or [])
            if isinstance(d, dict)]


def load_proposal(student_id: str, proposal_id: str) -> ImprovementProposal | None:
    for p in load_proposals(student_id):
        if p.id == proposal_id:
            return p
    return None


def save_proposals(student_id: str, items: list[ImprovementProposal]) -> None:
    with file_lock(_resolve(student_id, ext=".evaluation.json")):
        data = _load_working_set(student_id)
        data["proposals"] = [p.to_dict() for p in items[-MAX_PROPOSALS:]]
        _save_working_set(student_id, data)


def add_proposal(student_id: str, proposal: ImprovementProposal) -> bool:
    """Add a proposal after validating target/status whitelists.

    Rejects unknown targets/statuses so a bad advisor LLM cannot corrupt the
    store with arbitrary fields. Returns False on validation failure.
    """
    try:
        if proposal.target and proposal.target not in PROPOSAL_TARGETS:
            return False
        if proposal.status and proposal.status not in PROPOSAL_STATUSES:
            return False
        if not proposal.id:
            proposal.id = f"op_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        with file_lock(_resolve(student_id, ext=".evaluation.json")):
            items = load_proposals(student_id)
            items.append(proposal)
            save_proposals(student_id, items)
        return True
    except Exception:
        return False


def update_proposal_status(student_id: str, proposal_id: str,
                           status: str) -> bool:
    """Transition a proposal's status. Validates against the status whitelist.

    Transitioning to "applied" also stamps applied_ts (impact echo compares
    eval traces against it). Legacy applied proposals without applied_ts keep
    0.0 — the API treats that as "impact unknown", never as an error.
    """
    try:
        if status not in PROPOSAL_STATUSES:
            return False
        with file_lock(_resolve(student_id, ext=".evaluation.json")):
            items = load_proposals(student_id)
            found = False
            for p in items:
                if p.id == proposal_id:
                    p.status = status
                    if status == "applied" and not p.applied_ts:
                        p.applied_ts = time.time()
                    found = True
                    break
            if found:
                save_proposals(student_id, items)
        return found
    except Exception:
        return False


# --- advisor state (frequency gate) ------------------------------------

def load_advisor_state(student_id: str) -> dict[str, Any]:
    data = _load_working_set(student_id)
    return data.get("advisor") or {"traces_since_last": 0, "last_ts": 0.0}


def save_advisor_state(student_id: str, state: dict[str, Any]) -> None:
    with file_lock(_resolve(student_id, ext=".evaluation.json")):
        data = _load_working_set(student_id)
        data["advisor"] = state
        _save_working_set(student_id, data)
