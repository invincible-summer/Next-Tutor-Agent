"""Permissive PDF backend contract tests (ADR-0015).

Covers the ``app.core.pdf`` facade end to end on synthetic fixtures:
page count / per-page text + ``\\f`` boundary, outline, page labels,
render, table harvest, figure regions + crops, corrupt/encrypted inputs
and the PDFium mutex serialization discipline.
"""
from __future__ import annotations

import threading
import time
import unittest

from tests.support import pdf_fixtures as fixtures


class TestPageCountAndText(unittest.TestCase):
    def test_count_and_per_page_text(self):
        from app.core import pdf
        raw = fixtures.make_pdf(["alpha page", "beta page", "gamma page"])
        self.assertEqual(pdf.page_count(raw), 3)
        pages = pdf.page_texts(raw)
        self.assertEqual(len(pages), 3)
        joined = "\f".join(pages)
        self.assertEqual(len(joined.split("\f")), 3)  # 页边界契约
        self.assertIn("alpha", pages[0])
        self.assertIn("gamma", pages[2])

    def test_blank_pdf_empty_text_layer(self):
        from app.core import pdf
        raw = fixtures.blank_pdf(2)
        self.assertEqual(pdf.page_count(raw), 2)
        self.assertEqual(pdf.page_texts(raw), ["", ""])

    def test_corrupt_pdf_degrades_cleanly(self):
        from app.core import pdf
        junk = b"%PDF-1.7 junk without structure"
        self.assertEqual(pdf.page_count(b"not a pdf at all"), 0)
        self.assertEqual(pdf.page_texts(junk), [])
        self.assertEqual(pdf.outline(junk), [])
        self.assertEqual(pdf.page_labels(junk), [])
        self.assertIsNone(pdf.render_page_png(junk, 0))
        self.assertEqual(pdf.parse_failure_reason(b"not a pdf at all"), "pdf_corrupt")

    def test_encrypted_pdf_reports_reason(self):
        from app.core import pdf
        # 构造一个带空用户密码以外加密的 PDF 成本高；用 pypdf 写密码件
        from pypdf import PdfReader, PdfWriter
        raw = fixtures.make_pdf(["secret page"])
        writer = PdfWriter()
        writer.append(PdfReader(__import__("io").BytesIO(raw)))
        out = __import__("io").BytesIO()
        writer.encrypt(user_password="pw", owner_password="pw")
        writer.write(out)
        reason = pdf.parse_failure_reason(out.getvalue())
        self.assertIn(reason, (None, "pdf_encrypted", "pdf_corrupt"))

    def test_out_of_range_render_is_none(self):
        from app.core import pdf
        raw = fixtures.make_pdf(["only page"])
        self.assertIsNone(pdf.render_page_png(raw, 1))
        self.assertIsNone(pdf.render_page_png(raw, -1))


class TestOutlineAndLabels(unittest.TestCase):
    def test_outline_roundtrip_levels(self):
        from app.core import pdf
        raw = fixtures.make_pdf(["p1", "p2", "p3"])
        raw = fixtures.with_outline(raw, [
            (1, "第一章", 1), (2, "第一节", 2), (1, "第二章", 3)])
        entries = pdf.outline(raw)
        self.assertEqual([e.as_row() for e in entries], [
            (1, "第一章", 1), (2, "第一节", 2), (1, "第二章", 3)])

    def test_outline_absent_is_empty(self):
        from app.core import pdf
        self.assertEqual(pdf.outline(fixtures.make_pdf(["x"])), [])

    def test_labels_absent_are_blank_rows(self):
        from app.core import pdf
        self.assertEqual(pdf.page_labels(fixtures.make_pdf(["a", "b"])), ["", ""])

    def test_numeric_and_roman_labels(self):
        from app.core import pdf
        raw = fixtures.make_pdf(["a", "b", "c"])
        decimal = fixtures.with_page_labels(raw, style="D", first_page_num=112)
        self.assertEqual(pdf.page_labels(decimal), ["112", "113", "114"])
        roman = fixtures.with_page_labels(raw, style="r", first_page_num=4)
        self.assertEqual(pdf.page_labels(roman), ["iv", "v", "vi"])


class TestRender(unittest.TestCase):
    def test_render_png_signature_and_size(self):
        from app.core import pdf
        raw = fixtures.make_pdf(["render me"])
        png = pdf.render_page_png(raw, 0, dpi=100)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        from PIL import Image
        import io
        image = Image.open(io.BytesIO(png))
        # A4 595×842pt @100dpi → 826×1170 px（±1px 容差）
        self.assertAlmostEqual(image.size[0], 826, delta=1)
        self.assertAlmostEqual(image.size[1], 1170, delta=1)


class TestTablesAndFigures(unittest.TestCase):
    def test_table_rows_harvested(self):
        from app.core import pdf
        raw = fixtures.table_pdf([["函数", "导数"], ["x^n", "nx^(n-1)"], ["sin x", "cos x"]])
        blocks = pdf.harvest_tables(raw)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].page_1based, 1)
        self.assertEqual(blocks[0].rows[0], ["函数", "导数"])
        self.assertEqual(blocks[0].n_rows, 3)

    def test_figure_regions_and_crops(self):
        from app.core import pdf
        raw = fixtures.page_with_image(["正文"], image_rect=(72, 150, 350, 350))
        regions = pdf.harvest_figure_regions(raw)
        self.assertEqual(len(regions), 1)
        self.assertAlmostEqual(regions[0].width_pt, 278, delta=1)
        self.assertAlmostEqual(regions[0].height_pt, 200, delta=1)
        crops = pdf.render_figure_crops(raw, regions, dpi=200)
        self.assertIn(0, crops)
        from PIL import Image
        import io
        image = Image.open(io.BytesIO(crops[0].png))
        # 278pt×200pt @200dpi → 772×556 px（±2px 容差）
        self.assertAlmostEqual(image.size[0], 772, delta=2)
        self.assertAlmostEqual(image.size[1], 556, delta=2)

    def test_duplicated_placements_deduped(self):
        from app.core import pdf
        raw = fixtures.page_with_image(["正文", "更多正文"], image_rect=(72, 150, 350, 350))
        regions = pdf.harvest_figure_regions(raw)
        self.assertLessEqual(len(regions), 1)

    def test_corrupt_inputs_return_empty(self):
        from app.core import pdf
        self.assertEqual(pdf.harvest_tables(b"junk"), [])
        self.assertEqual(pdf.harvest_figure_regions(b"junk"), [])
        self.assertEqual(pdf.render_figure_crops(b"junk", []), {})


class TestPdfiumMutexDiscipline(unittest.TestCase):
    """PDFium 全局锁：持锁期间其它渲染必须阻塞（防 SIGSEGV 回归）。"""

    def test_render_blocks_while_lock_held_elsewhere(self):
        from app.core.pdf import PDFIUM_LOCK, render_page_png
        raw = fixtures.make_pdf(["page one", "page two"])
        done = threading.Event()
        out: list[bytes | None] = []

        def target():
            out.append(render_page_png(raw, 0))
            done.set()

        with PDFIUM_LOCK:
            t = threading.Thread(target=target)
            t.start()
            time.sleep(0.05)
            self.assertFalse(done.is_set())
        t.join(timeout=5)
        self.assertTrue(done.is_set())
        self.assertTrue(out and out[0].startswith(b"\x89PNG\r\n\x1a\n"))

    def test_stress_concurrent_renders(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.core.pdf import render_page_png
        raw = fixtures.make_pdf([f"page {i}" for i in range(6)])
        with ThreadPoolExecutor(max_workers=12) as pool:
            pngs = list(pool.map(lambda i: render_page_png(raw, i % 6), range(36)))
        self.assertTrue(all(p and p.startswith(b"\x89PNG\r\n\x1a\n") for p in pngs))


if __name__ == "__main__":
    unittest.main()
