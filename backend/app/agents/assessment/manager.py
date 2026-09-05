"""AssessmentManager: the single entry point for measurement intelligence.

Like student_model.manager.StudentModel (for "what the student knows") and
teaching_engine.manager.TeachingManager (for "how to teach"), this is the
facade for "did the student learn it". Surface:

    am = get_assessment_manager()
    q   = await am.create_check(goal, ctx, llm=...)        # Phase 2 generator
    res = await am.evaluate_and_record(question, answer, ctx, llm=...)  # close loop
    sess, q = await am.start_adaptive_test(goal, ctx, llm=...)  # Phase 3 CAT

evaluate_and_record is the SINGLE closed-loop point: it grades (MC
deterministically, open via LLM), derives concept_status, classifies the
misconception (reusing teaching_engine), and writes the result back to the
Student Model via the EXISTING record_quiz_result facade. There is no second
event bus and no second mastery updater -- assessment folds into M2's loop.

Consolidation contract (load-bearing): this module imports the
record_quiz_result facade lazily inside the method (not at module scope), the
same way teaching_engine's misconception import works. The Student Model is
written to only through its public facade; assessment owns no student state.

Graceful: any failure degrades to a no-op result; never breaks a turn.
Toggled by ASSESSMENT_ENGINE_MODE (default on).
"""
from __future__ import annotations

import os
from typing import Any

from ...core.llm_async import AsyncLLMClient
from .evaluator import (derive_concept_status, evaluate_mc, grade_open_prompt,
                        parse_grade)
from .question import Question, QuestionType
from .state import (AssessmentContext, AssessmentGoal, AssessmentResult,
                    ScoreLevel)
from .adaptive_test import (AssessmentSession, next_difficulty, should_stop,
                             summary as cat_summary)
from . import session_store


def is_enabled() -> bool:
    """Whether the assessment engine is active (default on)."""
    return os.getenv("ASSESSMENT_ENGINE_MODE", "1") not in ("0", "false", "False", "off")


class AssessmentManager:
    """Stateless facade over the assessment pipeline.

    Holds no per-student state; the Student Model owns all student state and
    assessment writes to it only through record_quiz_result. A single shared
    instance is cached per process.
    """

    async def create_check(self, goal: AssessmentGoal, ctx: AssessmentContext,
                           *, llm: AsyncLLMClient) -> Question | None:
        """Generate a single targeted check question (Phase 2)."""
        if not is_enabled():
            return None
        try:
            from .generator import generate_question
            return await generate_question(goal, ctx, llm=llm)
        except Exception:
            return None

    async def evaluate_and_record(self, question: Question,
                                  student_answer: str,
                                  ctx: AssessmentContext,
                                  *, llm: AsyncLLMClient | None = None,
                                  raw_grade: str | None = None,
                                  student_id: str = "",
                                  is_variant: bool = False) -> AssessmentResult:
        """The single closed-loop point: grade, classify, write back.

        ``is_variant`` marks same-family variant tasks (fit_quiz): their
        evidence enters the gate at VARIANT_TASK level instead of masquerading
        as an independent observation (updatePlan.md A05 minimal wiring)."""
        try:
            grading_confidence = 0.0
            grading_source = "assessment_unknown"
            # Branch on the declared type, not on options presence: letter
            # grading is deterministic and does not need the option bodies,
            # and /quiz/record callers may not carry them (a missing-options
            # MC question must not silently degrade to verdict="unknown").
            if question.q_type == QuestionType.MULTIPLE_CHOICE:
                result = evaluate_mc(question, student_answer)
                grading_confidence = 1.0
                grading_source = "assessment_multiple_choice"
            elif raw_grade is not None:
                result = parse_grade(raw_grade, question=question,
                                     concept=ctx.concept, subject=ctx.subject,
                                     ctx=ctx)
                grading_confidence = 0.85
                grading_source = "assessment_structured_grade"
            elif llm is not None:
                result = await self._grade_open_llm(question, student_answer, ctx, llm)
                grading_confidence = 0.75
                grading_source = "assessment_llm_grade"
            else:
                return AssessmentResult(question_id=question.id, concept=question.concept)
            if not result.skill_id and ctx.skill_id:
                result.skill_id = ctx.skill_id
            # M5.8: anchor the concept to a knowledge-graph node id, so the
            # result names the exact node the Student Model will credit. The
            # SkillGraph is the BKT keyspace and already mirrors the M5
            # ontology (strict bar: exact/alias/substring only, so an
            # off-syllabus concept is not mis-attributed).
            if not result.skill_id:
                try:
                    from ..student_model import (get_student_model,
                                                 is_enabled as _sm_on)
                    from ..student_model.store import DEFAULT_STUDENT_ID
                    if _sm_on():
                        sg = get_student_model(
                            student_id or DEFAULT_STUDENT_ID).load().graph
                        node = sg.match_concept(
                            result.concept or question.concept or ctx.concept,
                            threshold=0.6)
                        if node is not None:
                            result.skill_id = node.id
                except Exception:
                    pass
            if not result.concept:
                result.concept = ctx.concept or question.concept
        except Exception:
            return AssessmentResult(question_id=question.id,
                                     concept=question.concept or ctx.concept)
        self._record(
            result, student_id=student_id, student_answer=student_answer,
            grading_confidence=grading_confidence, grading_source=grading_source,
            is_variant=is_variant,
        )
        return result

    async def _grade_open_llm(self, question: Question, student_answer: str,
                              ctx: AssessmentContext,
                              llm: AsyncLLMClient) -> AssessmentResult:
        """Grade an open answer by calling the LLM (non-streaming)."""
        prompt = grade_open_prompt(
            stem=question.stem, q_type=question.q_type,
            correct_answer=question.answer, explanation=question.explanation,
            student_answer=student_answer, grade=ctx.grade,
        )
        content, _ = await llm.complete(
            [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=400,
            disable_thinking=True,
        )
        return parse_grade(content, question=question, concept=ctx.concept,
                           subject=ctx.subject, ctx=ctx)

    def _record(self, result: AssessmentResult, *, student_id: str = "",
                student_answer: str = "", grading_confidence: float = 0.0,
                grading_source: str = "assessment",
                is_variant: bool = False) -> None:
        """Write the result to the Student Model via its public facade.

        Lazy import keeps the module-scope import graph clean. Maps the
        three-level score to the binary observation the existing event path
        expects. Never raises.
        """
        if not is_enabled():
            return
        try:
            from ..skill_runtime import (assessment_evidence,
                                         evaluate_learning_evidence)
            evidence = assessment_evidence(
                learning_skill_id=result.skill_id or result.concept or "",
                verdict=result.verdict, student_answer=student_answer,
                question_id=result.question_id, source=grading_source,
                grading_confidence=grading_confidence,
                is_variant=is_variant,
            )
            gate = evaluate_learning_evidence(evidence)
            result.evidence_level = evidence.level.name
            result.evidence_gate = gate.to_dict()
            if not gate.allow_mastery_update:
                return
            from ..student_model import record_quiz_result
            record_quiz_result(
                concept=result.concept or "",
                correct=result.correct,
                skill_id=result.skill_id,
                knowledge_point=result.concept or "",
                subject="",
                note=(result.diagnosis_note if not result.correct else ""),
                student_id=student_id,
            )
        except Exception:
            pass

    # --- Phase 3: Computerized Adaptive Test -----------------------------

    def get_active_session(self, student_id: str) -> AssessmentSession | None:
        """Load the student's active CAT session, or None."""
        data = session_store.load_session(student_id)
        if not data:
            return None
        return _session_from_dict(data)

    async def start_adaptive_test(self, goal: AssessmentGoal, ctx: AssessmentContext,
                                  *, llm: AsyncLLMClient,
                                  student_id: str = "") -> tuple[AssessmentSession, Question | None]:
        """Begin a CAT: seed difficulty, generate the first question, persist.

        Returns (session, first_question). first_question is None if generation
        failed. The seed difficulty comes from ctx.base_difficulty (filled by
        the caller from teaching_engine.seed_from_mastery)."""
        diff = max(1, min(5, int(goal.difficulty or ctx.base_difficulty or 2)))
        session = AssessmentSession(
            student_id=student_id, goal=goal, ctx=ctx,
            current_difficulty=diff, status="active")
        ctx.base_difficulty = diff
        q = await self._gen_for_session(session, llm)
        if q is not None:
            session.questions.append(q)
            try:
                from ...core.learning_records import record_question
                record_question(student_id, f"assessment:{student_id}", q.to_dict(),
                                topic=goal.concept, subject=ctx.subject,
                                grade=ctx.grade, source_kind="assessment")
            except Exception:
                pass
        session_store.save_session(student_id, session.to_dict())
        return session, q

    async def next_question(self, student_id: str, *,
                            llm: AsyncLLMClient) -> tuple[AssessmentSession | None, Question | None, str]:
        """Advance a CAT after the current answer was graded. Returns
        (session, next_question_or_None, stop_reason). stop_reason is "" when a
        next question was produced; otherwise the session is finalized and the
        caller should render summary()."""
        session = self.get_active_session(student_id)
        if session is None:
            return None, None, "no_active_session"
        reason = should_stop(session)
        if reason:
            session.status = ("mastered" if reason == "mastered" else "stopped")
            session.stop_reason = reason
            session_store.save_session(student_id, session.to_dict())
            return session, None, reason
        session.current_difficulty = next_difficulty(session)
        q = await self._gen_for_session(session, llm)
        if q is not None:
            session.questions.append(q)
            try:
                from ...core.learning_records import record_question
                record_question(student_id, f"assessment:{student_id}", q.to_dict(),
                                topic=session.goal.concept,
                                subject=session.ctx.subject,
                                grade=session.ctx.grade, source_kind="assessment")
            except Exception:
                pass
        session_store.save_session(student_id, session.to_dict())
        return session, q, ""

    async def record_cat_answer(self, student_id: str, *,
                                answer: str, raw_grade: str | None = None,
                                llm: AsyncLLMClient | None = None) -> AssessmentResult | None:
        """Grade the current (last) question of an active CAT and append the
        result. Uses evaluate_and_record so the mastery loop stays single."""
        session = self.get_active_session(student_id)
        if session is None or not session.questions:
            return None
        q = session.questions[-1]
        if len(session.results) >= len(session.questions):
            return None  # current question already graded
        result = await self.evaluate_and_record(
            q, answer, session.ctx, llm=llm, raw_grade=raw_grade,
            student_id=student_id)
        session.results.append(result)
        try:
            from ...core.learning_records import record_verdict
            record_verdict(student_id, f"assessment:{student_id}", stem=q.stem,
                           verdict=result.verdict, student_answer=answer,
                           score=result.score,
                           concept=result.concept or q.concept or session.ctx.concept,
                           subject=session.ctx.subject, source_kind="assessment")
        except Exception:
            pass
        session_store.save_session(student_id, session.to_dict())
        return result

    def cat_report(self, student_id: str) -> dict[str, Any] | None:
        """Compact summary of the active/finished CAT for rendering."""
        session = self.get_active_session(student_id)
        if session is None:
            return None
        return cat_summary(session)

    def abandon_session(self, student_id: str) -> None:
        """End a CAT without a verdict (user navigated away)."""
        session_store.clear_session(student_id)

    async def _gen_for_session(self, session: AssessmentSession,
                               llm: AsyncLLMClient) -> Question | None:
        """Generate one question at the session's current difficulty."""
        from .generator import generate_question
        goal = AssessmentGoal(
            concept=session.goal.concept or session.ctx.concept,
            purpose="adaptive", difficulty=session.current_difficulty,
            count=session.goal.count, q_type=session.goal.q_type,
            assesses=list(session.goal.assesses),
            forbidden=list(session.goal.forbidden),
            bloom_focus=session.goal.bloom_focus)
        session.ctx.base_difficulty = session.current_difficulty
        return await generate_question(goal, session.ctx, llm=llm,
                                       student_id=session.student_id)


def _session_from_dict(data: dict[str, Any]) -> AssessmentSession:
    """Rebuild an AssessmentSession from its persisted dict. Defensive."""
    try:
        g = data.get("goal")
        return AssessmentSession(
            session_id=str(data.get("session_id", "") or ""),
            student_id=str(data.get("student_id", "") or ""),
            goal=AssessmentGoal(**g) if isinstance(g, dict) else AssessmentGoal(),
            ctx=AssessmentContext.from_dict(data.get("ctx") or {}),
            questions=[Question.from_quiz_dict(q) for q in (data.get("questions") or [])
                       if isinstance(q, dict)],
            results=[AssessmentResult.from_dict(r) for r in (data.get("results") or [])
                     if isinstance(r, dict)],
            current_difficulty=int(data.get("current_difficulty", 2)),
            status=str(data.get("status", "active") or "active"),
            stop_reason=str(data.get("stop_reason", "") or ""),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)))
    except Exception:
        return AssessmentSession()


_INSTANCE: AssessmentManager | None = None


def get_assessment_manager() -> AssessmentManager:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AssessmentManager()
    return _INSTANCE


async def evaluate_and_record(question: Question, student_answer: str,
                              ctx: AssessmentContext, *,
                              llm: AsyncLLMClient | None = None,
                              raw_grade: str | None = None,
                              student_id: str = "") -> AssessmentResult:
    """Top-level convenience: grade one answer and close the mastery loop."""
    return await get_assessment_manager().evaluate_and_record(
        question, student_answer, ctx, llm=llm, raw_grade=raw_grade,
        student_id=student_id)
