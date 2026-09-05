"""Answer evaluation: deterministic MC grading + three-level open grading.

This is where the student's learning gets measured. Two paths, deliberately
different in cost and certainty:

MC (multiple_choice) is DETERMINISTIC: a letter compare, zero LLM. This is
the fix for the documented gap (DESIGN section 14.14): MC was graded
client-side by the frontend and never reported back, so mastery tracking only
covered fill-in/short-answer. Now every question type feeds the loop.

Open (fill_blank / short_answer) is LLM-graded on a THREE-level scale
([correct] / [partial] / [wrong]) instead of binary. [partial] is the new
signal -- "right idea, missed a step" -- that the Teaching Engine's
REMEDIATION mode can target and CAT uses to step difficulty.

Consolidation rule (load-bearing): the mistake-type classification is REUSED
from teaching_engine.misconception.diagnose. One classifier in the system, not
two. One-way dependency: assessment -> teaching_engine.misconception.

All pure functions here: the LLM call lives in the API layer (it streams
deltas); this module supplies the prompt and parses the verdict.
"""
from __future__ import annotations

from .question import Question
from .state import (AssessmentContext, AssessmentResult, ScoreLevel,
                    STATUS_MASTERED, STATUS_MISCONCEPTION, STATUS_PARTIAL,
                    STATUS_UNKNOWN, VERDICT_CORRECT, VERDICT_PARTIAL,
                    VERDICT_UNKNOWN, VERDICT_WRONG)

_MET = 0.6  # mastery threshold for promoting a concept to "mastered"

# Open-answer grade prompt. Upgrades the original binary scale to three levels;
# [partial] is the qualitatively new signal a tutor needs (right direction,
# incomplete execution).
# W3/D04: 文本迁入 prompts/registry（assessment_grade@1.0.0，文本未改），
# 此处薄 re-export 兼容旧引用。
from ...prompts.registry import get as _prompt  # noqa: E402

_GRADE_PROMPT = _prompt("assessment_grade").text


def grade_open_prompt(*, stem: str, q_type: str, correct_answer: str,
                      explanation: str, student_answer: str,
                      grade: str = "") -> str:
    """Build the three-level open-answer grading prompt.

    P1: ``grade`` 默认空（自动）——批改时不预置学段典型错因，改用通用关注点；
    显式学段仍注入该学段典型错因（改造前行为）。
    """
    from ..teaching_engine.stage_profile import is_auto, normalize_grade, stage_profile
    grade = normalize_grade(grade)
    if is_auto(grade):
        mistakes = "概念理解、符号/单位、步骤遗漏、计算粗心、条件误用等常见错误"
        grade_phrase = "学生未指定学段，按知识点本身批改"
    else:
        mistakes = stage_profile(grade)["mistakes"]
        grade_phrase = f"学段「{grade}」"
    return _GRADE_PROMPT.format(
        grade=grade_phrase, stem=stem, q_type=q_type,
        correct_answer=correct_answer, explanation=explanation,
        student_answer=student_answer,
        mistakes=mistakes,
    )


def verdict_for_score(score: float) -> str:
    """Map a 0..1 score onto the backward-compatible verdict string."""
    if score >= 0.75:
        return VERDICT_CORRECT
    if score >= 0.25:
        return VERDICT_PARTIAL
    return VERDICT_WRONG


def _diagnose_mistake(note: str, *, concept: str = "", subject: str = "") -> str:
    """Reuse the Teaching Engine's misconception classifier (consolidation).

    Returns the MistakeType value string or "" when unclassified. Never raises.
    """
    if not note:
        return ""
    try:
        from ..teaching_engine import diagnose
        mtype = diagnose(note, concept=concept, subject=subject)
        return mtype.value if mtype is not None else ""
    except Exception:
        return ""


def evaluate_mc(question: Question, student_answer: str) -> AssessmentResult:
    """Grade a multiple-choice answer deterministically (zero LLM).

    MC has a single canonical letter answer, so a string compare is both exact
    and free. This is the path that closes the section-14.14 gap.
    """
    ans = (student_answer or "").strip().upper()
    correct = (question.answer or "").strip().upper()
    is_right = bool(ans) and ans == correct
    score = 1.0 if is_right else 0.0
    result = AssessmentResult(
        question_id=question.id,
        concept=question.concept,
        verdict=VERDICT_CORRECT if is_right else VERDICT_WRONG,
        score=score,
        feedback="",
        difficulty_at=question.difficulty,
    )
    if not is_right:
        note = f"选择题答错，选了 {ans or '空'}，正确为 {correct}"
        result.diagnosis_note = note[:60]
        result.mistake_type = _diagnose_mistake(note, concept=question.concept)
    return result


def parse_grade(raw: str, *, question: "Question | None" = None,
                concept: str = "", subject: str = "",
                ctx: "AssessmentContext | None" = None) -> AssessmentResult:
    """Parse the LLM's three-level grading output into an AssessmentResult.

    Reads the leading verdict token, maps to score, derives concept_status, and
    runs the (reused) misconception classifier on the feedback body when the
    answer is not fully correct. Never raises; unparseable -> UNKNOWN result.
    """
    full = (raw or "").lstrip()
    if full.startswith("[对]"):
        score = ScoreLevel.FULL.score
        body = full[3:].lstrip()
    elif full.startswith("[错]"):
        score = ScoreLevel.NONE.score
        body = full[3:].lstrip()
    elif full.startswith("[部分对]"):
        score = ScoreLevel.PARTIAL.score
        body = full[5:].lstrip()
    else:
        return AssessmentResult(
            concept=concept or (question.concept if question else ""),
            verdict=VERDICT_UNKNOWN, score=0.0,
            feedback=full[:300],
            difficulty_at=question.difficulty if question else 0,
        )
    verdict = verdict_for_score(score)
    q_concept = concept or (question.concept if question else "")
    difficulty_at = question.difficulty if question else (ctx.base_difficulty if ctx else 0)
    result = AssessmentResult(
        question_id=question.id if question else "",
        concept=q_concept,
        verdict=verdict,
        score=score,
        feedback=body[:300],
        difficulty_at=difficulty_at,
    )
    mastery = ctx.current_mastery if ctx else 0.0
    if score < ScoreLevel.FULL.score and body:
        result.diagnosis_note = body[:60]
        result.mistake_type = _diagnose_mistake(body, concept=q_concept, subject=subject)
    result.concept_status = derive_concept_status(score, mastery=mastery,
                                                   mistake_type=result.mistake_type)
    return result


def derive_concept_status(score: float, *, mastery: float = 0.0,
                           mistake_type: str = "") -> str:
    """Fold (answer score, current mastery, mistake type) into a concept label.

    A single correct answer does not mean "mastered" -- we require the
    underlying mastery to already be solid (>= _MET). A wrong answer with a
    diagnosed concept error flags a misconception. Otherwise partial/unknown.
    """
    if score >= ScoreLevel.FULL.score:
        return STATUS_MASTERED if mastery >= _MET else STATUS_PARTIAL
    if score <= ScoreLevel.NONE.score:
        if mistake_type == "concept":
            return STATUS_MISCONCEPTION
        return STATUS_PARTIAL if mastery >= _MET else STATUS_UNKNOWN
    return STATUS_PARTIAL
