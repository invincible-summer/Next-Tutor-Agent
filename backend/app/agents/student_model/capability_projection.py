"""W3 v2 capability projection + BKT replay on supersede (§8.5/§8.6.2).

The v2 projection answers「这个学生在每个概念×能力维度上，证据支持什么」from
the append-only events log + the learning ledger — derived on read, rebuilt by
construction, NO new storage root (sandbox/orphan registration not needed).

Aggregation rules (deterministic, deliberately conservative — 判定策略待试点
金标校准, §7.4):

  demonstrated_in_scope  ≥2 distinct rubric_ids observed the dimension as met
  needs_recheck          met AND not_met/partial evidence conflict
  developing             some observation, threshold not met
  not_observed           no observation at all (the default — 没有证据不宣称会)

Validity: quiz_graded events whose attempt_id the ledger shows as superseded
are excluded (a superseded grade must not keep shaping conclusions). partial
events carry no BKT move (W2) and only count as partial observations.
Assistance-marked events (hint requested, W3/F02) are kept but do not count
toward the independent tally.

``rebuild_mastery`` replays the valid binary observation sequence from scratch
per skill (same params, same order incl. MASTERY_RESET) — the honest way to
"undo" a Bayesian update (§8.5: 不能简单减去旧分数假装撤销). Low-frequency by
construction: called only from re-answer paths, idempotent, before/after
diff logged for audit.
"""
from __future__ import annotations

import logging
from typing import Any

from .mastery import Mastery, MasteryTracker
from .state import EventType, LearningEvent
from .store import read_events

logger = logging.getLogger(__name__)

DIMENSIONS = ("concept", "procedure", "reasoning", "transfer", "retention",
              "self_check")

STATUS_DEMONSTRATED = "demonstrated_in_scope"
STATUS_DEVELOPING = "developing"
STATUS_NEEDS_RECHECK = "needs_recheck"
STATUS_NOT_OBSERVED = "not_observed"

# 一条 v2 维度聚合的证据引用上限（F05 展示用，避免无界列表）。
_MAX_EVIDENCE_ITEMS = 20
# demonstrated_in_scope 需要的不同题族（rubric_id）数量下限（§7.4：证据不能
# 全来自同一道/同一模板；具体门槛属待校准的产品判定策略）。
_MIN_FAMILIES = 2


def _superseded_attempt_ids(student_id: str) -> set[str]:
    """Attempt ids the ledger marks as superseded (their dict carries a
    ``superseded_by``). Disputed attempts stay counted (conservative: an
    unresolved objection does not erase evidence by itself)."""
    out: set[str] = set()
    try:
        from ...core.learning_records import list_records
        for record in list_records(student_id):
            for attempt in (record.get("attempts") or []):
                if isinstance(attempt, dict) and attempt.get("superseded_by"):
                    aid = str(attempt.get("attempt_id") or "")
                    if aid:
                        out.add(aid)
    except Exception:
        return set()
    return out


def _read_all_events(student_id: str) -> list[LearningEvent]:
    """Full event log in file (chronological) order — replay must not be
    silently truncated by the display cap."""
    return read_events(student_id, limit=10 ** 9)


def _valid_quiz_graded(student_id: str,
                       superseded: set[str]) -> list[LearningEvent]:
    out: list[LearningEvent] = []
    for ev in _read_all_events(student_id):
        if ev.type != EventType.QUIZ_GRADED:
            continue
        attempt_id = str(ev.payload.get("attempt_id") or "")
        if attempt_id and attempt_id in superseded:
            continue
        out.append(ev)
    return out


# --- BKT replay ---------------------------------------------------------------

def rebuild_mastery(student_id: str, *, reason: str = "") -> dict[str, Any]:
    """Replay legacy BKT from the valid event sequence. Idempotent; returns
    the before/after diff for audit logging. Never raises."""
    try:
        from .manager import get_student_model
        sm = get_student_model(student_id).load()
        superseded = _superseded_attempt_ids(student_id)
        tracker: MasteryTracker = sm.mastery
        before = {sid: round(m.p_known, 4) for sid, m in tracker.records.items()}

        # per-skill replay: fresh record seeded with the skill's own params
        # (custom params must reproduce), then the exact observation sequence.
        replayed: dict[str, Mastery] = {}
        reset = False
        replay_count = 0
        for ev in _read_all_events(student_id):
            if ev.type == EventType.MASTERY_RESET:
                sid = str(ev.payload.get("skill_id") or "")
                if sid in replayed:
                    replayed[sid] = Mastery(
                        skill_id=sid, params=replayed[sid].params)
                reset = True
                continue
            if ev.type != EventType.QUIZ_GRADED:
                continue
            payload = ev.payload
            sid = str(payload.get("skill_id") or "")
            if not sid:
                continue
            if str(payload.get("verdict") or "") == "partial":
                continue  # W2 dual-track: partial never moved BKT
            attempt_id = str(payload.get("attempt_id") or "")
            if attempt_id and attempt_id in superseded:
                continue
            correct = bool(payload.get("correct"))
            if not isinstance(replayed.get(sid), Mastery):
                old = tracker.records.get(sid)
                replayed[sid] = Mastery(skill_id=sid,
                                        params=old.params if old else None)
            replayed[sid].update_binary(correct, note=str(payload.get("note") or ""))
            replay_count += 1

        changed: dict[str, list[float]] = {}
        for sid, fresh in replayed.items():
            tracker.records[sid] = fresh
            old_val = before.get(sid)
            new_val = round(fresh.p_known, 4)
            if old_val is None or old_val != new_val:
                changed[sid] = [old_val if old_val is not None else None, new_val]
        if replayed:
            try:
                sm._persist()  # same write path as record_events (package-internal)
            except Exception:
                pass
        if changed:
            logger.info("mastery replay student=%s reason=%s changed=%s",
                        student_id, reason or "supersede", changed)
        return {"replayed": replay_count, "reset_seen": reset,
                "changed": changed}
    except Exception:
        logger.exception("mastery replay failed student=%s", student_id)
        return {"replayed": 0, "reset_seen": False, "changed": {}}


# --- v2 capability projection --------------------------------------------------

def _dimension_status(observations: list[dict[str, Any]]) -> str:
    """Deterministic acceptance per concept×dimension (see module docstring)."""
    if not observations:
        return STATUS_NOT_OBSERVED
    met_families = {str(o.get("rubric_id") or o.get("attempt_id") or "")
                    for o in observations if o.get("result") == "met"}
    met_families.discard("")
    has_met = any(o.get("result") == "met" for o in observations)
    has_negative = any(o.get("result") in ("not_met", "partial")
                       for o in observations)
    if has_met and len(met_families) >= _MIN_FAMILIES:
        return STATUS_DEMONSTRATED
    if has_met and has_negative:
        return STATUS_NEEDS_RECHECK
    return STATUS_DEVELOPING


def project_capabilities(student_id: str, *,
                         concept: str = "") -> dict[str, Any]:
    """Aggregate valid quiz_graded events into the concept × dimension view.

    Derived on read — no cache file, no second mastery store (§8.2: M2 是唯一
    当前能力视图，可由账本重建)."""
    try:
        superseded = _superseded_attempt_ids(student_id)
        events = _valid_quiz_graded(student_id, superseded)
    except Exception:
        events = []

    concepts: dict[str, dict[str, Any]] = {}
    for ev in events:
        payload = ev.payload
        ckey = str(payload.get("concept") or payload.get("knowledge_point") or "")
        if not ckey:
            continue
        if concept and ckey != concept:
            continue
        entry = concepts.setdefault(ckey, {
            "concept": ckey,
            "evidence_count": 0,
            "independent_count": 0,
            "dimensions": {dim: [] for dim in DIMENSIONS},
            "evidence": [],
        })
        attempt_id = str(payload.get("attempt_id") or "")
        assisted = bool(payload.get("assistance"))
        entry["evidence_count"] += 1
        if not assisted:
            entry["independent_count"] += 1
        if len(entry["evidence"]) < _MAX_EVIDENCE_ITEMS:
            entry["evidence"].append({
                "attempt_id": attempt_id,
                "verdict": str(payload.get("verdict") or ""),
                "assistance": bool(payload.get("assistance")),
                "ts": ev.ts,
            })
        caps = payload.get("observed_capabilities")
        rubric_id = str(payload.get("rubric_id") or "")
        if isinstance(caps, dict):
            for dim in DIMENSIONS:
                result = str(caps.get(dim) or "")
                if result in ("met", "partial", "not_met", "not_observed"):
                    entry["dimensions"][dim].append({
                        "result": result, "rubric_id": rubric_id,
                        "attempt_id": attempt_id,
                    })

    out_concepts: list[dict[str, Any]] = []
    for ckey, entry in concepts.items():
        dims: dict[str, Any] = {}
        unknowns: list[str] = []
        for dim in DIMENSIONS:
            status = _dimension_status(entry["dimensions"][dim])
            dims[dim] = {
                "status": status,
                "evidence_count": len(entry["dimensions"][dim]),
            }
            if status == STATUS_NOT_OBSERVED:
                unknowns.append(dim)
        out_concepts.append({
            "concept": ckey,
            "evidence_count": entry["evidence_count"],
            "independent_count": entry["independent_count"],
            "dimensions": dims,
            "not_observed": unknowns,
            "evidence": entry["evidence"],
        })
    # 有证据的概念在前，按证据量降序；没有证据的概念不出现在列表里。
    out_concepts.sort(key=lambda c: (-c["evidence_count"], c["concept"]))
    return {
        "student_id": student_id,
        "schema": "capability.v2",
        "dimensions": list(DIMENSIONS),
        "concepts": out_concepts,
    }
