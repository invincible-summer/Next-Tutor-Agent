"""P8 文本层保真度分级器（core/text_quality）单元测试。

取证样本按 2026-08-26 对 chat_history/library/data/public 27 卷的实际
乱码模式合成（全角公式/ꎬ/PUA 音标/犃犅 替换/矩阵竖排），阈值标定见
模块常量注释。纯函数测试，不落盘。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core import text_quality as tq  # noqa: E402


def _pua(n: int) -> str:
    return "".join(chr(0xE000 + (i % 0x100)) for i in range(n))


class TestClassifyPage(unittest.TestCase):
    def test_good_prose_is_good(self):
        text = "卷积神经网络是一类具有局部连接和权重共享特性的神经网络。" * 10
        self.assertEqual(tq.classify_page(text), "good")

    def test_poetry_short_lines_not_corrupt(self):
        # 语文诗歌短行：无乱码字符证据时绝不误判（孤立短行单独不构成 corrupt）。
        poem = "独立寒秋，\n湘江北去，\n沙洲头。\n看万山红遍，\n层林尽染；\n漫江碧透，\n百舸争流。"
        self.assertEqual(tq.classify_page(poem), "good")

    def test_sparse_and_empty(self):
        self.assertEqual(tq.classify_page(""), "empty")
        self.assertEqual(tq.classify_page("   \n  "), "empty")
        self.assertEqual(tq.classify_page("短文本"), "sparse")

    def test_linear_algebra_fullwidth_garble(self):
        # 线性代数型：全角公式 + ꎬ（Saurashtra 区逗号）+ PUA 省略号。
        text = ("ａ１１ｘ１＋ａ１２ｘ２＝ｂ１ꎬａ２１ｘ１＋ａ２２ｘ２＝ｂ２ꎬ"
                + chr(0xE03A) * 3 + "正常中文夹在公式之间" * 5)
        self.assertEqual(tq.classify_page(text), "corrupt")

    def test_pep_math_font_substitution(self):
        # 必修2 型：犃犅 替代斜体 A/B（取证 11,530 处/卷）。
        text = "小船位移的大小是犃，犅两地之间的距离１５ｎｍｉｌｅ，求犃犅两地距离。" * 3
        self.assertEqual(tq.classify_page(text), "corrupt")

    def test_english_ipa_glossary(self):
        # 英语词表型：音标全部落 PUA（取证 3,643 行乱码音标）。
        lines = [f"word{i:02d} /{_pua(4)}/ n. 释义词{i}" for i in range(30)]
        self.assertEqual(tq.classify_page("\n".join(lines)), "corrupt")

    def test_matrix_vertical_explosion(self):
        # 矩阵被逐字炸成竖排（每行 ≤2 字符）+ 少量全角证据。
        text = "\n".join(["ａ１１ｘ１＋ａ１２ｘ２＝ｂ１", "１", "⋱", "１", "ｋ",
                          "１", "⋱", "１", "æ", "è", "ç", "ö", "ø", "÷", "第ｉ行."]
                        + ["正常句子补充内容较长一些" for _ in range(3)])
        self.assertEqual(tq.classify_page(text), "corrupt")

    def test_occasional_fullwidth_not_corrupt(self):
        # 排版偶用几个全角数字（低于 8 个 + 0.2% 双门槛）不误判。
        text = "本章介绍基本概念。（１）概述（２）方法（３）小结。" + "正常正文内容。" * 50
        self.assertEqual(tq.classify_page(text), "good")


class TestRoutingHelpers(unittest.TestCase):
    def test_pages_needing_ocr_targets_sparse_empty_corrupt(self):
        from app.core.pdf_ocr import pages_needing_ocr
        good = "卷积神经网络是具有局部连接和权重共享特性的神经网络模型。" * 5
        corrupt = "ａ１１ｘ１＋ａ１２ｘ２＝ｂ１ꎬ" * 4 + "正常中文" * 5
        pages = [good, "", "短", corrupt, good]
        self.assertEqual(pages_needing_ocr(pages), [1, 2, 3])

    def test_sparse_page_indices_unchanged(self):
        # 向后兼容：稀疏判定入口语义不变。
        from app.core.pdf_ocr import sparse_page_indices
        self.assertEqual(sparse_page_indices(["abc" * 20, "", "短", "x" * 50]), [1, 2])

    def test_summarize_pages_stats(self):
        good = "正常教材正文内容，包含足够的字符数量。" * 10
        corrupt = "犃犅犖犿狀" * 20
        summary = tq.summarize_pages([good, corrupt, "", "短"])
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["good"], 1)
        self.assertEqual(summary["corrupt"], 1)
        self.assertEqual(summary["empty"], 1)
        self.assertEqual(summary["sparse"], 1)
        self.assertGreater(summary["garble_rate"], 0)

    def test_mixed_ocr_accepts_external_indices(self):
        # ocr_indices 参数化：外部 verdict 路由（P8 重建通道）传入目标页。
        import asyncio
        from app.core import pdf_ocr
        from tests.agents.knowledge.test_pdf_ocr import _make_pdf

        raw = _make_pdf(["dense ascii content for page one " * 8,
                         "dense ascii content for page two " * 8])

        async def fake_ocr(png: bytes) -> str:
            return "OCR乱码页"

        pages, stats = asyncio.run(pdf_ocr.ocr_pdf_pages_mixed(
            raw, fake_ocr, ocr_indices=[1]))
        self.assertEqual(stats["ocr_done"], 1)
        self.assertIn("dense ascii", pages[0])   # 未指定页保留文本层
        self.assertEqual(pages[1], "OCR乱码页")   # 外部指定页被 OCR


if __name__ == "__main__":
    unittest.main()
