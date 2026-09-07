"""Teaching strategy composition: mode + context -> a full TeachingStrategy.

This is the upgraded successor to student_model/adaptation.adapt(). It takes
the TeachingMode chosen by strategy.select_strategy and the TeachingContext
and produces a complete TeachingStrategy dataclass the Supervisor renders into
its prompt as advisory guidance.

The strategy carries BOTH:
  - the legacy fields (review_first / explanation_depth /
    suggested_quiz_difficulty / misconceptions / recent_mistakes /
    plan_hints / rationale / target_*) so existing V3 callers and tests keep
    working unchanged;
  - the M3 fields (mode / depth / focus / avoid / examples_needed /
    exercise_level / next_check) so the prompt injection can express the
    richer "how to teach" directives the spec calls for.

Per-mode recipes (focus/avoid/depth) are a small table -- the educational
knowledge that distinguishes a tutor from a chatbot lives here. Pure rules,
no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import (BAND_NOVICE, BAND_PROGRESSING, BAND_STRONG, TeachingContext,
                    TeachingOutcome)
from .difficulty import (_assessed_outcomes, compute_difficulty,
                           difficulty_to_level, seed_from_mastery)
from .misconception import correction_focus_avoid
from .strategy import TeachingMode


@dataclass
class NextCheck:
    """A one-question probe to close the teaching turn with."""
    concept: str = ""
    difficulty: int = 1   # 1..5 (M3 difficulty scale; Phase 3 refines this)

    def to_dict(self) -> dict[str, Any]:
        return {"concept": self.concept, "difficulty": self.difficulty}


@dataclass
class TeachingStrategy:
    """The adaptive output fed into the planner/executor as soft guidance.

    Backward-compatible with the V3 TeachingStrategy: all the original fields
    are present with the same names and semantics. The M3 additions (mode /
    depth / focus / avoid / examples_needed / exercise_level / next_check)
    layer on top and default so that an old caller sees no change.
    """
    # --- identity ---
    target_skill_id: str = ""
    target_concept: str = ""
    # --- M3 core output ---
    mode: TeachingMode = TeachingMode.EXPLANATION
    depth: str = "adaptive"          # basic | deep | adaptive
    focus: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    examples_needed: bool = True
    exercise_level: str = "medium"   # easy | medium | hard
    next_check: NextCheck = field(default_factory=NextCheck)
    # --- legacy V3 fields (kept for back-compat with tests + supervisor) ---
    review_first: list[Any] = field(default_factory=list)   # SkillNode-like objs
    explanation_depth: str = "adaptive"   # mirrors `depth`
    explanation_style: str = "balanced"
    suggested_quiz_difficulty: str = "medium"
    misconceptions: list[str] = field(default_factory=list)
    recent_mistakes: list[str] = field(default_factory=list)
    rationale: str = ""
    plan_hints: list[str] = field(default_factory=list)
    # --- W3/D08 additions (LLM teaching-decision adapter) ---
    # assistance: 下一任务的帮助级别（F02 阶梯 full_demo|key_hints|independent；
    # "" = 规则路径未表态）。decision_id: 产生本次调整的教学决策 id（审计）。
    assistance: str = ""
    decision_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_skill_id": self.target_skill_id,
            "target_concept": self.target_concept,
            "mode": self.mode.value,
            "depth": self.depth,
            "focus": list(self.focus),
            "avoid": list(self.avoid),
            "examples_needed": self.examples_needed,
            "exercise_level": self.exercise_level,
            "next_check": self.next_check.to_dict(),
            "review_first": [getattr(n, "name", str(n)) for n in self.review_first],
            "explanation_depth": self.explanation_depth,
            "explanation_style": self.explanation_style,
            "suggested_quiz_difficulty": self.suggested_quiz_difficulty,
            "misconceptions": list(self.misconceptions),
            "recent_mistakes": list(self.recent_mistakes),
            "rationale": self.rationale,
            "plan_hints": list(self.plan_hints),
            "assistance": self.assistance,
            "decision_id": self.decision_id,
        }


# --- per-mode recipes -------------------------------------------------------
# The educational knowledge: each mode carries a focus list (what to emphasize)
# and an avoid list (what to NOT do). These are deliberately concrete and
# short so they translate into crisp prompt directives.
_MODE_RECIPES: dict[TeachingMode, dict[str, list[str] | bool]] = {
    TeachingMode.INTRODUCTION: {
        "focus": ["先讲直觉与生活类比", "用一个最简单的例子切入", "建立概念的整体轮廓"],
        "avoid": ["不要堆砌公式", "不要引入严格定义与边界情形", "不要立刻进入计算"],
        "depth": "basic",
        "examples_needed": True,
    },
    TeachingMode.EXPLANATION: {
        "focus": ["讲清概念定义", "给一道标准例题并分步演示", "给一道变式让学生区分"],
        "avoid": ["不要跳过中间步骤", "不要一次性给太多变式"],
        "depth": "adaptive",
        "examples_needed": True,
    },
    TeachingMode.REMEDIATION: {
        "focus": ["先定位错在哪一步/哪个概念", "重建正确的直觉模型", "针对根因重新讲解"],
        "avoid": ["不要直接重讲全部内容", "不要只给正确答案不解释原因", "不要堆新公式"],
        "depth": "basic",
        "examples_needed": True,
    },
    TeachingMode.PRACTICE: {
        "focus": ["给应用题让学生动手", "暴露常见错误并纠正", "逐步加大难度"],
        "avoid": ["不要重复已掌握的基础讲解"],
        "depth": "adaptive",
        "examples_needed": False,
    },
    TeachingMode.REVIEW: {
        "focus": ["梳理知识结构与关联", "归纳易错点与解题套路", "串联前后知识点"],
        "avoid": ["不要当成第一次讲解"],
        "depth": "adaptive",
        "examples_needed": False,
    },
    TeachingMode.CHALLENGE: {
        "focus": ["给综合题或迁移题", "引导多步推理", "联系其它章节或学科"],
        "avoid": ["不要停留在单点基础题"],
        "depth": "deep",
        "examples_needed": False,
    },
}


def _quiz_level_from_mastery(mastery: float) -> tuple[str, int]:
    """Map mastery -> (exercise_level, numeric difficulty 1..5)."""
    if mastery < BAND_NOVICE:
        return "easy", 1
    if mastery < BAND_PROGRESSING:
        return "easy", 2
    if mastery < BAND_STRONG:
        return "medium", 3
    return "hard", 4


def _depth_for_mode(mode: TeachingMode, style_depth: str) -> str:
    """A fixed style preference wins; otherwise the mode's recipe decides."""
    if style_depth in ("basic", "deep"):
        return style_depth
    return str(_MODE_RECIPES[mode]["depth"])


# --- applied teaching guidance (M7 human-approved proposals) ----------------
# Guidance arrives as open-ended text authored by the advisor LLM and applied
# by a human. compose does NOT interpret the text (no rules, no value domains):
# it routes it into the fields the supervisor already renders (focus/avoid)
# with a light applicability filter, so guidance reaches the prompt through
# the existing channel and revoking it restores byte-identical behavior.

# per-turn budget: the freshest N entries, one focus line + one caution each
_MAX_GUIDANCE_PER_TURN = 2
_GUIDANCE_LINE_CAP = 110   # chars per rendered guidance line


def _guidance_applies(entry, ctx: TeachingContext) -> bool:
    """Deterministic applicability filter over the LLM-authored scope text.

    Empty applicability = general guidance (always applies). Otherwise the
    current subject/concept must appear in the applicability text — a
    conservative containment check that degrades to "not for this context"
    rather than forcing irrelevant guidance into the prompt.
    """
    scope = (getattr(entry, "applicability", "") or "").strip()
    if not scope:
        return True
    hay = scope.lower()
    for tok in (ctx.subject, ctx.concept):
        tok = (tok or "").strip().lower()
        if tok and tok in hay:
            return True
    return False


def _apply_guidance(strat: TeachingStrategy, ctx: TeachingContext,
                    guidance: list) -> None:
    """Fold applied guidance entries into focus/avoid + rationale attribution.

    Guidance lines go to the FRONT of focus/avoid: the supervisor renders only
    the first 3 of each, and human-approved guidance outranks the generic mode
    recipe. rationale records which proposals are in effect (trace/debug).
    """
    entries = [g for g in guidance if _guidance_applies(g, ctx)]
    entries = entries[-_MAX_GUIDANCE_PER_TURN:]
    if not entries:
        return
    focus_lines: list[str] = []
    avoid_lines: list[str] = []
    for e in entries:
        title = (getattr(e, "title", "") or "").strip() or "教学指导"
        principle = (getattr(e, "guidance", "") or "").strip()
        if principle:
            line = f"教学指导「{title}」：{principle}"
            focus_lines.append(line[:_GUIDANCE_LINE_CAP])
        cautions = [str(c).strip() for c in (getattr(e, "cautions", []) or [])
                    if str(c).strip()]
        if cautions:
            avoid_lines.append(cautions[0][:_GUIDANCE_LINE_CAP])
    if focus_lines:
        strat.focus[0:0] = focus_lines
    if avoid_lines:
        strat.avoid[0:0] = avoid_lines
    ids = "、".join("#" + ((getattr(e, "source_proposal", "") or "")
                           or getattr(e, "id", "")) for e in entries)
    strat.rationale = f"已应用教学指导（提案 {ids}）；" + strat.rationale


def compose(ctx: TeachingContext, mode: TeachingMode,
           *, recent_outcomes: list | None = None,
           guidance: list | None = None) -> TeachingStrategy:
    """Build a full TeachingStrategy from the chosen mode + context.

    Pure function of (ctx, mode, recent_outcomes, guidance); deterministic;
    never raises. `guidance` is the list of active applied teaching-guidance
    entries (teaching_engine/guidance_store) — optional so every pre-guidance
    caller/test keeps working unchanged, and empty/None is a no-op.
    """
    recipe = _MODE_RECIPES.get(mode, _MODE_RECIPES[TeachingMode.EXPLANATION])
    focus = list(recipe["focus"])       # type: ignore[arg-type]
    avoid = list(recipe["avoid"])       # type: ignore[arg-type]
    # Grade calibration (INTRODUCTION 按学段分档；此前只有「高中/本科 vs
    # 其它」两档，小学与初中混用默认配方、本科与高中完全同配方）：
    if mode is TeachingMode.INTRODUCTION and ctx.grade == "本科":
        # 本科首次接触：动机先行 + 定义-定理-证明结构，不停留在经验描述
        focus = ["从动机与问题背景切入（为什么需要这个概念）",
                 "给出严格定义、定理叙述与证明思路",
                 "联系已有知识脉络（与前修概念的关系）与工程/科研应用"]
        avoid = ["不要只讲直觉类比而略过严格定义与证明",
                 "不要停留在中学层面的经验性描述",
                 "不要立刻进入综合计算题"]
    elif mode is TeachingMode.INTRODUCTION and ctx.grade == "高中":
        # 高中首次接触：直觉切入但定义与推导必须讲透
        focus = ["先用一个直观例子或生活类比建立直觉",
                 "给出完整定义、公式与推导过程",
                 "建立概念的整体轮廓与适用条件"]
        avoid = ["不要只讲直觉类比而略过严格定义与推导",
                 "不要立刻进入综合计算题"]
    elif mode is TeachingMode.INTRODUCTION and ctx.grade == "小学":
        # 小学专属档：默认配方其实是「低龄通用」，这里显式化并加强——
        # 更小步、每步即练、段末互动提问，防长段抽象灌输。
        focus = ["用生活场景、实物或图画建立直觉",
                 "把一个知识点拆成小步子，每步配一个例子立刻练",
                 "每段讲完提一个小问题请学生回答"]
        avoid = ["不要引入严格定义与公式推导",
                 "不要长段抽象讲解",
                 "不要一次引入多个新名词"]
    strat = TeachingStrategy(
        target_concept=ctx.concept,
        mode=mode,
        focus=focus,
        avoid=avoid,
        examples_needed=bool(recipe["examples_needed"]),
    )

    # depth: style preference overrides, else the mode's recipe
    style_depth = (ctx.learning_style or {}).get("explanation_depth", "adaptive")
    strat.depth = _depth_for_mode(mode, style_depth)
    if mode is TeachingMode.INTRODUCTION and ctx.grade in ("高中", "本科") \
            and strat.depth == "basic":
        # "basic" renders as 减少抽象推导 downstream — wrong signal for an
        # older student's first contact with a rigorous topic.
        strat.depth = "adaptive"
    strat.explanation_depth = strat.depth  # legacy mirror
    strat.explanation_style = (ctx.learning_style or {}).get("preference", "balanced")

    # exercise level + next-check difficulty: dynamic (Phase 3) when recent
    # assessed outcomes exist, else seeded from mastery. The 1..5 internal scale
    # is mapped to easy/medium/hard for the quiz tool (no interface change).
    recent = recent_outcomes or []
    diff = compute_difficulty(ctx.mastery, recent) if recent else seed_from_mastery(ctx.mastery)
    level = difficulty_to_level(diff)
    # Grade floor: 高中/本科的 fresh concept（还没有任何作答证据）不从
    # 课本例题档起步——older students 的 "easy" 几乎必然偏简单，是学生
    # 抱怨「全是最基础题」的来源。一旦有了 assessed outcomes（含连错），
    # 拨盘全权决定，仍可降到 easy。
    if level == "easy" and ctx.grade in ("高中", "本科") \
            and not _assessed_outcomes(recent):
        diff = max(diff, 3)
        level = difficulty_to_level(diff)
    strat.exercise_level = level
    strat.suggested_quiz_difficulty = level   # legacy mirror
    strat.next_check = NextCheck(concept=ctx.concept, difficulty=diff)

    # review_first (legacy field): surface the unmet prereqs for the supervisor
    # to recap inline. Cap to 1 for focus (matches the V3 behavior).
    if ctx.unmet_prereqs and mode in (TeachingMode.REMEDIATION, TeachingMode.INTRODUCTION,
                                      TeachingMode.EXPLANATION):
        strat.review_first = list(ctx.unmet_prereqs)[:1]

    # misconceptions + recent mistakes carried through for correction
    strat.misconceptions = list(ctx.misconceptions)[-2:]
    strat.recent_mistakes = list(ctx.mistakes)[-3:]

    # Phase 2: fold misconception correction recipes into focus/avoid on top of
    # the mode recipe. Each mistake type contributes a targeted focus/avoid pair
    # so the tutor addresses the root cause, not just re-lectures.
    if ctx.mistake_types:
        m_focus, m_avoid, m_approaches = correction_focus_avoid(ctx.mistake_types)
        strat.focus.extend(m_focus)
        strat.avoid.extend(m_avoid)
        if m_approaches:
            strat.plan_hints = [h for h in strat.plan_hints
                                if not h.startswith("教学重点：")]
            strat.plan_hints.insert(0, "针对纠错：" + "；".join(m_approaches) + "。")

    # rationale: one human-readable line explaining the choice, for trace/debug
    strat.rationale = _rationale(ctx, mode)

    # applied teaching guidance folds in last (front of focus/avoid +
    # rationale attribution) so human-approved guidance outranks the generic
    # recipe within the supervisor's focus[:3]/avoid[:3] render budget
    if guidance:
        _apply_guidance(strat, ctx, guidance)

    # plan hints: concrete soft instructions the Supervisor appends to context
    strat.plan_hints = _plan_hints(ctx, strat)
    return strat


def _rationale(ctx: TeachingContext, mode: TeachingMode) -> str:
    if mode == TeachingMode.REMEDIATION:
        if ctx.has_misconception:
            return f"检测到既有误解（{('、'.join(ctx.misconceptions[:1]))}），先纠错根因再继续。"
        names = "、".join(ctx.unmet_prereq_names[:2]) or "前置知识"
        return f"前置知识不足（{names}），先补缺再讲新内容。"
    if mode == TeachingMode.INTRODUCTION:
        return "学生首次接触该知识点，从直觉与类比入手。"
    if mode == TeachingMode.CHALLENGE:
        return "学生已较好掌握，进入综合与迁移训练。"
    if mode == TeachingMode.PRACTICE:
        return "概念已基本建立，进入应用与纠错练习。"
    if mode == TeachingMode.REVIEW:
        return "复习总结，梳理结构与易错点。"
    if ctx.previous_mode and ctx.previous_outcome == TeachingOutcome.CORRECT:
        return "上轮学生答对，本轮回升到下一阶段。"
    return "按学生当前水平自适应讲解。"


def _plan_hints(ctx: TeachingContext, strat: TeachingStrategy) -> list[str]:
    hints: list[str] = []
    if strat.review_first:
        names = "、".join(getattr(n, "name", str(n)) for n in strat.review_first)
        hints.append(
            f"在讲解「{ctx.concept or ctx.subject}」之前，先用一两句话回顾前置知识"
            f"「{names}」，确认学生跟得上再继续。"
        )
    # focus/avoid rendered as direct imperatives
    if strat.focus:
        hints.append("教学重点：" + "；".join(strat.focus[:3]) + "。")
    if strat.avoid:
        hints.append("需要避免：" + "；".join(strat.avoid[:3]) + "。")
    if strat.misconceptions:
        hints.append("注意纠正这些已有误解：" + "；".join(strat.misconceptions) + "。")
    if strat.recent_mistakes:
        hints.append("该生近期在这些点上出错：" + "；".join(strat.recent_mistakes[-2:]) + "。")
    if strat.examples_needed:
        hints.append("请给出至少一个具体例子帮助理解。")
    if strat.next_check.concept:
        hints.append(
            f"讲解结束后，出一道难度{strat.next_check.difficulty}的"
            f"「{strat.next_check.concept}」检测题确认掌握。"
        )
    return hints
