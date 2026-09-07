"""Voice layer regressions for browser text input and MeloTTS output."""
from __future__ import annotations

import io
import math
import json
import struct
import wave
import unittest
from array import array
from unittest.mock import patch

from tests.storage_sandbox import StorageSandboxTestCase

from app.voice.sentences import split_sentences, take_complete, take_speech_cuts
from app.voice.speak_text import to_speakable
from app.voice.wav import wav_to_pcm16


class TestSentenceSplitting(unittest.TestCase):
    def test_chinese_terminators(self):
        self.assertEqual(split_sentences("你好。这是两句！还有；分号？"),
                         ["你好。", "这是两句！", "还有；", "分号？"])

    def test_ascii_dot_rules(self):
        parts = split_sentences("英文 fine. Next one. 小数 3.14 不切")
        self.assertEqual(parts, ["英文 fine.", "Next one.", "小数 3.14 不切"])

    def test_streaming_take_complete_keeps_remainder(self):
        complete, rest = take_complete("第一句完整。第二句还没说")
        self.assertEqual(complete, ["第一句完整。"])
        self.assertEqual(rest, "第二句还没说")
        complete, rest = take_complete(rest + "完了吗？")
        self.assertEqual(complete, ["第二句还没说完了吗？"])
        self.assertEqual(rest, "")

    def test_force_split_long_runon(self):
        parts = split_sentences("字" * 300)
        self.assertTrue(all(len(p) <= 121 for p in parts))
        self.assertGreaterEqual(sum(len(p) for p in parts), 300)

    def test_math_span_blocks_split(self):
        text = "定义为\n$$\\int_{-1}^{1} x. y$$\n所以收敛。下一段"
        complete, rest = take_complete(text)
        self.assertEqual(complete, ["定义为\n$$\\int_{-1}^{1} x. y$$\n所以收敛。"])
        self.assertEqual(rest, "下一段")

    def test_inline_math_decimal_intact(self):
        complete, rest = take_complete("圆周率是 $3.14$ 与 $2.71$。好了")
        self.assertEqual(complete, ["圆周率是 $3.14$ 与 $2.71$。"])
        self.assertEqual(rest, "好了")

    def test_unclosed_math_holds_buffer(self):
        complete, rest = take_complete("例如 $x^2 还没")
        self.assertEqual(complete, [])
        self.assertIn("$x^2", rest)

    def test_bracket_display_math_holds_buffer(self):
        # \[ opener arrived, \] still streaming: the buffer must hold like
        # an unclosed $$ (terminators inside are protected).
        complete, rest = take_complete("定义为\n\\[\\left|a_n-0\\right| =\\frac{1}{n}. 还没闭合")
        self.assertEqual(complete, [])
        self.assertIn("$$", rest)

    def test_bracket_display_no_split_inside(self):
        # The trailing period sits inside \[...\]: it must not end the
        # sentence (a cut-open span would read raw LaTeX downstream). The
        # sentence completes only at the real terminator after the span.
        text = "严格含义是：\n\\[\n\\left|a_n-0\\right|\n=\\frac{1}{n}\n<\\varepsilon.\n\\]\n下一段说明。"
        complete, rest = take_complete(text)
        self.assertEqual(complete, ["严格含义是：\n$$\n\\left|a_n-0\\right|\n=\\frac{1}{n}"
                                    "\n<\\varepsilon.\n$$\n下一段说明。"])
        self.assertEqual(rest, "")

    def test_row_break_spacing_not_display_opener(self):
        # \\[2mm] is a row break with spacing, not an opener: pairing must
        # survive (a misread here inverts every following math span).
        text = "因此：\n$$\na=1,\\\\[2mm]\nb=2.\n$$\n完毕。"
        complete, rest = take_complete(text)
        self.assertEqual(complete, [text])
        self.assertEqual(rest, "")

    def test_force_split_keeps_math_span_whole(self):
        formula = "$$" + "\\frac{x+1}{x-2}+y" * 12 + "$$"
        sentence = "结论是" + formula + "，然后解释。"
        parts = split_sentences("字" * 20 + sentence)
        for part in parts:
            self.assertEqual(part.count("$$") % 2, 0,
                             f"formula sliced open: {part[:60]}")


class TestSpeechCuts(unittest.TestCase):
    """Clause-level streaming cuts feeding the synthesis pipeline."""

    def test_weak_punct_cuts_only_after_min_length(self):
        # The first ， sits below _SPEECH_MIN_CHARS and must hold; the second
        # one (buffer >= 24 chars) ends the clip without waiting for a full
        # sentence terminator.
        cuts, rest = take_speech_cuts(
            "我们首先来看这个函数的定义域，它必须满足分母不为零，同时分子也要有意义。")
        self.assertEqual(cuts, ["我们首先来看这个函数的定义域，它必须满足分母不为零，",
                                "同时分子也要有意义。"])
        self.assertEqual(rest, "")

    def test_short_strong_sentence_still_cuts(self):
        cuts, rest = take_speech_cuts("短句。下一句是完整的。")
        self.assertEqual(cuts, ["短句。", "下一句是完整的。"])
        self.assertEqual(rest, "")

    def test_streaming_remainder_carries_over(self):
        cuts, rest = take_speech_cuts("这一小段还不够长，没有到最小切分长度")
        self.assertEqual(cuts, [])
        self.assertTrue(rest)
        cuts, rest = take_speech_cuts(rest + "所以继续等待。")
        self.assertEqual(cuts, ["这一小段还不够长，没有到最小切分长度所以继续等待。"])
        self.assertEqual(rest, "")

    def test_math_span_blocks_weak_cut(self):
        # Weak punctuation and length inside $$...$$ never cut; the clip
        # ends at the ，after the span closes, formula intact.
        text = "考虑函数 $$f(x)=x^2, x \\in [0,1]$$ 的性质，它在此区间上递增。"
        cuts, rest = take_speech_cuts(text)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(cuts[0].count("$$"), 2)
        self.assertIn("f(x)=x^2", cuts[0])
        # The post-span tail is under the min length, so its ，holds and the
        # clip completes at the sentence terminator instead.
        self.assertEqual(cuts[1], "的性质，它在此区间上递增。")
        self.assertEqual(rest, "")

    def test_unclosed_math_holds(self):
        cuts, rest = take_speech_cuts("例如 $x^2, 还没闭合")
        self.assertEqual(cuts, [])
        self.assertIn("$x^2", rest)

    def test_fence_is_no_cut_zone(self):
        # Commas inside a code fence must not cut: the fence collapses to a
        # placeholder in to_speakable only when it survives as one piece.
        text = "看下面的实现，\n```python\nprint(a, b)\n```\n然后继续说明。"
        cuts, rest = take_speech_cuts(text)
        self.assertEqual(rest, "")
        for cut in cuts:
            self.assertEqual(cut.count("```") % 2, 0,
                             f"fence sliced open: {cut[:60]}")
        self.assertIn("print(a, b)", "".join(cuts))

    def test_unclosed_fence_holds(self):
        cuts, rest = take_speech_cuts("看代码：```python\nprint(1)")
        self.assertEqual(cuts, [])
        self.assertIn("```", rest)

    def test_punctuation_free_run_hard_capped(self):
        cuts, rest = take_speech_cuts("字" * 300)
        self.assertTrue(cuts)
        self.assertTrue(all(len(c) <= 120 for c in cuts))
        self.assertEqual(sum(len(c) for c in cuts) + len(rest), 300)

    def test_first_cut_dispatches_early(self):
        # 23 chars + ，: the cut fires exactly at min length, so the first
        # clip leaves long before any sentence terminator exists.
        cuts, rest = take_speech_cuts("前" * 23 + "，后面还有很多内容没有结束")
        self.assertEqual(cuts, ["前" * 23 + "，"])
        self.assertEqual(rest, "后面还有很多内容没有结束")

    def test_table_block_is_single_cut(self):
        # 表格是不可切区：前导正文单独成句，整块表格作为一个 cut 交给
        # worker（换成口播引导语 + 上黑板），表格后的正文照常切分。
        text = ("总结如下：\n| 物理量 | 单位 |\n|---|---|\n"
                "| 力 | 牛 |\n| 功 | 焦 |\n接下来看例题。")
        cuts, rest = take_speech_cuts(text)
        self.assertEqual(cuts[0], "总结如下：")
        self.assertTrue(cuts[1].startswith("| 物理量"))
        self.assertIn("| 力 | 牛 |", cuts[1])
        self.assertEqual(cuts[1].count("\n"), 3)
        self.assertEqual(cuts[2], "接下来看例题。")
        self.assertEqual(rest, "")

    def test_unclosed_table_holds(self):
        cuts, rest = take_speech_cuts("| a | b |\n|---|---|\n| 1")
        self.assertEqual(cuts, [])
        self.assertTrue(rest.startswith("| a | b |"))

    def test_long_table_not_hard_capped(self):
        # 长表不被 120 硬上限劈开：未闭合时整块留在 pending，补上表格后
        # 的正文行后一次性成 cut。
        rows = ("| 量 | 值 |\n|---|---|\n"
                + "".join(f"| 项目{i} | 数字{i} |\n" for i in range(30)))
        cuts, rest = take_speech_cuts(rows)
        self.assertEqual(cuts, [])
        self.assertGreater(len(rest), 120)
        cuts2, rest2 = take_speech_cuts(rows + "好。")
        self.assertEqual(len(cuts2), 2)
        self.assertTrue(cuts2[0].startswith("| 量 |"))
        self.assertGreater(len(cuts2[0]), 120)
        self.assertEqual(cuts2[1], "好。")
        self.assertEqual(rest2, "")

    def test_table_cell_math_does_not_toggle_math_pairing(self):
        # 表内 $ 公式不翻转数学配对状态：表格结束后正文的未闭合公式照常
        # hold，不会被当成"已闭合"而提前下刀。
        text = ("| 力 | $F$ |\n|---|---|\n| 1 | 2 |\n"
                "再看 $x^2, 未闭合")
        cuts, rest = take_speech_cuts(text)
        self.assertEqual(len(cuts), 1)
        self.assertTrue(cuts[0].startswith("| 力 |"))
        self.assertIn("$x^2", rest)


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


class TestSpeakableChunks(unittest.TestCase):
    def test_short_text_single_chunk(self):
        from app.api.v1.voice import _speakable_chunks
        self.assertEqual(_speakable_chunks("短句。"), ["短句。"])

    def test_long_text_chunks_at_punctuation(self):
        from app.api.v1.voice import _speakable_chunks
        text = "这一句用来填充长度，" * 40  # 520 chars, cut points everywhere
        chunks = _speakable_chunks(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 240)
        self.assertEqual("".join(chunks), text)

    def test_unpunctuated_text_hard_split_under_sidecar_cap(self):
        from app.api.v1.voice import _speakable_chunks
        text = "字" * 600
        chunks = _speakable_chunks(text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 240)
        self.assertEqual("".join(chunks), text)


class TestWavHelpers(unittest.TestCase):
    def test_decode_sidecar_wav(self):
        pcm = b"\x01\x02" * 1600
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(44100)
            wav_file.writeframes(pcm)
        out, rate = wav_to_pcm16(buf.getvalue())
        self.assertEqual(out, pcm)
        self.assertEqual(rate, 44100)


class TestVoiceWebSocket(StorageSandboxTestCase):
    """Browser text protocol with a canned run_turn and stub TTS."""

    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        from app.voice.tts import reset_tts_provider
        self._reset_tts = reset_tts_provider
        patcher = patch.object(settings, "voice_tts_provider", "stub")
        patcher.start()
        self._patches.append(patcher)

        from app.main import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._reset_tts()
        super().tearDown()

    async def _canned_turn(self, user_message, session, tools, llm=None,
                           progress_cb=None, lang="zh", output_language=None,
                           attachments=None, student_id=""):
        yield {"type": "step", "step": "thinking"}
        yield {"type": "answer", "content": "勾股定理是对的。", "is_delta": True}
        yield {"type": "answer", "content": "两直角边的平方和等于斜边平方！", "is_delta": True}
        from app.core.session import save_session
        save_session(session)
        yield {"type": "done", "thinking": "", "answer": "…",
               "tool_calls": [], "trace_id": "trace_voice_test"}

    def _ticket(self) -> str:
        resp = self.client.post("/api/v1/voice/ticket")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["ticket"]

    def _receive_turn(self, ws, session_id):
        events = []
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                events.append(("audio", len(msg["bytes"])))
                continue
            if msg.get("text") is None:
                continue
            event = json.loads(msg["text"])
            events.append(event["type"])
            if event["type"] == "error":
                self.fail(f"unexpected error event: {event}")
            if event["type"] == "turn_end":
                self.assertEqual(event["session_id"], session_id)
                self.assertTrue(event["tts_ok"])
                return events

    def _receive_until(self, ws, stop_type):
        """Collect raw events (dicts + audio tuples) until `stop_type` arrives."""
        events = []
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                events.append(("audio", len(msg["bytes"])))
                continue
            if msg.get("text") is None:
                continue
            event = json.loads(msg["text"])
            self.assertNotEqual(event["type"], "error",
                                f"unexpected error event: {event}")
            events.append(event)
            if event["type"] == stop_type:
                return events

    def test_status_reports_browser_stt_and_tts(self):
        data = self.client.get("/api/v1/voice/status").json()
        self.assertEqual(data, {"enabled": True, "stt": "browser", "tts": "stub"})

    def test_status_disabled_tts_still_reports_browser_stt(self):
        from app.core.config import settings
        from app.voice.tts import reset_tts_provider

        with patch.object(settings, "voice_tts_provider", "off"):
            reset_tts_provider()
            data = self.client.get("/api/v1/voice/status").json()
        reset_tts_provider()
        self.assertEqual(data, {"enabled": False, "stt": "browser", "tts": None})

    def test_text_turn_without_pcm(self):
        with patch("app.agents.chat_agent.run_turn", self._canned_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                bound = ws.receive_json()
                self.assertEqual(bound["type"], "session_bound")
                sid = bound["session_id"]
                ws.send_json({"type": "utterance_end", "text": "我是谁"})
                self.assertEqual(ws.receive_json(), {"type": "stt_start"})
                self.assertEqual(ws.receive_json(),
                                 {"type": "stt_result", "text": "我是谁"})
                events = self._receive_turn(ws, sid)

        self.assertIn("answer_delta", events)
        self.assertEqual(events.count("answer_delta"), 2)
        self.assertEqual(events.count("tts_start"), 2)
        self.assertEqual(events.count("tts_end"), 2)
        audio_frames = [event for event in events if isinstance(event, tuple)]
        self.assertEqual(len(audio_frames), 2)
        self.assertTrue(all(size > 0 for _tag, size in audio_frames))

        from app.agents.student_model.store import DEFAULT_STUDENT_ID
        from app.core.session import load_session
        persisted = load_session(sid)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.student_id, DEFAULT_STUDENT_ID)

    def test_pipeline_streams_text_ahead_of_slow_tts(self):
        """Synthesis must not stall the LLM stream: with slow clips every
        answer_delta is sent before the first tts_start, each tts_start +
        binary + tts_end trio stays contiguous, and turn_end follows the
        last audio frame."""
        import asyncio
        from app.voice.tts.stub import StubTTS

        original = StubTTS.synthesize

        async def slow_synthesize(provider, text, *, speed=None):
            await asyncio.sleep(0.15)
            return await original(provider, text, speed=speed)

        async def multi_sentence_turn(user_message, session, tools, llm=None,
                                      progress_cb=None, lang="zh",
                                      output_language=None, attachments=None,
                                      student_id=""):
            yield {"type": "step", "step": "thinking"}
            for i in range(6):
                yield {"type": "answer", "content": f"第{i}个要点讲解完毕。",
                       "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_pipe"}

        with patch("app.agents.chat_agent.run_turn", multi_sentence_turn), \
                patch.object(StubTTS, "synthesize", slow_synthesize):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "讲六个要点"})
                frames = []  # ("text", event) | ("audio", size)
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        frames.append(("audio", len(msg["bytes"])))
                        continue
                    if msg.get("text") is None:
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    frames.append(("text", event))
                    if event["type"] == "turn_end":
                        self.assertTrue(event["tts_ok"])
                        break

        def text_positions(etype):
            return [i for i, f in enumerate(frames)
                    if f[0] == "text" and f[1]["type"] == etype]

        deltas = text_positions("answer_delta")
        self.assertEqual(len(deltas), 6)
        # The core pipeline property: the generator was never parked behind
        # a synthesis await (the old serial loop interleaved them).
        first_tts = text_positions("tts_start")[0]
        self.assertLess(max(deltas), first_tts)

        seqs = []
        i = 0
        while i < len(frames):
            kind, payload = frames[i]
            if kind == "text" and payload["type"] == "tts_start":
                self.assertEqual(frames[i + 1][0], "audio",
                                 "binary frame must follow its tts_start")
                end = frames[i + 2]
                self.assertEqual(end[0], "text")
                self.assertEqual(end[1]["type"], "tts_end")
                self.assertEqual(end[1]["seq"], payload["seq"])
                seqs.append(payload["seq"])
                i += 3
                continue
            i += 1
        self.assertEqual(len(seqs), 6)
        self.assertEqual(len(set(seqs)), 6)
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(frames[-1][1]["type"], "turn_end")

    def test_trailing_remainder_without_terminator_is_spoken(self):
        async def trailing_turn(user_message, session, tools, llm=None,
                                progress_cb=None, lang="zh",
                                output_language=None, attachments=None,
                                student_id=""):
            yield {"type": "answer", "content": "最后一段没有句号",
                   "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_tail"}

        with patch("app.agents.chat_agent.run_turn", trailing_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "收尾"})
                events = []
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        events.append(("audio", len(msg["bytes"])))
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    events.append(event)
                    if event["type"] == "turn_end":
                        break

        starts = [e for e in events if isinstance(e, dict)
                  and e["type"] == "tts_start"]
        audio = [e for e in events if isinstance(e, tuple)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["text"], "最后一段没有句号")
        self.assertEqual(len(audio), 1)
        self.assertEqual(events[-1]["type"], "turn_end")

    def test_table_turn_speaks_cue_and_pins_board(self):
        """表格不逐格朗读：整块 markdown 走 board_table 事件，口播一句固定
        引导语，后续句子照常合成（流水线不停顿），answer_delta 原样透传。"""
        from app.voice.tts.stub import StubTTS

        table = ("| 物理量 | 单位 |\n|---|---|\n"
                 "| 力 | 牛 |\n| 功 | 焦 |\n")

        async def table_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "answer", "content": "对比表如下：\n",
                   "is_delta": True}
            yield {"type": "answer", "content": table, "is_delta": True}
            yield {"type": "answer", "content": "下一句继续讲解。",
                   "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_table"}

        speeds = []
        original = StubTTS.synthesize

        async def recording_synthesize(provider, text, *, speed=None):
            speeds.append(speed)
            return await original(provider, text, speed=speed)

        with patch("app.agents.chat_agent.run_turn", table_turn), \
                patch.object(StubTTS, "synthesize", recording_synthesize), \
                patch("app.api.v1.voice._resolve_tts_speed", return_value=0.75):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "讲对比表"})
                events = []
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        events.append(("audio", len(msg["bytes"])))
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    events.append(event)
                    if event["type"] == "turn_end":
                        self.assertTrue(event["tts_ok"])
                        break

        boards = [e for e in events if isinstance(e, dict)
                  and e["type"] == "board_table"]
        self.assertEqual(len(boards), 1)
        self.assertTrue(boards[0]["markdown"].startswith("| 物理量"))
        self.assertIn("| 力 | 牛 |", boards[0]["markdown"])
        self.assertEqual(boards[0]["hold_ms"], 7000)
        # 口播顺序：前导正文 → 引导语（表格被换掉）→ 表格后正文。
        starts = [e for e in events if isinstance(e, dict)
                  and e["type"] == "tts_start"]
        self.assertEqual([s["text"] for s in starts],
                         ["对比表如下：", "请看这个表格。", "下一句继续讲解。"])
        self.assertEqual(sum(1 for e in events if isinstance(e, tuple)), 3)
        self.assertEqual(events[-1]["type"], "turn_end")
        # 每用户语速逐片传给 provider。
        self.assertEqual(speeds, [0.75, 0.75, 0.75])

    def test_text_stream_never_parks_behind_stalled_synthesis(self):
        """合成队列绝不能把文字流和音频耦合：第一片合成挂起、12 句全部
        入队时，LLM 生成器仍被完整消费（旧的有界队列在第 8 句后就把
        生产者连同 answer_delta 一起冻结）。"""
        import asyncio
        import threading
        from app.voice.tts.stub import StubTTS

        original = StubTTS.synthesize
        release = threading.Event()
        generator_done = threading.Event()

        async def gated_synthesize(provider, text, *, speed=None):
            while not release.is_set():
                await asyncio.sleep(0.02)
            return await original(provider, text, speed=speed)

        async def twelve_sentence_turn(user_message, session, tools, llm=None,
                                       progress_cb=None, lang="zh",
                                       output_language=None, attachments=None,
                                       student_id=""):
            for i in range(12):
                yield {"type": "answer", "content": f"第{i}个要点讲解完毕。",
                       "is_delta": True}
            generator_done.set()
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_unbounded"}

        try:
            with patch("app.agents.chat_agent.run_turn", twelve_sentence_turn), \
                    patch.object(StubTTS, "synthesize", gated_synthesize):
                with self.client.websocket_connect(
                        f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                    ws.send_json({"type": "start", "session_id": None})
                    ws.receive_json()["session_id"]
                    ws.send_json({"type": "utterance_end", "text": "讲十二个要点"})
                    # 合成仍挂起时，整个生成器必须已被消费完（5 秒远高于
                    # 逐 delta 循环成本；有界队列会在这里永远停车）。
                    self.assertTrue(generator_done.wait(timeout=5),
                                    "producer parked behind stalled synthesis")
                    release.set()
                    frames = []
                    while True:
                        msg = ws.receive()
                        if msg.get("bytes") is not None:
                            frames.append(("audio", len(msg["bytes"])))
                            continue
                        if msg.get("text") is None:
                            continue
                        event = json.loads(msg["text"])
                        self.assertNotEqual(event["type"], "error")
                        frames.append(("text", event))
                        if event["type"] == "turn_end":
                            self.assertTrue(event["tts_ok"])
                            break
        finally:
            release.set()

        deltas = [i for i, f in enumerate(frames)
                  if f[0] == "text" and f[1]["type"] == "answer_delta"]
        starts = [i for i, f in enumerate(frames)
                  if f[0] == "text" and f[1]["type"] == "tts_start"]
        self.assertEqual(len(deltas), 12)
        self.assertEqual(len(starts), 12)
        # 生成器在第一片合成完成前就跑完了：全部文字先于全部音频。
        self.assertLess(max(deltas), min(starts))
        self.assertEqual(sum(1 for f in frames if f[0] == "audio"), 12)
        self.assertEqual(frames[-1][1]["type"], "turn_end")

    def test_tts_failure_keeps_text_turn_alive(self):
        from app.voice.base import VoiceProviderError

        async def failing_synthesize(_provider, text, *, speed=None):
            raise VoiceProviderError("sidecar down", code="tts_unavailable")

        with patch("app.agents.chat_agent.run_turn", self._canned_turn), \
                patch("app.voice.tts.stub.StubTTS.synthesize", failing_synthesize):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                sid = ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "我是谁"})
                events = []
                tts_ok = None
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        self.fail("a failed TTS provider must not emit audio")
                    event = json.loads(msg["text"])
                    events.append(event)
                    if event["type"] == "turn_end":
                        tts_ok = event["tts_ok"]
                        self.assertEqual(event["session_id"], sid)
                        break

        self.assertTrue(any(e["type"] == "answer_delta" for e in events))
        self.assertEqual(sum(e["type"] == "tts_error" for e in events), 1)
        self.assertFalse(tts_ok)
        self.assertFalse(any(e["type"] == "error" for e in events))

    def test_binary_audio_is_rejected_without_stt_fallback(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            self.assertEqual(ws.receive_json()["type"], "session_bound")
            ws.send_bytes(b"not an accepted input frame")
            self.assertEqual(ws.receive_json(), {
                "type": "error", "code": "binary_audio_unsupported"})

    def test_empty_text_returns_empty_transcript(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            self.assertEqual(ws.receive_json()["type"], "session_bound")
            ws.send_json({"type": "utterance_end", "text": "  "})
            self.assertEqual(ws.receive_json(), {"type": "stt_start"})
            self.assertEqual(ws.receive_json(),
                             {"type": "error", "code": "empty_transcript"})

    def test_ticket_is_single_use(self):
        ticket = self._ticket()
        with self.client.websocket_connect(f"/api/v1/voice/ws?ticket={ticket}"):
            pass
        with self.assertRaises(Exception):
            with self.client.websocket_connect(f"/api/v1/voice/ws?ticket={ticket}"):
                pass

    def test_bad_ticket_rejected(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/api/v1/voice/ws?ticket=nope"):
                pass

    def test_header_auth_skips_ticket(self):
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        user = id_store.create_user("voice@test.local", "voice",
                                    hash_password("pw123456"))
        token = create_token(user.id)
        with self.client.websocket_connect(
                "/api/v1/voice/ws",
                headers={"Authorization": f"Bearer {token}"}) as ws:
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json()["type"], "pong")

    def test_busy_rejects_overlapping_text_turn(self):
        async def pending_turn(*args, **kwargs):
            yield {"type": "answer", "content": "未完成"}
            await asyncio.sleep(0.2)

        import asyncio
        with patch("app.agents.chat_agent.run_turn", pending_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                self.assertEqual(ws.receive_json()["type"], "session_bound")
                ws.send_json({"type": "utterance_end", "text": "第一句"})
                self.assertEqual(ws.receive_json()["type"], "stt_start")
                self.assertEqual(ws.receive_json()["type"], "stt_result")
                self.assertEqual(ws.receive_json()["type"], "answer_delta")
                ws.send_json({"type": "utterance_end", "text": "第二句"})
                self.assertEqual(ws.receive_json(), {"type": "error", "code": "busy"})

    def test_foreign_session_invisible(self):
        from app.core.session import TutorSession, save_session
        other = TutorSession(grade="")
        other.student_id = "usr_somebodyelse"
        other.session_id = "sess_foreign_voice"
        save_session(other)
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": "sess_foreign_voice"})
            event = ws.receive_json()
            self.assertEqual(event["type"], "error")
            self.assertEqual(event["code"], "session_not_found")

    def test_end_control_closes(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            ws.receive()
            ws.send_json({"type": "end"})
            self.assertEqual(ws.receive_json()["type"], "bye")

    def test_tool_events_carry_card_payloads(self):
        """tool_result 必须完整透传：通话中的题目卡/知识检索卡依赖载荷，
        只有 thinking 允许留在服务端。"""
        knowledge_payload = {"status": "ok", "data": {
            "query": "勾股定理", "count": 1, "omitted_count": 0,
            "results": [{"file_id": "f1", "filename": "教材.pdf", "page": 3,
                         "excerpt": "勾股定理：a²+b²=c²"}]}}
        quiz_payload = {"status": "ok", "data": {"questions": [
            {"id": "q1", "type": "single", "stem": "1+1=?", "options": ["1", "2"]}]}}

        async def tool_turn(user_message, session, tools, llm=None,
                            progress_cb=None, lang="zh", output_language=None,
                            attachments=None, student_id=""):
            yield {"type": "tool_start", "name": "knowledge_search",
                   "args": {"query": "勾股定理"}}
            yield {"type": "tool_result", "result": knowledge_payload}
            yield {"type": "answer", "content": "查到了。", "is_delta": True}
            yield {"type": "tool_start", "name": "generate_quiz", "args": {}}
            yield {"type": "tool_result", "result": quiz_payload}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_cards"}

        with patch("app.agents.chat_agent.run_turn", tool_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()
                ws.send_json({"type": "utterance_end", "text": "出题"})
                events = self._receive_until(ws, "turn_end")

        starts = [e["name"] for e in events if isinstance(e, dict)
                  and e["type"] == "tool_start"]
        self.assertEqual(starts, ["knowledge_search", "generate_quiz"])
        results = [e["result"] for e in events if isinstance(e, dict)
                   and e["type"] == "tool_result"]
        self.assertEqual(results, [knowledge_payload, quiz_payload])

    def test_retry_event_matches_sse_naming(self):
        """retry 状态帧与 SSE 命名对齐：前端只监听 type=="retry"。"""

        async def retry_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "retry", "attempt": 1, "reason": "timeout"}
            yield {"type": "answer", "content": "好了。", "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_retry"}

        with patch("app.agents.chat_agent.run_turn", retry_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()
                ws.send_json({"type": "utterance_end", "text": "重试"})
                events = self._receive_until(ws, "turn_end")

        self.assertEqual([e for e in events if isinstance(e, dict)
                          and e["type"] == "retry"],
                         [{"type": "retry", "attempt": 1}])
        self.assertFalse(any(isinstance(e, dict) and e["type"] == "status"
                             for e in events))

    def test_end_during_turn_finishes_text_and_closes(self):
        """轮次在途时挂断（end）：音频立即停（end 后入队的句子不再合成），
        文字流跑完并落盘，客户端依次收到剩余 answer_delta → turn_end →
        bye，随后连接以 1000 关闭——聊天不会悬空在流式态。"""
        import asyncio
        import threading
        from app.voice.tts.stub import StubTTS

        release = threading.Event()
        original = StubTTS.synthesize

        async def gated_synthesize(provider, text, *, speed=None):
            while not release.is_set():
                await asyncio.sleep(0.02)
            return await original(provider, text, speed=speed)

        async def gated_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "answer", "content": "第一句讲完了。", "is_delta": True}
            # 文字流先到、合成仍挂起：此时客户端挂断。
            while not release.is_set():
                await asyncio.sleep(0.02)
            yield {"type": "answer", "content": "后面还有。", "is_delta": True}
            from app.core.session import save_session
            session.messages.append({"role": "user", "content": user_message})
            session.messages.append({"role": "assistant",
                                     "content": "第一句讲完了。后面还有。"})
            save_session(session)
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_drain"}

        try:
            with patch("app.agents.chat_agent.run_turn", gated_turn), \
                    patch.object(StubTTS, "synthesize", gated_synthesize):
                with self.client.websocket_connect(
                        f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                    ws.send_json({"type": "start", "session_id": None})
                    sid = ws.receive_json()["session_id"]
                    ws.send_json({"type": "utterance_end", "text": "讲两句"})
                    frames = []
                    while True:
                        msg = ws.receive()
                        if msg.get("bytes") is not None:
                            frames.append(("audio", len(msg["bytes"])))
                            continue
                        if msg.get("text") is None:
                            continue
                        event = json.loads(msg["text"])
                        self.assertNotEqual(event["type"], "error")
                        frames.append(("text", event))
                        if event["type"] == "answer_delta":
                            # 第一个增量已落账（文字先于挂起的音频）：挂断。
                            ws.send_json({"type": "end"})
                            release.set()
                        if event["type"] == "bye":
                            break
                    # 挂断后服务端仍写完文字并优雅收线，最后关闭连接。
                    close = ws.receive()
                    self.assertEqual(close.get("type"), "websocket.close")
                    self.assertEqual(close.get("code"), 1000)
        finally:
            release.set()

        def texts(etype):
            return [f[1] for f in frames if f[0] == "text"
                    and f[1]["type"] == etype]
        def positions(etype):
            return [i for i, f in enumerate(frames)
                    if f[0] == "text" and f[1]["type"] == etype]

        self.assertEqual(len(texts("answer_delta")), 2)
        # 只有挂断前入队的第一句合成过；end 后入队的句子被排水丢弃。
        self.assertEqual([s["text"] for s in texts("tts_start")], ["第一句讲完了。"])
        self.assertEqual(sum(1 for f in frames if f[0] == "audio"), 1)
        self.assertLess(max(positions("answer_delta")), positions("bye")[-1])
        self.assertLess(positions("turn_end")[0], positions("bye")[0])
        # 轮次完整落盘：刷新/重载后文字与卡片都在。
        from app.core.session import load_session
        persisted = load_session(sid)
        self.assertIsNotNone(persisted)
        assistant = [m for m in persisted.messages if m.get("role") == "assistant"]
        self.assertTrue(assistant)
        self.assertEqual(assistant[-1]["content"], "第一句讲完了。后面还有。")


class TestTtsSpeed(unittest.TestCase):
    """默认语速略慢 + 每用户 prefs.tts_speed 夹取（不触真实 users/）。"""

    def test_default_speed_slightly_slow(self):
        from app.core.config import settings
        self.assertEqual(settings.voice_tts_speed, 0.9)

    def test_resolve_tts_speed_clamps_and_falls_back(self):
        from types import SimpleNamespace
        from app.api.v1.voice import _resolve_tts_speed
        from app.core.config import settings

        def user_with(pref):
            return SimpleNamespace(
                profile=SimpleNamespace(prefs={"tts_speed": pref}))

        with patch("app.identity.store.get_by_id",
                   return_value=user_with(1.3)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 1.3)
        with patch("app.identity.store.get_by_id", return_value=user_with(9)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 2.0)
        with patch("app.identity.store.get_by_id",
                   return_value=user_with(0.1)):
            self.assertEqual(_resolve_tts_speed("usr_x"), 0.5)
        # 字符串 / True 等非法值与游客（无账号）都回落实例默认。
        for bad in ("fast", True, None):
            with patch("app.identity.store.get_by_id",
                       return_value=user_with(bad)):
                self.assertEqual(_resolve_tts_speed("usr_x"),
                                 settings.voice_tts_speed)
        with patch("app.identity.store.get_by_id", return_value=None):
            self.assertEqual(_resolve_tts_speed("student_default"),
                             settings.voice_tts_speed)


if __name__ == "__main__":
    unittest.main()
