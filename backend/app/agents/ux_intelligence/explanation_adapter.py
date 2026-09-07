"""Explanation adapter: the heart of M8.

Input: the teaching decision (concept / mode / intent from M1+M3) PLUS the UX
context (style + recent feedback + motivation). Output: a ResponseDirective --
a plain struct of "how to express this answer" that the context_builder turns
into the [交互智能·...] soft-instruction block.

BOUNDARY CONTRACT (M8 <-> M3, fixed per the M8 review):
  M3 OWNS the TeachingPlan (what to teach: concept, goal, strategy, mode,
  difficulty, misconception). M8 RECEIVES it read-only and OWNS only the
  ExplanationDirective (how to express: tone, length, examples, visualization,
  analogy level, pacing, what to avoid). M8 MUST NOT mutate the TeachingPlan
  -- build_directive() never writes back to M3. The data flow is one-way:

      M3 TeachingPlan (content decision)  --read-only-->  M8 build_directive()
      M8 UXProfile + InteractionHistory    --owned------>  ExplanationDirective
                                                            (presentation decision)
                                                            --> [交互智能·...] soft block --> LLM

  This keeps "teach what" (M3) and "present how" (M8) orthogonal: a student
  liking animations changes HOW Newton's 2nd law is framed, never WHETHER it
  is taught. The ResponseQualityEvaluator closes the loop by scoring the
  expression outcome and feeding it back into the UXProfile's presentation
  hints -- again never touching the TeachingPlan.

Crucially this does NOT regenerate knowledge. It produces expression guidance:
"this student found long answers too hard last time -> keep it short and lead
with an everyday example". The LLM still generates the actual content within
these advisory boundaries -- same advisory contract as M3/M5/M6/M7.

Also folds M2's academic explanation preference (read-only) so the directive
reflects BOTH "what kind of explanation" (M2) and "what register" (M8), without
either owning the other's data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import interaction_style as style_res
from .learner_profile import m2_learning_style_snapshot
from .motivation_engine import current_streak, milestone_due
from .schema import FeedbackType, Tone, UXProfile

# difficulty-communication mapping: how to FRAME a hard task for an anxious /
# fragile-confidence student. The difficulty VALUE is M3's; the framing is M8's.


@dataclass
class ResponseDirective:
    """The output of the explanation adapter: expression guidance for one turn.

    Rendered by context_builder into a [交互智能·...] system note. Advisory
    only; never alters content correctness.
    """
    style_lines: list[str] = field(default_factory=list)   # tone/detail/visual/pacing
    feedback_adjustment: str = ""                          # react to last feedback
    motivation_line: str = ""                              # milestone / streak nudge
    academic_style_note: str = ""                          # M2 read-only preference
    intent_guard: str = ""                                 # chitchat/quiz special-case notes

    def is_empty(self) -> bool:
        return not (self.style_lines or self.feedback_adjustment
                    or self.motivation_line or self.academic_style_note
                    or self.intent_guard)

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_lines": list(self.style_lines),
            "feedback_adjustment": self.feedback_adjustment,
            "motivation_line": self.motivation_line,
            "academic_style_note": self.academic_style_note,
            "intent_guard": self.intent_guard,
        }


def _feedback_adjustment(profile: UXProfile) -> str:
    """If the most recent experience signal was negative, tell the LLM how to
    adjust THIS turn to fix the experience (e.g. simplify after "too hard")."""
    if not profile.recent_feedback:
        return ""
    last = profile.recent_feedback[-1]
    if last == FeedbackType.EXPLANATION_TOO_HARD:
        return ("上一轮学生反馈「看不懂/太难」。本轮请降低门槛：先用一个生活化例子或类比建立直觉，"
                "再引入概念；避免一开始就堆公式与术语。")
    if last == FeedbackType.EXPLANATION_TOO_LONG:
        return "上一轮学生觉得太长。本轮请更精炼，先给结论，再补最短必要解释。"
    if last == FeedbackType.EXPLANATION_TOO_SHORT:
        return "上一轮学生觉得没讲清。本轮请展开关键步骤与「为什么」，确保逻辑闭环。"
    if last == FeedbackType.TOO_FAST:
        return "上一轮学生觉得节奏太快。本轮一次只推进一个要点。"
    if last == FeedbackType.TOO_SLOW:
        return "上一轮学生觉得节奏太慢。本轮可合并步骤、直奔要点。"
    if last == FeedbackType.PRAISE:
        return "上一轮学生表示听懂了/讲得好。可顺势给出一个小挑战或迁移题巩固。"
    return ""


def _academic_style_note(student_id: str) -> str:
    """Read M2's academic explanation preference (read-only) and render it as
    guidance, so the directive reflects BOTH academic style (M2) and register
    (M8). Returns "" when M2 is off or has no preference signal."""
    snap = m2_learning_style_snapshot(student_id)
    if not snap:
        return ""
    pref = str(snap.get("preference", "") or "")
    depth = str(snap.get("explanation_depth", "") or "")
    parts: list[str] = []
    if pref == "step_by_step":
        parts.append("该生偏好分步骤讲解（按学段画像·M2）")
    elif pref == "examples_first":
        parts.append("该生偏好先举例后归纳（按学段画像·M2）")
    if depth == "basic":
        parts.append("讲解以基础为主（按学段画像·M2）")
    elif depth == "deep":
        parts.append("可深入拓展（按学段画像·M2）")
    return "；".join(parts) + "。" if parts else ""


def _motivation_line(student_id: str, profile: UXProfile,
                     intent: str) -> str:
    """Surface a streak/milestone nudge at most once per milestone. Returns ""
    for assessment turns (don't congratulate during a quiz) and when nothing
    new to say."""
    if intent in ("quiz", "assess", "practice"):
        return ""
    streak = current_streak(student_id)
    due = milestone_due(streak, profile.motivation.last_milestone_surfaced)
    if due is None:
        return ""
    profile.motivation.last_milestone_surfaced = due
    profile.motivation.last_nudge_ts = profile.updated_at
    return (f"该生已连续学习 {streak} 天，达 {due} 天里程碑。"
            "可在回答开头用一句话肯定这份坚持（例如「你已经连续学习X天，状态很稳」），再进入正题。")


def _intent_guard(intent: str) -> str:
    """Special expression guidance for non-explain intents, so M8 does not
    fight other layers. E.g. during assessment, keep encouragement light and
    don't reveal answers."""
    if intent in ("quiz", "assess", "practice"):
        return "本轮为测评：保持鼓励但克制，不剧透答案，作答后才给讲解。"
    if intent == "chitchat":
        return "本轮为闲聊：语气可更轻松，但仍贴合该生画像与学段。"
    return ""


def build_directive(*, student_id: str, profile: UXProfile, concept: str = "",
                    subject: str = "", intent: str = "explain",
                    grade: str = "") -> ResponseDirective:
    """Compose the ResponseDirective for one turn. Pure over inputs (reads M2
    and M6 read-only inside the helpers). Mutates ONLY the motivation surfacing
    bookkeeping on the passed profile (the caller persists it)."""
    d = ResponseDirective()
    d.style_lines = style_res.preferred_style_block(profile)
    d.feedback_adjustment = _feedback_adjustment(profile)
    d.academic_style_note = _academic_style_note(student_id)
    d.intent_guard = _intent_guard(intent)
    d.motivation_line = _motivation_line(student_id, profile, intent)
    return d
