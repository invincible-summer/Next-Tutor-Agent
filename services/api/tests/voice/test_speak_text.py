"""Voice: speakable-text normalization pipeline."""
from __future__ import annotations
import io
import math
import json
import struct
import wave
import unittest
from array import array
from unittest.mock import patch
from tests.support.storage_sandbox import StorageSandboxTestCase
from app.voice.sentences import split_sentences, take_complete, take_speech_cuts
from app.voice.speak_text import to_speakable
from app.voice.wav import wav_to_pcm16
"""Voice layer regressions for browser text input and MeloTTS output."""
if __name__ == "__main__":
    unittest.main()
class TestSpeakText(unittest.TestCase):
    def test_markdown_stripped(self):
        out = to_speakable("## 标题\n\n**重点**与*强调*，[链接](http://x)，`x = 1`")
        self.assertNotIn("#", out)
        self.assertNotIn("**", out)
        self.assertNotIn("[", out)
        self.assertIn("重点", out)
        self.assertIn("链接", out)
        self.assertIn("x 等于 1", out)

    def test_code_fence_placeholder(self):
        out = to_speakable("看代码：\n```python\nprint(1)\n```\n结束。")
        self.assertNotIn("print", out)
        self.assertIn("代码", out)

    def test_math_readings(self):
        out = to_speakable("$\\frac{1}{2}$ 加 $\\sqrt{2}$ 等于多少？")
        self.assertIn("2分之1", out)
        self.assertIn("根号2", out)

    def test_math_equation_speakable(self):
        out = to_speakable("$a^2 + b^2 = c^2$")
        self.assertIn("的2次方", out)
        self.assertIn("等于", out)
        self.assertNotIn("$", out)
        self.assertNotIn("\\", out)

    def test_prose_dash_survives(self):
        out = to_speakable("well-known method，但是 x-1 会读成减，3-x 也是")
        self.assertIn("well-known", out)
        self.assertIn("x减1", out)
        self.assertIn("3减x", out)

    def test_display_math_multiline(self):
        out = to_speakable("定义为\n$$\\int_{-1}^{1}\\frac{1}{x^2} dx$$\n的情形。")
        self.assertNotIn("$", out)
        self.assertNotIn("\\int", out)
        self.assertIn("积分", out)
        self.assertIn("分之", out)

    def test_stray_dollars_stripped(self):
        out = to_speakable("费用是 $5 与 $$ 残留")
        self.assertNotIn("$", out)

    def test_bare_subscript_reading(self):
        out = to_speakable("当 $x_1$ 增大时")
        self.assertIn("x1", out)

    def test_subscript_concatenated_not_worded(self):
        # 2026-08-31 反馈："W下标0" 的读法生硬——下标与底数直接连读。
        out = to_speakable("当 $W_{0}$ 与 $W_0$ 增大时")
        self.assertIn("W0", out)
        self.assertNotIn("下标", out)
        self.assertNotIn("_", out)

    def test_prose_bare_subscript_reads_concatenated(self):
        # 正文裸下标（不在 $...$ 里）与数学段同读法；残余下划线静默删除。
        out = to_speakable("W_{0} 是初角速度，W_0 也一样，$T_{max}$ 是上限")
        self.assertIn("W0 是初角速度", out)
        self.assertIn("Tmax", out)
        self.assertNotIn("_", out)
        self.assertNotIn("下标", out)

    def test_subscript_in_expression_keeps_operators(self):
        out = to_speakable("$x_{i-1}$ 表示前一时刻")
        self.assertIn("i减1", out)
        self.assertNotIn("下标", out)

    def test_math_degree_not_power(self):
        out = to_speakable("角 $30^\\circ$ 是锐角")
        self.assertIn("30度", out)
        self.assertNotIn("次方", out)

    def test_math_function_names(self):
        out = to_speakable("$\\sin x + \\cos x$")
        self.assertIn("正弦", out)
        self.assertIn("余弦", out)
        self.assertNotIn("sin", out)
        self.assertNotIn("\\", out)

    def test_nested_frac_reads_inside_out(self):
        out = to_speakable("$\\frac{\\sqrt{2}}{2}$")
        self.assertIn("2分之根号2", out)

    def test_mathbb_set_names(self):
        out = to_speakable("$x \\in \\mathbb{R}$")
        self.assertIn("属于", out)
        self.assertIn("实数集", out)

    def test_percent_reading(self):
        out = to_speakable("增长 $50\\%$")
        self.assertIn("百分之50", out)

    def test_nth_root_reading(self):
        out = to_speakable("$\\sqrt[3]{8}$")
        self.assertIn("3次根号8", out)

    def test_text_group_keeps_content(self):
        out = to_speakable("$\\text{当 } x > 0 \\text{ 时递增}$")
        self.assertIn("当", out)
        self.assertIn("时递增", out)
        self.assertNotIn("text", out)

    def test_infty_and_tendency(self):
        out = to_speakable("$x \\to \\infty$")
        self.assertIn("趋于", out)
        self.assertIn("无穷", out)

    def test_combined_sum_bounds(self):
        out = to_speakable("$\\sum_{i=1}^{n} i$")
        self.assertIn("求和，从i等于1到n", out)

    def test_vec_and_bar_readings(self):
        out = to_speakable("$\\vec{a}$、$\\bar{x}$")
        self.assertIn("向量a", out)
        self.assertIn("x拔", out)

    def test_paren_and_bracket_display_delimiters(self):
        out = to_speakable(r"\[\gamma=\frac{1}{\sqrt{1-\frac{v^2}{c^2}}}\]")
        self.assertNotIn("\\", out)
        self.assertNotIn("$", out)
        self.assertIn("分之", out)
        self.assertIn("根号", out)

    def test_inline_paren_delimiters(self):
        out = to_speakable(r"函数 \(y=a^x\)（其中 \(a>0\)）递增")
        self.assertIn("y等于a的x次方", out)
        self.assertIn("a大于0", out)
        self.assertNotIn("\\", out)

    def test_braceless_frac_sqrt_vec(self):
        out = to_speakable(r"$\frac12 e^{x^2}$ 与 $\frac1{\sqrt x}$ 与 $\vec L$")
        self.assertIn("2分之1", out)
        self.assertIn("根号x分之1", out)
        self.assertIn("向量L", out)
        self.assertNotIn("frac", out)

    def test_mathrm_bare_differential(self):
        out = to_speakable(r"$\vec v=\frac{\mathrm d \vec r}{\mathrm dt}$")
        self.assertIn("dt分之d", out)
        self.assertNotIn("mathrm", out)

    def test_boxed_keeps_inner_only(self):
        out = to_speakable(r"最优面积是 $$\boxed{50\ \text{m}^2}$$")
        self.assertIn("50 平方米", out)
        self.assertNotIn("boxed", out)

    def test_unit_readings(self):
        self.assertIn("米每秒", to_speakable(r"$c\approx 3.0\times 10^8\ \text{m/s}$"))
        self.assertIn("米每平方秒", to_speakable(r"$a=9.8\ \mathrm{m/s^2}$"))
        self.assertIn("千克", to_speakable(r"质量 $m$ 的单位是 $\mathrm{kg}$"))
        self.assertIn("平方米", to_speakable(r"$S=5\times 10\ \text{m}^2$"))
        self.assertIn("千赫兹", to_speakable(r"频率 $\mathrm{kHz}$"))

    def test_text_words_not_units(self):
        # \text{收敛} is a Chinese word, not a unit expression.
        out = to_speakable(r"$p>1\Rightarrow\text{收敛}$")
        self.assertIn("收敛", out)
        self.assertNotIn("每", out)

    def test_ascii_comparisons(self):
        out = to_speakable(r"当 $\varepsilon>0$ 且 $n>=N$ 时")
        self.assertIn("艾普西隆大于0", out)
        self.assertIn("n大于等于N", out)

    def test_absolute_value_reading(self):
        out = to_speakable(r"$\left|a_n-0\right|=\left|\frac{1}{n}\right|$")
        self.assertIn("an减0的绝对值", out)
        self.assertIn("n分之1的绝对值", out)
        self.assertNotIn("|", out)
        self.assertNotIn("下标", out)

    def test_bracket_interval_reading(self):
        out = to_speakable("在区间 $[-1,1]$ 上定义")
        self.assertIn("从负1到1的闭区间", out)

    def test_table_separator_row_silent(self):
        out = to_speakable("| 物理量 | 单位 |\n|---|---|\n| 力 | $\\mathrm{N}$ |")
        self.assertIn("物理量", out)
        self.assertIn("力", out)
        self.assertIn("牛", out)
        self.assertNotIn("-", out)

    def test_signed_superscript_and_factorial(self):
        out = to_speakable(r"右导数 $h\to0^+$，$f'_+(0)=1$，Taylor 项 $1-\frac{1}{2!}$")
        self.assertIn("0正", out)
        self.assertIn("f撇正括号0括号等于1", out)
        self.assertIn("2的阶乘分之1", out)

    def test_mixed_second_partial(self):
        out = to_speakable(r"记 $\frac{\partial^2 z}{\partial x\partial y}$")
        self.assertIn("z对x、y的2阶偏导数", out)
        self.assertNotIn("frac", out)

    def test_binary_vs_unary_minus(self):
        out = to_speakable("$x^2-9=(x-3)(x+3)$ 且 $a=-1$")
        self.assertIn("x的2次方减9", out)
        self.assertIn("x减3", out)
        self.assertIn("a等于负1", out)

    def test_nested_power_wording(self):
        out = to_speakable(r"$\int x e^{x^2}dx$")
        self.assertIn("e的x平方次方", out)

    def test_cut_formula_drops_known_command_names(self):
        # A message truncated mid-formula: the bare \frac name must not be
        # read as the English word "frac".
        out = to_speakable("对 x 求偏导：\n\n$$\n\\frac{\\partial")
        self.assertNotIn("frac", out)
        self.assertNotIn("\\", out)

    def test_prose_english_dash_survives_math_rules(self):
        out = to_speakable("well-known method 在 $b-a$ 里读减")
        self.assertIn("well-known", out)
        self.assertIn("b减a", out)

    def test_tool_call_markup_not_spoken(self):
        # 2026-08-31 回归：模型把工具调用叙述成 XML 正文。护栏在上游拦截，
        # to_speakable 兜底保证任何漏网标记都不会被朗读。
        text = ("我先查一下教材。<tool_call>\n<function=knowledge_search>\n"
                '<parameter=keywords>角动量守恒定律 合外力矩为零</parameter>\n'
                '<parameter=content_types>["textbook"]</parameter>\n'
                "<parameter=max_results>5</parameter>\n</function>\n</tool_call>"
                "根据资料，角动量守恒的条件是合外力矩为零。")
        out = to_speakable(text)
        self.assertNotIn("tool_call", out)
        self.assertNotIn("knowledge_search", out)
        self.assertNotIn("parameter", out)
        self.assertIn("我先查一下教材", out)
        self.assertIn("角动量守恒的条件是合外力矩为零", out)

    def test_unclosed_tool_call_tail_not_spoken(self):
        # 流式截断可能留下未闭合的 <tool_call> 尾块：整块静默。
        out = to_speakable("讲解开始。<tool_call><function=knowledge_search>角动量")
        self.assertEqual(out, "讲解开始。")

    def test_stray_tool_tag_shells_stripped(self):
        # 只有闭合壳残留（块正则没吃到）时，壳本身也剥掉。
        out = to_speakable("结论如下。</function></tool_call>完毕。")
        self.assertNotIn("function", out)
        self.assertNotIn("tool_call", out)
        self.assertIn("结论如下", out)
        self.assertIn("完毕", out)
