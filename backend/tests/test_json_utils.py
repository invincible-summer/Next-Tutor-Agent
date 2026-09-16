"""LaTeX 容错 JSON 解析（quiz 链路共享）回归。

live 验收根因：推理模型在 JSON 字符串里输出 `$\\{a_n\\}$`、`$\\sqrt{2}$`
等 LaTeX；单反斜杠转义是非法 JSON，plain json.loads 拒绝整个文档，
generate_quiz/fit_quiz/blueprint/critic/assessment 生成全部退化为 0 题。
"""
from __future__ import annotations

import json
import unittest

from app.core.json_utils import (extract_json_object, loads_tolerant,
                                 repair_backslash_escapes)

# 模型实际输出形态（update_plan 验收现场捕获）：字符串值里是单反斜杠。
LATEX_QUIZ = (
    "{\n  \"questions\": [\n    {\n      \"id\": 1,\n"
    "      \"type\": \"short_answer\",\n"
    "      \"stem\": \"设数列 $\\{a_n\\}$ 满足 $a_1=1$，求 $\\lim_{n\\to\\infty} a_n$。\",\n"
    "      \"answer\": \"极限为 $\\sqrt{2}$。\",\n"
    "      \"explanation\": \"先证有界性，再证单调性，由单调有界定理知收敛。\",\n"
    "      \"knowledge_point\": \"极限收敛\",\n"
    "      \"difficulty\": \"medium\"\n    }\n  ]\n}"
)


class RepairBackslashTest(unittest.TestCase):
    def test_invalid_latex_escapes_repaired(self):
        repaired = repair_backslash_escapes(LATEX_QUIZ)
        data = json.loads(repaired)
        stem = data["questions"][0]["stem"]
        # 解析结果保留写作者意图的单反斜杠 LaTeX 源码。
        self.assertIn("$\\{a_n\\}$", stem)
        self.assertIn("$\\sqrt{2}$", data["questions"][0]["answer"])

    def test_valid_escapes_untouched(self):
        src = '{"a": "line\\nbreak \\"quote\\\\ ok \\u4e2d"}'
        self.assertEqual(repair_backslash_escapes(src), src)
        self.assertEqual(loads_tolerant(src)["a"],
                         "line\nbreak \"quote\\ ok 中")

    def test_short_unicode_escape_repaired(self):
        # \u 后不足 4 位十六进制：按非法转义修复而不是抛错。
        self.assertEqual(loads_tolerant('{"a": "\\u12"}'), {"a": "\\u12"})

    def test_unrepairable_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            loads_tolerant('{"a": "unterminated')


class LatexRestoreTest(unittest.TestCase):
    """json.loads 把 \\frac 的 \\f 还原成换页符、\\right 的 \\r 还原成回车，
    公式损坏。loads_tolerant 必须把这些控制字符还原成反斜杠形式。"""

    def test_frac_right_left_survive_strict_parse(self):
        # 模型未转义 \frac/\right/\left：strict json.loads "成功"但吞掉反斜杠。
        raw = ('{"stem": "a_{n+1}=\\frac{1}{2}\\left(a_n+'
               '\\frac{2}{a_n}\\right)$，且 $n \\geq 1$"}')
        out = loads_tolerant(raw)
        self.assertEqual(out["stem"],
                         "a_{n+1}=\\frac{1}{2}\\left(a_n+"
                         "\\frac{2}{a_n}\\right)$，且 $n \\geq 1$")

    def test_real_newline_preserved(self):
        self.assertEqual(loads_tolerant('{"a": "第一行\\n第二行"}')["a"],
                         "第一行\n第二行")

    def test_tab_and_cr_followed_by_letter_restored(self):
        out = loads_tolerant('{"a": "$\\tau$ 与 $\\rho$"}')
        self.assertEqual(out["a"], "$\\tau$ 与 $\\rho$")

    def test_lf_before_latex_letters_restored(self):
        out = loads_tolerant('{"a": "$\\nu > 0$ 且 $\\neq 0$"}')
        self.assertEqual(out["a"], "$\\nu > 0$ 且 $\\neq 0$")

    def test_restore_walks_nested_structures(self):
        raw = ('{"items": [{"stem": "$\\frac{1}{2}$"}, ["$\\beta$"]], '
               '"$\\x0c$": 1}')
        out = loads_tolerant(raw)
        self.assertEqual(out["items"][0]["stem"], "$\\frac{1}{2}$")
        self.assertEqual(out["items"][1][0], "$\\beta$")


class ExtractObjectTest(unittest.TestCase):
    def test_latex_quiz_payload_extracted(self):
        data = extract_json_object("前置废话。\n" + LATEX_QUIZ + "\n后置废话。")
        self.assertIsInstance(data, dict)
        self.assertEqual(len(data["questions"]), 1)

    def test_unparseable_returns_none(self):
        self.assertIsNone(extract_json_object("no json here"))

    def test_strict_payload_unchanged(self):
        data = extract_json_object('{"ok": 1, "text": "中文\\n换行"}')
        self.assertEqual(data, {"ok": 1, "text": "中文\n换行"})


class QuizParseChainTest(unittest.TestCase):
    """接线层：三个出题解析器在 LaTeX 输出下不再返回空。"""

    def test_generate_quiz_parse_accepts_latex(self):
        from app.tools.quiz import GenerateQuizTool
        parsed = GenerateQuizTool._parse(LATEX_QUIZ)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["knowledge_point"], "极限收敛")

    def test_fit_quiz_parse_accepts_latex(self):
        from app.tools.fit_quiz import FitQuizTool
        parsed = FitQuizTool._parse(LATEX_QUIZ)
        self.assertEqual(len(parsed), 1)

    def test_blueprint_parse_accepts_latex(self):
        from app.core.quiz_design import parse_blueprint
        raw = ("{\"items\": [{\"construction_brief\": \"考查 $\\{a_n\\}$ 的收敛性\""
               ", \"target_claims\": [\"单调有界\"]}]}")
        items = parse_blueprint(raw)
        self.assertEqual(len(items), 1)

    def test_critic_audit_parse_accepts_latex(self):
        from app.core.quiz_verify import _parse_audits
        raw = ("{\"items\": [{\"question_ref\": \"q_1\", \"proposed_status\": "
               "\"passed\", \"answer_check\": \"重解得 $\\sqrt{2}$，一致\"}]}")
        audits = _parse_audits(raw)
        self.assertEqual(audits["q_1"]["proposed_status"], "passed")

    def test_assessment_generator_parse_accepts_latex(self):
        from app.agents.assessment.generator import _parse_dict
        q = _parse_dict(LATEX_QUIZ)
        self.assertIsNotNone(q)
        self.assertIn("$\\{a_n\\}$", q["stem"])


if __name__ == "__main__":
    unittest.main()
