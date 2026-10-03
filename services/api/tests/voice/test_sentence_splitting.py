"""Voice: sentence splitting and speech cut cleanup."""
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
