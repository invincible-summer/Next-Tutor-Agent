"""原生 PDF 图表/印刷页码收割（figure_harvest）契约。

确定性部分（find_tables 表格、位图区域、page label）零 LLM；图述阶段复用
多模态通道且失败即弃（宁缺毋滥）；merge 保持页序与 hash 稳定。
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import figure_harvest


def _pdf_with_image_and_label(*, label: str | None = None) -> bytes:
    """文本层页 + 一张中等尺寸位图（≈11% 页面积）+ 可选 page label。"""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "第一章 力学基础")
    page.insert_text((72, 110), "正文段落，文本层良好。")
    img = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 150))
    img.clear_with(128)
    page.insert_image(fitz.Rect(72, 150, 350, 350), pixmap=img)
    if label:
        # label="iv" 等非数字走罗马 style，数字走十进制 style
        style = "D" if label.isdigit() else "r"
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": style,
                              "firstpagenum": int(label) if label.isdigit() else 1}])
    raw = doc.tobytes()
    doc.close()
    return raw


class TestFigureHarvestSync(unittest.TestCase):
    def test_image_region_harvested_and_filtered(self):
        raw = _pdf_with_image_and_label()
        harvested = figure_harvest.harvest_native_blocks_sync(raw)
        self.assertIn(1, harvested)
        entry = harvested[1]
        self.assertEqual(len(entry.get("figure_pngs") or []), 1)
        self.assertGreater(len(entry["figure_pngs"][0]), 100)

    def test_page_label_numeric_only(self):
        raw = _pdf_with_image_and_label(label="112")
        harvested = figure_harvest.harvest_native_blocks_sync(raw)
        self.assertEqual(harvested[1].get("label"), 112)

    def test_non_numeric_label_ignored(self):
        raw = _pdf_with_image_and_label(label="iv")
        harvested = figure_harvest.harvest_native_blocks_sync(raw)
        self.assertNotIn("label", harvested[1])

    def test_tiny_images_skipped(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 90), "正文")
        img = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
        img.clear_with(200)
        page.insert_image(fitz.Rect(72, 120, 92, 140), pixmap=img)  # 20pt 图标
        raw = doc.tobytes()
        doc.close()
        harvested = figure_harvest.harvest_native_blocks_sync(raw)
        self.assertNotIn("figure_pngs", harvested.get(1, {}))

    def test_bad_pdf_returns_empty(self):
        self.assertEqual(figure_harvest.harvest_native_blocks_sync(b"not pdf"), {})


class TestHarvestNativeBlocksEndToEnd(unittest.TestCase):
    def test_full_path_with_fake_descriptions(self):
        raw = _pdf_with_image_and_label(label="112")

        async def fake_describe(pngs):
            return ["图述：图中为斜面模型示意。"] * len(pngs)

        with patch.object(figure_harvest, "_describe_figures", fake_describe):
            out = asyncio.run(figure_harvest.harvest_native_blocks(raw))
        self.assertIn(1, out)
        entry = out[1]
        self.assertEqual(entry["label"], 112)
        self.assertEqual(len(entry["blocks"]), 1)
        self.assertTrue(entry["blocks"][0].startswith("[图|插图]"))
        self.assertIn("图述：", entry["blocks"][0])

    def test_no_description_drops_figure_but_keeps_label(self):
        raw = _pdf_with_image_and_label(label="12")

        async def fake_describe(pngs):
            return [""] * len(pngs)

        with patch.object(figure_harvest, "_describe_figures", fake_describe):
            out = asyncio.run(figure_harvest.harvest_native_blocks(raw))
        self.assertEqual(out[1]["blocks"], [])
        self.assertEqual(out[1]["label"], 12)


class TestMergeHarvest(unittest.TestCase):
    def test_merge_inserts_label_and_blocks_per_page(self):
        text = "第一页正文。\f第二页正文。"
        harvested = {
            1: {"label": 5, "blocks": ["[表|表格]\n| a | b |"]},
            2: {"blocks": ["[图|插图]\n图述：示意图。"]},
        }
        merged = figure_harvest.merge_harvest_into_text(text, harvested)
        pages = merged.split("\f")
        self.assertTrue(pages[0].startswith("[页码=5]"))
        self.assertIn("| a | b |", pages[0])
        self.assertIn("图述：示意图。", pages[1])
        self.assertNotIn("[页码=", pages[1])

    def test_merge_empty_is_identity(self):
        text = "正文"
        self.assertEqual(figure_harvest.merge_harvest_into_text(text, {}), text)
        self.assertEqual(figure_harvest.merge_harvest_into_text(text, {9: {"label": 1}}), text)

    def test_merge_out_of_range_pages_ignored(self):
        text = "单页"
        merged = figure_harvest.merge_harvest_into_text(
            text, {3: {"blocks": ["[图|插图]\n图述：x"]}})
        self.assertEqual(merged, "单页")


class TestTableMarkdown(unittest.TestCase):
    def test_fallback_rows_when_no_markdown_api(self):
        class FakeTable:
            def to_markdown(self):
                raise AttributeError("no api")

            def extract(self):
                return [["函数", "导数"], ["x^n", "nx^(n-1)"], [None, ""]]

        md = figure_harvest._table_markdown(FakeTable())
        self.assertIn("| 函数 | 导数 |", md)
        self.assertIn("nx^(n-1)", md)


if __name__ == "__main__":
    unittest.main()
