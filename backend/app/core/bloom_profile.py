"""Bloom cognitive profile: deterministic aggregation over the learning ledger
(L1 档案层，单一真相源).

Aggregates per-concept per-level performance from learning_records (the
independent ledger that already survives chat deletion). Every record whose
question carried a "bloom_level" tag and was graded contributes:

    attempts / correct-ish count (correct=1, partial=0.5) per (concept, level)

Consumers all read THIS module — no parallel copies:
  - M4 generator + chat quiz tools: context_line() grounds level choice
  - M9 daily_composer: weakness_lines() for grounding
  - profile page: weakness summary line
  - GET /student/bloom-profile: read-only API projection

Pure read-side; zero LLM; never raises. Old records without bloom_level are
counted only in totals, not in level aggregates.
"""
from __future__ import annotations

import time
from typing import Any

from .bloom import BLOOM_LEVELS, BLOOM_ZH, normalize_level


def _score_of(verdict: str) -> float:
    if verdict == "correct":
        return 1.0
    if verdict == "partial":
        return 0.5
    return 0.0


def profile_for(student_id: str) -> dict[str, Any]:
    """{concepts: {concept: {levels: {lv: {attempts, correct}}, last_at}},
    overall: {lv: {...}}, weaknesses: [...], totals, updated_at}."""
    out: dict[str, Any] = {
        "concepts": {}, "overall": {}, "weaknesses": [],
        "totals": {"records": 0, "tagged": 0}, "updated_at": time.time(),
    }
    if not student_id:
        return out
    try:
        from . import learning_records as lr
        records = lr.list_records(student_id)
    except Exception:
        return out
    overall: dict[str, dict[str, float]] = {}
    for r in records:
        if not r.get("verdict"):
            continue
        out["totals"]["records"] += 1
        lv = normalize_level(r.get("bloom_level"))
        concept = str(r.get("knowledge_point") or "").strip()
        if not lv:
            continue
        out["totals"]["tagged"] += 1
        gain = _score_of(str(r.get("verdict") or ""))
        ts = float(r.get("updated_at") or 0)
        for bucket in (overall.setdefault(lv, {"attempts": 0, "correct": 0.0}),
                       out["concepts"].setdefault(
                           concept, {"levels": {}, "last_at": 0.0}
                       )["levels"].setdefault(lv, {"attempts": 0, "correct": 0.0})):
            bucket["attempts"] += 1
            bucket["correct"] += gain
        if ts > out["concepts"][concept]["last_at"]:
            out["concepts"][concept]["last_at"] = ts

    out["overall"] = {
        lv: {"attempts": int(v["attempts"]),
             "correct": round(v["correct"], 2),
             "rate": round(v["correct"] / v["attempts"], 3) if v["attempts"] else 0.0}
        for lv, v in overall.items()
    }

    # weaknesses: concept-level with >=2 attempts at one level and rate < 0.6
    weak: list[dict[str, Any]] = []
    for concept, cdata in out["concepts"].items():
        for lv, v in cdata["levels"].items():
            attempts = int(v["attempts"])
            if attempts < 2:
                continue
            rate = v["correct"] / attempts if attempts else 0.0
            if rate < 0.6:
                weak.append({"concept": concept, "level": lv,
                             "level_zh": BLOOM_ZH.get(lv, lv),
                             "attempts": attempts,
                             "rate": round(rate, 3)})
    weak.sort(key=lambda w: (w["rate"], -w["attempts"]))
    out["weaknesses"] = weak[:20]
    return out


def weakness_lines(student_id: str, limit: int = 5) -> list[str]:
    """Grounded one-liners like "导数 · 应用层级 2/5 不稳"，供 M9/建议 prompt 用。"""
    try:
        prof = profile_for(student_id)
        lines = []
        for w in prof.get("weaknesses", [])[:limit]:
            lines.append(
                f"{w['concept']}·{w['level_zh']}层级 {w['attempts']}次仅对{int(round(w['rate']*w['attempts']))}次")
        return lines
    except Exception:
        return []


def context_line(student_id: str, concept: str = "") -> str:
    """One-line snapshot for generator prompts ("" when no tagged data).

    Prioritizes the given concept's own levels, then the global weakest
    levels — enough signal for the LLM to pick a sensible level, no more."""
    try:
        prof = profile_for(student_id)
        if prof["totals"]["tagged"] == 0:
            return ""
        parts: list[str] = []
        if concept:
            cdata = prof["concepts"].get(concept)
            if cdata and cdata["levels"]:
                seg = "、".join(
                    f"{BLOOM_ZH.get(lv, lv)}{int(v['attempts'])}次"
                    f"对{int(round(v['correct']))}次"
                    for lv, v in sorted(cdata["levels"].items(),
                                        key=lambda kv: -kv[1]["attempts"])[:3])
                parts.append(f"该生在「{concept}」上：{seg}")
        weak = prof.get("weaknesses", [])[:3]
        if weak:
            parts.append("整体薄弱：" + "、".join(
                f"{w['concept']}·{w['level_zh']}" for w in weak))
        return "；".join(parts)[:300]
    except Exception:
        return ""
