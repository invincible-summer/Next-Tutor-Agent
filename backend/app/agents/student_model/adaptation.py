"""Adaptive Teaching Policy: student state -> TeachingStrategy.

This is where "student intelligence" becomes "student-aware teaching". Given a
task understanding (what the student asked) and a snapshot of their state
(mastery / memory / profile), we produce a TeachingStrategy the planner and
executor consume as soft guidance:

  - if the asked concept has UNMET prerequisites, prepend a brief review of
    the weakest unmet prereq before teaching (the spec's "先补函数再讲导数");
  - pick an explanation depth from mastery + learning_style (a struggling
    student gets basic-first; a confident one gets deeper derivation);
  - suggest quiz difficulty from current mastery of the concept;
  - surface misconceptions and recent mistakes so the teacher can address them.

Pure rules, deterministic, no LLM. The strategy is advisory (the LLM still
drives the actual answer); we only constrain *how* to teach, not *what* facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mastery import MasteryTracker
from .skill_graph import MASTERY_MET_THRESHOLD, SkillGraph, SkillNode
from .state import ConceptRecord, ConceptState, StudentProfile

# mastery bands
_BAND_NOVICE = 0.3      # below -> novice, basics first
_BAND_PROGRESSING = 0.6 # below -> progressing; above -> solid


@dataclass
class TeachingStrategy:
    """The adaptive output fed into the planner/executor as soft guidance."""
    target_skill_id: str = ""
    target_concept: str = ""
    # whether we should review prerequisites first (and which ones)
    review_first: list[SkillNode] = field(default_factory=list)
    explanation_depth: str = "adaptive"   # basic | deep | adaptive
    explanation_style: str = "balanced"   # step_by_step | examples_first | balanced
    suggested_quiz_difficulty: str = "medium"  # easy | medium | hard
    misconceptions: list[str] = field(default_factory=list)
    recent_mistakes: list[str] = field(default_factory=list)
    rationale: str = ""                   # one-line human reason, for trace/debug
    plan_hints: list[str] = field(default_factory=list)  # soft plan instructions

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_skill_id": self.target_skill_id,
            "target_concept": self.target_concept,
            "review_first": [n.name for n in self.review_first],
            "explanation_depth": self.explanation_depth,
            "explanation_style": self.explanation_style,
            "suggested_quiz_difficulty": self.suggested_quiz_difficulty,
            "misconceptions": list(self.misconceptions),
            "recent_mistakes": list(self.recent_mistakes),
            "rationale": self.rationale,
            "plan_hints": list(self.plan_hints),
        }


def adapt(concept: str, subject: str, grade: str,
          profile: StudentProfile, mastery: MasteryTracker,
          memory: dict[str, ConceptRecord], graph: SkillGraph,
          *, intent: str = "explain") -> TeachingStrategy:
    """Build a TeachingStrategy for one teaching target.

    `intent` is the TaskType value (explain/practice/...). The strategy adapts
    depth/difficulty/review accordingly; it never returns None.
    """
    strat = TeachingStrategy(target_concept=concept)
    target = graph.match_concept(concept) if concept else None
    mastery_view = mastery.to_dict()

    # --- 1. prerequisite review ------------------------------------------------
    if target is not None:
        strat.target_skill_id = target.id
        unmet = graph.unmet_prerequisites(target.id, mastery_view)
        if unmet and intent in ("explain", "solve", "plan"):
            # weakest first: sort by mastery ascending; cap to 1 for focus
            unmet.sort(key=lambda n: float((mastery_view.get(n.id) or {}).get("p_known", 0)))
            strat.review_first = unmet[:1]
            names = "、".join(n.name for n in strat.review_first)
            strat.rationale = f"检测到前置知识不足（{names}），建议先简短回顾再进入新内容。"
            strat.plan_hints.append(
                f"在讲解「{target.name}」之前，先用一两句话回顾前置知识「{names}」，"
                "确认学生跟得上再继续。"
            )

    # --- 2. explanation depth from mastery + style -----------------------------
    p = 0.0
    if target is not None:
        rec = mastery.get(target.id)
        p = rec.p_known if rec else 0.0
    style_pref = (profile.learning_style.preference if profile.learning_style
                  else "balanced")
    style_depth = (profile.learning_style.explanation_depth if profile.learning_style
                   else "adaptive")

    if style_depth in ("basic", "deep"):
        strat.explanation_depth = style_depth
    elif p < _BAND_NOVICE:
        strat.explanation_depth = "basic"
    elif p >= _BAND_PROGRESSING:
        strat.explanation_depth = "deep"
    else:
        strat.explanation_depth = "adaptive"
    strat.explanation_style = style_pref

    # --- 3. quiz difficulty from mastery --------------------------------------
    if p < _BAND_NOVICE:
        strat.suggested_quiz_difficulty = "easy"
    elif p >= _BAND_PROGRESSING:
        strat.suggested_quiz_difficulty = "hard"
    else:
        strat.suggested_quiz_difficulty = "medium"

    # --- 4. misconceptions + recent mistakes ----------------------------------
    if target is not None:
        rec = memory.get(target.id)
        if rec:
            if rec.state == ConceptState.MISCONCEPTION and rec.misconceptions:
                strat.misconceptions = list(rec.misconceptions[-2:])
        mrec = mastery.get(target.id)
        if mrec and mrec.mistakes:
            strat.recent_mistakes = list(mrec.mistakes[-3:])
    # also surface any concept-memory misconceptions for the bare concept string
    bare = memory.get(concept) if concept else None
    if bare and bare.misconceptions and not strat.misconceptions:
        strat.misconceptions = list(bare.misconceptions[-2:])

    # --- 5. rationale default --------------------------------------------------
    if not strat.rationale:
        if strat.explanation_depth == "basic":
            strat.rationale = "学生当前掌握度偏低，建议先打基础、多举例、少推导。"
            strat.plan_hints.append("讲解以直观例子和基础概念为主，减少抽象推导。")
        elif strat.explanation_depth == "deep":
            strat.rationale = "学生掌握度较好，可以深入推导与拓展。"
            strat.plan_hints.append("可以加入更深入的推导、变式与拓展联系。")
        else:
            strat.rationale = "按学生当前水平自适应讲解。"

    if strat.misconceptions:
        strat.plan_hints.append(
            "注意纠正这些已有误解：" + "；".join(strat.misconceptions) + "。"
        )
    return strat
