"""Learning-path planning (Phase 3): what to learn next, and what to review.

The skill graph already knows "what depends on what" (prerequisites). But a
curriculum needs more than a DAG: it needs to answer "today learn X, next Y,
and revisit Z because it's going stale". This module composes that view from
two read-only inputs the caller assembles from the student model:

  - next_learnable: skills whose prerequisites are all met but the student has
    not mastered them (graph.next_learnable, already a primitive).
  - review_candidates: skills the student has seen but whose mastery is
    middling AND whose last review is ageing -- spaced-repetition style.

build_learning_path is a pure function over plain-data inputs (no student_model
types), keeping the package import-clean. It only runs when the task asks for a
plan (intent=plan) or when the student explicitly asks "what next"; otherwise
the strategy's focus stays on the current concept.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .state import BAND_NOVICE, BAND_PROGRESSING, BAND_STRONG

# spaced-repetition: a skill at middling mastery is worth reviewing once it has
# not been touched for this many seconds. ~3 days; tunable.
_REVIEW_STALE_SECONDS = 3 * 24 * 3600
_MAX_NEXT = 4
_MAX_REVIEW = 4


@dataclass
class PathNode:
    """One node in a learning path: a skill name + why it is here."""
    name: str = ""
    skill_id: str = ""
    difficulty: int = 3
    reason: str = ""   # "前置已满足，难度最低" / "掌握度中等且久未复习" etc.

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "skill_id": self.skill_id,
                "difficulty": self.difficulty, "reason": self.reason}


@dataclass
class LearningPath:
    """A forward+review plan for one subject, advisory (not a rigid schedule)."""
    current: PathNode | None = None
    next_nodes: list[PathNode] = field(default_factory=list)
    review_nodes: list[PathNode] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current.to_dict() if self.current else None,
            "next_nodes": [n.to_dict() for n in self.next_nodes],
            "review_nodes": [n.to_dict() for n in self.review_nodes],
            "rationale": self.rationale,
        }


def build_learning_path(
    *,
    current_name: str = "",
    current_skill_id: str = "",
    next_learnable: list[dict[str, Any]] | None = None,
    review_candidates: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> LearningPath:
    """Compose a LearningPath from plain-data inputs.

    next_learnable: [{name, skill_id, difficulty}] -- graph-ready skills.
    review_candidates: [{name, skill_id, mastery, last_review}] -- seen skills.
    Both default to empty so a caller with no graph data still gets a path
    centred on the current concept. Never raises.
    """
    try:
        now = now if now is not None else time.time()
        path = LearningPath()
        if current_name or current_skill_id:
            path.current = PathNode(name=current_name, skill_id=current_skill_id,
                                    reason="当前学习目标")
        # next: lowest-difficulty first, capped
        nxt = sorted(next_learnable or [], key=lambda n: (n.get("difficulty", 3),
                                                          n.get("name", "")))
        for n in nxt[:_MAX_NEXT]:
            path.next_nodes.append(PathNode(
                name=str(n.get("name", "")), skill_id=str(n.get("skill_id", "")),
                difficulty=int(n.get("difficulty", 3)),
                reason="前置已满足，按难度排序"))
        # review: middling mastery (between novice and strong) AND stale, or
        # explicitly flagged. Sort by staleness (oldest review first).
        revs: list[tuple[float, dict[str, Any]]] = []
        for r in (review_candidates or []):
            mastery = float(r.get("mastery", 0.0))
            last = float(r.get("last_review", 0.0))
            is_middling = BAND_NOVICE <= mastery < BAND_STRONG
            is_stale = (now - last) >= _REVIEW_STALE_SECONDS if last > 0 else False
            flagged = bool(r.get("flagged"))
            if (is_middling and is_stale) or flagged:
                # sort key: oldest review first (most overdue)
                revs.append((last if last > 0 else 0.0, r))
        revs.sort(key=lambda kv: kv[0])
        for _, r in revs[:_MAX_REVIEW]:
            stale_days = int((now - float(r.get("last_review", 0.0))) / 86400) \
                if r.get("last_review") else 0
            reason = (f"掌握度{float(r.get('mastery',0)):.2f}，"
                      f"{stale_days}天未复习" if stale_days
                      else f"掌握度{float(r.get('mastery',0)):.2f}，建议巩固")
            path.review_nodes.append(PathNode(
                name=str(r.get("name", "")), skill_id=str(r.get("skill_id", "")),
                difficulty=int(r.get("difficulty", 3)), reason=reason))

        # rationale
        parts = []
        if path.next_nodes:
            parts.append(f"下一步建议学「{path.next_nodes[0].name}」"
                         + (f"等{len(path.next_nodes)}项" if len(path.next_nodes) > 1 else ""))
        if path.review_nodes:
            parts.append(f"建议复习「{path.review_nodes[0].name}」"
                         + (f"等{len(path.review_nodes)}项" if len(path.review_nodes) > 1 else ""))
        path.rationale = "；".join(parts) or "暂无可规划路径"
        return path
    except Exception:
        return LearningPath(rationale="学习路径规划降级")
