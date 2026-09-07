"""Unit tests for the Assessment Intelligence module (M4 Phase 1).

Covers: Question model + from_quiz_dict lifting, deterministic MC grading,
three-level open-answer parsing ([对]/[部分对]/[错]), concept_status folding
(answer score x mastery x mistake type), and the consolidation invariant
(the reused teaching_engine.misconception classifier produces real types).
All pure functions, no LLM.
"""
import os
import unittest

from app.agents.assessment import (AssessmentContext, AssessmentGoal,
                                    AssessmentResult, Question, QuestionType,
                                    ScoreLevel, derive_concept_status,
                                    evaluate_mc, grade_open_prompt,
                                    parse_grade, verdict_for_score)
from app.agents.assessment.state import (STATUS_MASTERED, STATUS_MISCONCEPTION,
                                          STATUS_PARTIAL, STATUS_UNKNOWN,
                                          VERDICT_CORRECT, VERDICT_PARTIAL,
                                          VERDICT_WRONG)


class TestQuestion(unittest.TestCase):
    def test_from_quiz_dict_lifts_legacy_fields(self):
        d = {"id": 2, "type": "multiple_choice",
             "stem": "下列哪个是顶点？",
             "options": {"A": "(0,0)", "B": "(1,1)"},
             "answer": "A", "explanation": "顶点在原点",
             "knowledge_point": "二次函数·顶点", "difficulty": "hard"}
        q = Question.from_quiz_dict(d, concept="二次函数")
        self.assertEqual(q.id, "2")
        self.assertEqual(q.concept, "二次函数")
        self.assertEqual(q.q_type, QuestionType.MULTIPLE_CHOICE)
        self.assertTrue(q.is_multiple_choice)
        self.assertEqual(q.difficulty, 5)        # hard -> 5
        self.assertIn("顶点", q.knowledge_points)

    def test_from_quiz_dict_easy_medium_to_difficulty(self):
        self.assertEqual(Question.from_quiz_dict({"difficulty": "easy"}).difficulty, 2)
        self.assertEqual(Question.from_quiz_dict({"difficulty": "medium"}).difficulty, 3)
        self.assertEqual(Question.from_quiz_dict({"difficulty": "hard"}).difficulty, 5)

    def test_from_quiz_dict_numeric_difficulty(self):
        self.assertEqual(Question.from_quiz_dict({"difficulty": 4}).difficulty, 4)
        # clamped
        self.assertEqual(Question.from_quiz_dict({"difficulty": 99}).difficulty, 5)

    def test_from_quiz_dict_split_knowledge_point(self):
        q = Question.from_quiz_dict({"knowledge_point": "浮力、密度、压强"})
        self.assertEqual(q.knowledge_points, ["浮力", "密度", "压强"])

    def test_is_multiple_choice_requires_options(self):
        q = Question(q_type=QuestionType.MULTIPLE_CHOICE)
        self.assertFalse(q.is_multiple_choice)
        q.options = {"A": "x"}
        self.assertTrue(q.is_multiple_choice)


class TestEvaluateMC(unittest.TestCase):
    def _q(self, answer="B"):
        return Question(concept="惯性", q_type=QuestionType.MULTIPLE_CHOICE,
                        stem="s", options={"A": "a", "B": "b"}, answer=answer,
                        difficulty=3)

    def test_correct_mc(self):
        r = evaluate_mc(self._q(), "B")
        self.assertEqual(r.verdict, VERDICT_CORRECT)
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.correct)

    def test_wrong_mc(self):
        r = evaluate_mc(self._q(), "A")
        self.assertEqual(r.verdict, VERDICT_WRONG)
        self.assertEqual(r.score, 0.0)
        self.assertFalse(r.correct)
        self.assertTrue(r.diagnosis_note)  # a note is crafted for diagnosis

    def test_mc_is_case_insensitive_and_trims(self):
        self.assertEqual(evaluate_mc(self._q(), " b ").verdict, VERDICT_CORRECT)

    def test_mc_empty_answer_is_wrong(self):
        r = evaluate_mc(self._q(), "")
        self.assertEqual(r.verdict, VERDICT_WRONG)


class TestParseGrade(unittest.TestCase):
    def _ctx(self, mastery=0.0):
        return AssessmentContext(concept="惯性", current_mastery=mastery)

    def test_parse_correct(self):
        r = parse_grade("[对] 思路正确，抓住了质量不变。\n后续注意单位。",
                        concept="惯性", ctx=self._ctx(0.7))
        self.assertEqual(r.score, 1.0)
        self.assertEqual(r.verdict, VERDICT_CORRECT)
        self.assertEqual(r.concept_status, STATUS_MASTERED)  # mastery>=0.6

    def test_parse_wrong_concept_error_flags_misconception(self):
        body = "混淆了速度与惯性，误认为速度越大惯性越大，这是概念错误。"
        r = parse_grade("[错] " + body, concept="惯性", ctx=self._ctx(0.2))
        self.assertEqual(r.score, 0.0)
        self.assertEqual(r.verdict, VERDICT_WRONG)
        # the reused classifier should tag this a concept error
        self.assertEqual(r.mistake_type, "concept")
        self.assertEqual(r.concept_status, STATUS_MISCONCEPTION)

    def test_parse_partial(self):
        r = parse_grade("[部分对] 方向对但缺了代入步骤。\n补上数据即可。",
                        concept="浮力", ctx=self._ctx(0.5))
        self.assertEqual(r.score, 0.5)
        self.assertEqual(r.verdict, VERDICT_PARTIAL)
        self.assertEqual(r.concept_status, STATUS_PARTIAL)

    def test_parse_unparseable_is_unknown(self):
        r = parse_grade("模型没有按格式输出", concept="x", ctx=self._ctx())
        self.assertEqual(r.verdict, "unknown")
        self.assertEqual(r.score, 0.0)

    def test_correct_on_low_mastery_is_partial_not_mastered(self):
        # one right answer on a brand-new concept does not overclaim mastered
        r = parse_grade("[对] 全对。", concept="x", ctx=self._ctx(0.1))
        self.assertEqual(r.concept_status, STATUS_PARTIAL)


class TestDeriveConceptStatus(unittest.TestCase):
    def test_full_with_high_mastery_mastered(self):
        self.assertEqual(derive_concept_status(1.0, mastery=0.8), STATUS_MASTERED)

    def test_full_with_low_mastery_partial(self):
        self.assertEqual(derive_concept_status(1.0, mastery=0.3), STATUS_PARTIAL)

    def test_none_concept_error_misconception(self):
        self.assertEqual(derive_concept_status(0.0, mastery=0.2,
                         mistake_type="concept"), STATUS_MISCONCEPTION)

    def test_none_no_type_low_mastery_unknown(self):
        self.assertEqual(derive_concept_status(0.0, mastery=0.2), STATUS_UNKNOWN)

    def test_partial_always_partial(self):
        self.assertEqual(derive_concept_status(0.5, mastery=0.9), STATUS_PARTIAL)


class TestVerdictForScore(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(verdict_for_score(1.0), VERDICT_CORRECT)
        self.assertEqual(verdict_for_score(0.5), VERDICT_PARTIAL)
        self.assertEqual(verdict_for_score(0.0), VERDICT_WRONG)


class TestGradePrompt(unittest.TestCase):
    def test_prompt_is_three_level(self):
        p = grade_open_prompt(stem="s", q_type="short_answer",
                              correct_answer="a", explanation="e",
                              student_answer="sa", grade="高中")
        self.assertIn("[对]", p)
        self.assertIn("[部分对]", p)
        self.assertIn("[错]", p)
        self.assertIn("题目：s", p)  # the prompt is returned already formatted


class TestConsolidation(unittest.TestCase):
    """The mistake-type classifier is REUSED from teaching_engine, not rebuilt."""
    def test_real_concept_classification(self):
        from app.agents.teaching_engine import diagnose
        from app.agents.assessment.generator import (generate_question,
                                                     _constraint_block,
                                                     _difficulty_label,
                                                     _parse, _pick_q_type)
        # the assessment parse path should produce the same type diagnose() does
        note = "误认为速度越大惯性越大，概念混淆"
        self.assertEqual(diagnose(note).value, "concept")

    def test_no_second_classifier_in_assessment(self):
        # assessment.evaluator must not define its own keyword table
        import app.agents.assessment.evaluator as ev
        self.assertFalse(hasattr(ev, "_RULES"),
                        "assessment must reuse teaching_engine.misconception, "
                        "not ship a second classifier")


class TestGenerator(unittest.TestCase):
    """Phase 2: constraint-driven generation helpers (pure functions)."""
    def test_difficulty_label_maps_to_quiz_triple(self):
        from app.agents.assessment.generator import _difficulty_label
        self.assertEqual(_difficulty_label(1), "easy")
        self.assertEqual(_difficulty_label(2), "easy")
        self.assertEqual(_difficulty_label(3), "medium")
        self.assertEqual(_difficulty_label(5), "hard")

    def test_pick_q_type_check_is_mc(self):
        from app.agents.assessment.generator import _pick_q_type
        self.assertEqual(_pick_q_type(AssessmentGoal(purpose="check")),
                         QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(_pick_q_type(AssessmentGoal(purpose="diagnose")),
                         QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(_pick_q_type(AssessmentGoal(purpose="practice")),
                         QuestionType.SHORT_ANSWER)

    def test_explicit_q_type_wins(self):
        from app.agents.assessment.generator import _pick_q_type
        g = AssessmentGoal(purpose="check", q_type="fill_blank")
        self.assertEqual(_pick_q_type(g), "fill_blank")

    def test_constraint_block_renders_assesses_and_forbidden(self):
        from app.agents.assessment.generator import _constraint_block
        g = AssessmentGoal(assesses=["顶点", "开口方向"], forbidden=["求导"])
        block = _constraint_block(g)
        self.assertIn("顶点", block)
        self.assertIn("求导", block)

    def test_parse_lifts_generated_question(self):
        from app.agents.assessment.generator import _parse
        import json
        raw = json.dumps({"questions": [{
            "stem": "下列哪个是顶点？", "answer": "A",
            "type": "multiple_choice",
            "options": {"A": "(0,0)", "B": "(1,1)"},
            "knowledge_point": "二次函数", "difficulty": "medium",
        }]})
        q = _parse(raw, concept="二次函数", difficulty=3)
        self.assertIsNotNone(q)
        self.assertEqual(q.answer, "A")
        self.assertEqual(q.difficulty, 3)

    def test_parse_returns_none_on_garbage(self):
        from app.agents.assessment.generator import _parse
        self.assertIsNone(_parse("not json at all", concept="x", difficulty=3))
        self.assertIsNone(_parse('{"questions":[]}', concept="x", difficulty=3))
        # question without stem/answer is rejected
        self.assertIsNone(_parse('{"questions":[{"stem":"","answer":""}]}',
                                 concept="x", difficulty=3))

    def test_generate_question_none_without_concept(self):
        # the public entry must refuse an empty concept without raising
        import asyncio
        from app.agents.assessment.generator import generate_question
        ctx = AssessmentContext(concept="")
        r = asyncio.run(generate_question(AssessmentGoal(), ctx, llm=None))  # type: ignore[arg-type]
        self.assertIsNone(r)


class TestAdaptiveTest(unittest.TestCase):
    """Phase 3 CAT: pure stop rules + difficulty stepping."""
    def _res(self, verdict, score=None):
        from app.agents.assessment.state import AssessmentResult as R
        s = {"correct": 1.0, "wrong": 0.0, "partial": 0.5}.get(verdict, 0.0)
        return R(verdict=verdict, score=(score if score is not None else s))

    def _session(self, difficulty=3, count=10):
        from app.agents.assessment.adaptive_test import AssessmentSession
        return AssessmentSession(current_difficulty=difficulty,
                                  goal=AssessmentGoal(concept="x", count=count))

    def test_next_difficulty_steps_up_on_high_accuracy(self):
        from app.agents.assessment.adaptive_test import next_difficulty
        s = self._session(difficulty=3)
        s.results = [self._res("correct"), self._res("correct"),
                     self._res("correct"), self._res("correct"), self._res("correct")]
        self.assertEqual(next_difficulty(s), 4)  # 100% >= 0.8 -> step up

    def test_next_difficulty_steps_down_on_low_accuracy(self):
        from app.agents.assessment.adaptive_test import next_difficulty
        s = self._session(difficulty=3)
        s.results = [self._res("wrong"), self._res("wrong"), self._res("wrong")]
        self.assertEqual(next_difficulty(s), 2)  # 0% <= 0.4 -> step down

    def test_next_difficulty_clamps(self):
        from app.agents.assessment.adaptive_test import next_difficulty
        s = self._session(difficulty=5)
        s.results = [self._res("correct")] * 5
        self.assertEqual(next_difficulty(s), 5)  # already at max
        s2 = self._session(difficulty=1)
        s2.results = [self._res("wrong")] * 5
        self.assertEqual(next_difficulty(s2), 1)  # already at min

    def test_next_difficulty_partial_counts_half(self):
        from app.agents.assessment.adaptive_test import next_difficulty
        s = self._session(difficulty=3)
        s.results = [self._res("correct"), self._res("partial")]  # avg 0.75 < 0.8
        self.assertEqual(next_difficulty(s), 3)  # no step up

    def test_should_stop_mastered(self):
        from app.agents.assessment.adaptive_test import should_stop, STOP_MASTERED
        s = self._session(difficulty=3)
        s.results = [self._res("correct"), self._res("correct")]
        self.assertEqual(should_stop(s), STOP_MASTERED)

    def test_should_stop_mastered_needs_high_difficulty(self):
        from app.agents.assessment.adaptive_test import should_stop
        s = self._session(difficulty=2)  # too easy to claim mastered
        s.results = [self._res("correct"), self._res("correct")]
        self.assertEqual(should_stop(s), "")

    def test_should_stop_confirmed_gap_at_floor(self):
        from app.agents.assessment.adaptive_test import should_stop, STOP_CONFIRMED_GAP
        s = self._session(difficulty=1)
        s.results = [self._res("wrong"), self._res("wrong")]
        self.assertEqual(should_stop(s), STOP_CONFIRMED_GAP)

    def test_should_stop_confirmed_gap_not_at_floor(self):
        from app.agents.assessment.adaptive_test import should_stop
        s = self._session(difficulty=3)  # can still go easier
        s.results = [self._res("wrong"), self._res("wrong")]
        self.assertEqual(should_stop(s), "")

    def test_should_stop_max_reached(self):
        from app.agents.assessment.adaptive_test import should_stop, STOP_MAX
        s = self._session(difficulty=3, count=2)
        s.results = [self._res("wrong"), self._res("correct")]
        self.assertEqual(should_stop(s), STOP_MAX)

    def test_should_stop_oscillating(self):
        from app.agents.assessment.adaptive_test import should_stop, STOP_OSCILLATING
        s = self._session(difficulty=3)
        s.results = [self._res("correct"), self._res("wrong"),
                     self._res("correct"), self._res("wrong")]
        self.assertEqual(should_stop(s), STOP_OSCILLATING)

    def test_should_stop_continue_on_single_answer(self):
        from app.agents.assessment.adaptive_test import should_stop
        s = self._session(difficulty=3)
        s.results = [self._res("correct")]
        self.assertEqual(should_stop(s), "")

    def test_session_persistence_roundtrip(self):
        from app.agents.assessment.adaptive_test import AssessmentSession
        from app.agents.assessment.manager import _session_from_dict
        from app.agents.assessment import session_store
        sid = "student_cat_test_" + os.urandom(2).hex()
        try:
            s = AssessmentSession(student_id=sid,
                                  goal=AssessmentGoal(concept="惯性", count=5),
                                  ctx=AssessmentContext(concept="惯性", current_mastery=0.4),
                                  current_difficulty=3)
            session_store.save_session(sid, s.to_dict())
            loaded = session_store.load_session(sid)
            self.assertIsNotNone(loaded)
            rebuilt = _session_from_dict(loaded)
            self.assertEqual(rebuilt.current_difficulty, 3)
            self.assertEqual(rebuilt.goal.concept, "惯性")
            self.assertEqual(rebuilt.ctx.current_mastery, 0.4)
        finally:
            session_store.clear_session(sid)


class TestAssessmentResultBinary(unittest.TestCase):
    def test_correct_property_threshold(self):
        self.assertTrue(AssessmentResult(score=1.0).correct)
        self.assertFalse(AssessmentResult(score=0.5).correct)
        self.assertFalse(AssessmentResult(score=0.0).correct)


if __name__ == "__main__":
    unittest.main()
