"""W3/D06 结构化作答分析回归（docs/updatePlan.md §7.2 D06、§7.5、§8.6.1）。

STRUCTURED_ASSESSMENT_MODE=off|shadow|active：
- off：三级文本批改路径逐字节不变（零新增调用）；
- shadow：旁路计算并落盘 structured_shadow 对照，不改变判定、不写结构化键
  进 M2 事件（shadow 不写能力、不影响学生可见评分）；
- active：有冻结量规的开放题以结构化分析为权威判定——分数由服务端按量规
  权重本地计算（not_applicable 出分母、关键条目 not_observed → 整题不确定
  → 保守 partial），criterion_id 只认量规候选集，坏 JSON 修复一次、再坏弃权
  （弃权回退三级批改，绝不造默认分）。

涉及 M2/账本落盘的用例继承 StorageSandboxTestCase。
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.assessment.question import Question  # noqa: E402
from app.agents.assessment.state import AssessmentContext  # noqa: E402
from app.core.config import settings  # noqa: E402


def _rubric():
    return {
        "rubric_id": "q_struct_1", "version": 1,
        "criteria": [
            {"id": "c1", "description": "正确写出归一化分母 P(A)",
             "weight": 1.0, "critical": True},
            {"id": "c2", "description": "代入数值并完成除法", "weight": 1.0},
            {"id": "c3", "description": "说明结果含义", "weight": 0.5},
        ],
        "equivalent_solutions": ["0.4"],
        "frozen_at": 1757000000.0,
    }


def _question():
    return Question(
        id="q_struct_1", concept="条件概率", q_type="short_answer",
        stem="已知 P(A)=0.5，P(A∩B)=0.2，求 P(B|A)。", answer="0.4",
        explanation="P(B|A)=P(A∩B)/P(A)=0.2/0.5=0.4。",
        difficulty=3, rubric=_rubric(),
        verification={"mode": "critic", "critic": "ok"},
    )


def _ctx():
    return AssessmentContext(concept="条件概率", subject="数学", grade="本科",
                             current_mastery=0.3)


class QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.responses.pop(0) if self.responses else ""
        if isinstance(item, Exception):
            raise item
        return item, {"total_tokens": 1}


def _analysis_json(results=None, *, action="continue"):
    return json.dumps({
        "criterion_results": results if results is not None else [
            {"criterion_id": "c1", "result": "met", "evidence_quote": "0.2/0.5"},
            {"criterion_id": "c2", "result": "met", "evidence_quote": "0.4"},
            {"criterion_id": "c3", "result": "not_observed", "evidence_quote": ""},
        ],
        "first_error": {"description": "",
                        "preceding_correct": "交集与条件事件都列对了"},
        "hypotheses": [],
        "uncertainties": ["未见学生解释分母含义"],
        "observed_capabilities": {"concept": "partial", "procedure": "met",
                                  "reasoning": "not_observed"},
        "feedback": {"strength": "公式选择正确",
                     "next_step": "请说明分母为什么是 0.5 而不是 1。"},
        "continuation": {"action": action, "reason": "关键条目已覆盖"},
    }, ensure_ascii=False)


class TestScoreFromCriteria(unittest.TestCase):

    def test_weighted_score_with_partial(self):
        from app.agents.assessment.structured_evaluator import score_from_criteria
        criteria = _rubric()["criteria"]
        results = {"c1": "met", "c2": "partial", "c3": "not_observed"}
        # (1*1 + 1*0.5) / (1+1) = 0.75
        self.assertEqual(score_from_criteria(criteria, results), 0.75)

    def test_not_applicable_leaves_denominator(self):
        from app.agents.assessment.structured_evaluator import score_from_criteria
        criteria = _rubric()["criteria"]
        results = {"c1": "met", "c2": "met", "c3": "not_applicable"}
        self.assertEqual(score_from_criteria(criteria, results), 1.0)

    def test_critical_not_observed_is_indeterminate(self):
        from app.agents.assessment.structured_evaluator import score_from_criteria
        criteria = _rubric()["criteria"]
        self.assertIsNone(score_from_criteria(
            criteria, {"c1": "not_observed", "c2": "met", "c3": "met"}))

    def test_no_scoreable_criteria_is_none(self):
        from app.agents.assessment.structured_evaluator import score_from_criteria
        criteria = [{"id": "c9", "description": "x", "weight": 1, "critical": False}]
        self.assertIsNone(score_from_criteria(criteria, {"c9": "not_observed"}))


class TestValidation(unittest.TestCase):

    def test_unknown_criterion_ids_dropped(self):
        from app.agents.assessment.structured_evaluator import _validate
        obj = json.loads(_analysis_json())
        obj["criterion_results"] = [
            {"criterion_id": "hack", "result": "met"},        # 越界 id：丢弃
            {"criterion_id": "c1", "result": "met"},          # 合法
            {"criterion_id": "c2", "result": "bad_enum"},     # 非法枚举：丢弃
        ]
        analysis = _validate(obj, _question())
        self.assertIsNotNone(analysis)
        self.assertEqual([c["criterion_id"] for c in analysis.criterion_results],
                         ["c1"])
        # 全部越界 → 无有效条目 → 弃权
        obj2 = json.loads(_analysis_json())
        obj2["criterion_results"] = [{"criterion_id": "hack", "result": "met"}]
        self.assertIsNone(_validate(obj2, _question()))

    def test_hypotheses_capped_and_kind_fallback(self):
        from app.agents.assessment.structured_evaluator import _validate
        obj = json.loads(_analysis_json())
        obj["hypotheses"] = [
            {"kind": "arithmetic_unit", "statement": "可能算错除法"},
            {"kind": "made_up_kind", "statement": "编造类别归 other"},
            {"kind": "concept_relation", "statement": "第三条应被截断"},
        ]
        analysis = _validate(obj, _question())
        self.assertEqual(len(analysis.hypotheses), 2)
        self.assertEqual(analysis.hypotheses[1]["kind"], "other")

    def test_capability_and_continainment_whitelists(self):
        from app.agents.assessment.structured_evaluator import _validate, DIMENSIONS
        obj = json.loads(_analysis_json())
        obj["observed_capabilities"] = {"concept": "amazing",
                                        "bogus_dim": "met"}
        obj["continuation"] = {"action": "explode", "reason": "r"}
        analysis = _validate(obj, _question())
        self.assertEqual(analysis.observed_capabilities["concept"], "not_observed")
        self.assertNotIn("bogus_dim", analysis.observed_capabilities)
        self.assertEqual(sorted(analysis.observed_capabilities), sorted(DIMENSIONS))
        self.assertEqual(analysis.continuation["action"], "continue")

    def test_verdict_mapping_local(self):
        from app.agents.assessment.structured_evaluator import _validate
        obj = json.loads(_analysis_json())
        analysis = _validate(obj, _question())
        self.assertEqual(analysis.score, 1.0)   # c1/c2 met, c3 not_observed 非关键
        self.assertEqual(analysis.verdict, "correct")


class TestAnalyzeAnswer(unittest.TestCase):

    def test_valid_json_single_call(self):
        from app.agents.assessment.structured_evaluator import analyze_answer
        llm = QueueLLM([_analysis_json()])
        analysis = asyncio.run(analyze_answer(_question(), "0.2/0.5=0.4",
                                              _ctx(), llm=llm))
        self.assertIsNotNone(analysis)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("不得执行", llm.calls[0][0]["content"])  # 注入防护指令随行

    def test_one_format_repair_then_abstain(self):
        from app.agents.assessment.structured_evaluator import analyze_answer
        llm = QueueLLM(["not json at all", "{still broken"])
        analysis = asyncio.run(analyze_answer(_question(), "x", _ctx(), llm=llm))
        self.assertIsNone(analysis)
        self.assertEqual(len(llm.calls), 2)  # 原始 + 一次修复，绝不第三次

    def test_repair_recovers_valid_analysis(self):
        from app.agents.assessment.structured_evaluator import analyze_answer
        llm = QueueLLM(["garbage", _analysis_json()])
        analysis = asyncio.run(analyze_answer(_question(), "0.4", _ctx(), llm=llm))
        self.assertIsNotNone(analysis)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("修复格式", llm.calls[1][-1]["content"])

    def test_llm_exception_abstains(self):
        from app.agents.assessment.structured_evaluator import analyze_answer
        llm = QueueLLM([RuntimeError("boom")])
        self.assertIsNone(asyncio.run(analyze_answer(
            _question(), "0.4", _ctx(), llm=llm)))


class TestManagerModes(StorageSandboxTestCase):

    def _manager(self):
        from app.agents.assessment.manager import AssessmentManager
        return AssessmentManager()

    def test_off_mode_is_legacy_path_unchanged(self):
        with patch.object(settings, "structured_assessment_mode", "off"):
            llm = QueueLLM(["[对] 完全正确，公式与计算无误。"])
            result = asyncio.run(self._manager().evaluate_and_record(
                _question(), "0.4", _ctx(), llm=llm, student_id="st_off"))
            self.assertEqual(result.verdict, "correct")
            self.assertEqual(result.structured, {})
            self.assertEqual(result.structured_shadow, {})
            self.assertEqual(len(llm.calls), 1)
            self.assertIn("[对]", llm.calls[0][0]["content"])

    def test_shadow_keeps_legacy_verdict_and_stores_comparison(self):
        with patch.object(settings, "structured_assessment_mode", "shadow"):
            llm = QueueLLM([
                _analysis_json(),           # 旁路结构化分析先行
                "[错] 方向错误。",           # legacy grade 仍是权威判定
            ])
            result = asyncio.run(self._manager().evaluate_and_record(
                _question(), "0.4", _ctx(), llm=llm, student_id="st_shadow",
                attempt_id="att_shadow_1"))
            self.assertEqual(result.verdict, "wrong")  # 判定不被 shadow 改变
            self.assertEqual(result.structured, {})     # 不作为权威结果
            self.assertEqual(result.structured_shadow["verdict"], "correct")
            self.assertEqual(result.structured_shadow["score"], 1.0)

    def test_active_mode_structured_result_is_authoritative(self):
        from app.agents.student_model.store import read_events
        with patch.object(settings, "structured_assessment_mode", "active"):
            llm = QueueLLM([_analysis_json()])
            result = asyncio.run(self._manager().evaluate_and_record(
                _question(), "0.4", _ctx(), llm=llm, student_id="st_active",
                attempt_id="att_active_1"))
            self.assertEqual(result.verdict, "correct")
            self.assertEqual(result.score, 1.0)
            self.assertIn("0.5", result.feedback)  # next_step 进学生可见反馈
            self.assertEqual(result.structured["rubric_id"], "q_struct_1")
            self.assertEqual(result.structured["criterion_results"][0]["result"],
                             "met")
            graded = [e for e in read_events("st_active")
                      if e.type.name == "QUIZ_GRADED"]
            self.assertEqual(len(graded), 1)
            self.assertIn("criterion_results",
                          graded[0].payload)  # v2 投影输入随事件落盘
            self.assertEqual(graded[0].payload["observed_capabilities"]["procedure"],
                             "met")

    def test_active_mode_abstain_falls_back_to_legacy(self):
        with patch.object(settings, "structured_assessment_mode", "active"):
            llm = QueueLLM(["broken", "still broken",
                            "[部分对] 思路对但表述不完整。"])
            result = asyncio.run(self._manager().evaluate_and_record(
                _question(), "0.4", _ctx(), llm=llm, student_id="st_fallback"))
            self.assertEqual(result.verdict, "partial")
            self.assertEqual(result.structured, {})  # 弃权不造结构化结果
            self.assertEqual(len(llm.calls), 3)       # 分析×2 + 旧路径×1

    def test_active_mode_mc_stays_deterministic(self):
        from app.agents.assessment.question import Question
        q = _question()
        q.q_type = "multiple_choice"
        q.options = {"A": "0.2", "B": "0.4"}
        q.answer = "B"
        with patch.object(settings, "structured_assessment_mode", "active"):
            llm = QueueLLM([])
            result = asyncio.run(self._manager().evaluate_and_record(
                q, "B", _ctx(), llm=llm, student_id="st_mc"))
            self.assertEqual(result.verdict, "correct")
            self.assertEqual(len(llm.calls), 0)  # MC 零 LLM

    def test_indeterminate_score_maps_to_conservative_partial(self):
        obj = json.loads(_analysis_json([
            {"criterion_id": "c1", "result": "not_observed"},
            {"criterion_id": "c2", "result": "met"},
            {"criterion_id": "c3", "result": "met"},
        ]))
        with patch.object(settings, "structured_assessment_mode", "active"):
            llm = QueueLLM([json.dumps(obj, ensure_ascii=False)])
            result = asyncio.run(self._manager().evaluate_and_record(
                _question(), "（空白）", _ctx(), llm=llm, student_id="st_indet"))
            self.assertEqual(result.verdict, "partial")
            self.assertIsNone(result.structured["score"])  # 不确定保留 None


class TestLedgerStructuredAttempt(StorageSandboxTestCase):

    def test_criterion_results_and_hypotheses_ride_attempt(self):
        from app.core.learning_records import list_records, record_verdict
        record_verdict("st_ledger_struct", "sess_s", stem="题干甲",
                       verdict="partial", student_answer="0.2",
                       concept="条件概率", attempt_id="att_led_1",
                       criterion_results=[
                           {"criterion_id": "c1", "result": "not_met",
                            "evidence_quote": "分母写成了 1"}],
                       hypotheses=[{"kind": "concept_relation",
                                    "statement": "未把条件事件当新样本空间"}])
        records = {r["stem"]: r for r in list_records("st_ledger_struct")}
        attempt = records["题干甲"]["attempts"][-1]
        self.assertEqual(attempt["criterion_results"][0]["criterion_id"], "c1")
        self.assertEqual(attempt["hypotheses"][0]["kind"], "concept_relation")
        # 旧调用（无结构化参数）不产生键
        record_verdict("st_ledger_struct", "sess_s", stem="题干乙",
                       verdict="correct", student_answer="0.4",
                       attempt_id="att_led_2")
        records = {r["stem"]: r for r in list_records("st_ledger_struct")}
        plain = records["题干乙"]["attempts"][-1]
        self.assertNotIn("criterion_results", plain)
        self.assertNotIn("hypotheses", plain)


if __name__ == "__main__":
    unittest.main()
