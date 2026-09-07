"""Misconception diagnosis: classify a student error and pick a correction.

This is the single biggest behavioural difference between a tutor and a
chatbot. A chatbot reacts to "wrong" by re-explaining the whole topic. A tutor
first asks "WHAT kind of wrong?" -- a concept confusion, a skipped step, an
arithmetic slip, or a reasoning gap -- and corrects the root cause.

Design (mirrors the rest of the engine):
  - PURE: diagnose() is a pure function (note, context) -> MistakeType | None.
    No I/O, no LLM, no side effects. It runs inside student_model's event
    processor (which owns persistence) and inside policy.compose (which folds
    the correction recipe into focus/avoid). The direction is one-way:
    student_model imports teaching_engine.misconception to persist a diagnosis;
    teaching_engine never imports student_model.
  - RULE-BASED: keyword patterns, ordered most-specific first. Deliberately
    not an LLM classifier -- the same stability/testability reasoning as the
    skill graph (§14.5). Returns None when nothing matches, so the caller
    keeps the previous behaviour instead of forcing a category.
  - CORRECTION RECIPES: each type carries a focus/avoid pair that policy
    composes ON TOP of the mode recipe (so REMEDIATION + ConceptError yields
    both "rebuild the intuition model" and "don't pile on formulas").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MistakeType(str, Enum):
    """The four canonical error categories for K-12 / undergrad STEM.

    str-Enum so it serializes to JSON cleanly and slots into ConceptRecord.
    """
    CONCEPT = "concept"            # 概念错误:混淆/误解了概念本身
    PROCEDURE = "procedure"        # 步骤错误:漏步/乱序/流程错
    CALCULATION = "calculation"    # 计算错误:算术/符号/单位
    REASONING = "reasoning"        # 推理错误:方向/判断/条件分析

    @classmethod
    def from_value(cls, v: Any) -> "MistakeType | None":
        if v is None or v == "":
            return None
        if isinstance(v, MistakeType):
            return v
        try:
            return cls(str(v))
        except ValueError:
            return None


@dataclass
class CorrectionRecipe:
    """How to correct one mistake type: emphasis + what NOT to do."""
    focus: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    approach: str = ""   # one-line teaching approach, for rationale/debug


# Per-type correction recipes. These are the pedagogical knowledge that makes
# the tutor target the root cause instead of re-lecturing.
_RECIPES: dict[MistakeType, CorrectionRecipe] = {
    MistakeType.CONCEPT: CorrectionRecipe(
        focus=["重建正确的概念模型与直觉", "对比正确与错误理解的差异"],
        avoid=["不要直接堆公式", "不要只重述定义"],
        approach="定位概念混淆点，重建直觉模型"),
    MistakeType.PROCEDURE: CorrectionRecipe(
        focus=["拆解完整步骤、逐步演示", "标出容易遗漏的中间环节"],
        avoid=["不要跳步", "不要只给最终步骤不给过程"],
        approach="补全漏掉的步骤、理顺顺序"),
    MistakeType.CALCULATION: CorrectionRecipe(
        focus=["检查计算细节：符号、单位、进位", "重算关键一步"],
        avoid=["不要只讲思路而跳过算术", "不要忽略正负号与单位"],
        approach="定位计算出错处，规范运算习惯"),
    MistakeType.REASONING: CorrectionRecipe(
        focus=["梳理推理链条", "用关系图/受力图等可视化辅助"],
        avoid=["不要只给结论不给推理", "不要忽略隐含条件与边界情形"],
        approach="重建推理路径，补全被跳过的判断"),
}


# Keyword rules. ORDER MATTERS: first match wins. Conceptual confusion is the
# most fundamental and most mis-tagged, so its signals are checked first;
# reasoning next (it often masquerades as concept); then procedure; calculation
# last (it is the most surface-level and its keywords like "算" are noisy).
_RULES: list[tuple[tuple[str, ...], MistakeType]] = [
    # --- Concept: confusion / mis-definition / wrong mental model ---
    (("混淆", "搞混", "搞错概念", "误认为", "误以为", "误把", "误将",
      "当成", "当作", "理解为", "以为是", "错认为",
      "概念错误", "概念不清", "概念混淆", "定义错", "本质理解"),
     MistakeType.CONCEPT),
    # --- Reasoning: direction / condition / analysis / logic gaps ---
    (("方向错", "方向判断", "判断错", "分析错", "推理错", "逻辑错",
      "条件漏", "隐含条件", "漏掉情形", "未考虑", "没考虑",
      "受力分析错", "情形漏", "关系搞反"),
     MistakeType.REASONING),
    # --- Procedure: missed / mis-ordered steps ---
    (("漏步骤", "漏掉步骤", "漏了", "少一步", "少写", "跳步", "跳过步骤",
      "步骤错", "步骤漏", "顺序错", "流程错", "忘了", "忘记", "未完成",
      "不完整"),
     MistakeType.PROCEDURE),
    # --- Calculation: arithmetic / sign / unit slips ---
    (("算错", "算成", "计算错", "计算错误", "算反", "正负错", "正负号错",
      "符号错", "单位错", "漏单位", "进位错", "约分错", "运算错",
      "粗心", "笔误"),
     MistakeType.CALCULATION),
]


def diagnose(note: str, *, concept: str = "", subject: str = "") -> MistakeType | None:
    """Classify one error note into a MistakeType, or None if unclassified.

    Pure keyword scan, ordered most-specific first. `concept`/`subject` are
    accepted for future subject-aware rules but do not change the result today.
    Never raises.
    """
    note = (note or "").strip()
    if not note:
        return None
    for keywords, mtype in _RULES:
        if any(kw in note for kw in keywords):
            return mtype
    return None


def recipe_for(mtype: "MistakeType | None") -> CorrectionRecipe | None:
    """Return the correction recipe for a type, or None if mtype is None."""
    if mtype is None:
        return None
    return _RECIPES.get(mtype)


def correction_focus_avoid(mtypes: list) -> tuple[list[str], list[str], list[str]]:
    """Fold a list of mistake types into (focus[], avoid[], approaches[]) for
    policy.compose. Dedupes; preserves first-seen order. Empty lists when no
    type is recognised. Never raises.
    """
    focus: list[str] = []
    avoid: list[str] = []
    approaches: list[str] = []
    seen_focus: set[str] = set()
    seen_avoid: set[str] = set()
    for mt in mtypes:
        if isinstance(mt, MistakeType):
            t = mt
        else:
            t = MistakeType.from_value(mt)
        rec = recipe_for(t)
        if rec is None:
            continue
        for f in rec.focus:
            if f not in seen_focus:
                seen_focus.add(f)
                focus.append(f)
        for a in rec.avoid:
            if a not in seen_avoid:
                seen_avoid.add(a)
                avoid.append(a)
        if rec.approach and rec.approach not in approaches:
            approaches.append(rec.approach)
    return focus, avoid, approaches
