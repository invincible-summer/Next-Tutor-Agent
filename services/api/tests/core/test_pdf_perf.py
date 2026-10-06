"""PDF backend performance guardrails (ADR-0015, plan acceptance 5.12).

Synthetic 20/100/300-page corpora (1000-page behind ``PDF_PERF_HEAVY=1``).
Guards against order-of-magnitude regressions versus the legacy engine —
absolute budgets are deliberately generous wall-clock bounds so the suite
stays stable on shared CI runners; a real regression investigation starts
from these numbers, never from swapping in a second engine.
"""
from __future__ import annotations

import os
import resource
import time
import unittest

from tests.support import pdf_fixtures as fixtures

_HEAVY = os.getenv("PDF_PERF_HEAVY") == "1"


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _make_pages(n: int) -> list[str]:
    line = ("本章讲解导数与积分的基本概念，包含定义、几何意义与典型例题。"
            "Derivative and integral basics with worked examples. ")
    return [line * 8 for _ in range(n)]


class TestExtractionPerformance(unittest.TestCase):
    def test_text_extraction_budgets(self):
        from app.core import pdf
        for n in (20, 100, 300):
            raw = fixtures.make_pdf(_make_pages(n))
            start = time.perf_counter()
            pages = pdf.page_texts(raw)
            elapsed = time.perf_counter() - start
            self.assertEqual(len(pages), n)
            # pypdf 纯文本抽取 ~ms/页；预算按 40ms/页 + 1s 常量留足 CI 抖动
            self.assertLess(elapsed, 1.0 + 0.04 * n,
                            f"{n}-page extraction took {elapsed:.2f}s")

    def test_page_count_budget(self):
        from app.core import pdf
        raw = fixtures.make_pdf(_make_pages(100))
        start = time.perf_counter()
        self.assertEqual(pdf.page_count(raw), 100)
        self.assertLess(time.perf_counter() - start, 2.0)

    @unittest.skipUnless(_HEAVY, "set PDF_PERF_HEAVY=1 for the 1000-page corpus")
    def test_heavy_1000_pages(self):
        from app.core import pdf
        raw = fixtures.make_pdf(_make_pages(1000))
        start = time.perf_counter()
        self.assertEqual(len(pdf.page_texts(raw)), 1000)
        self.assertLess(time.perf_counter() - start, 45.0)


class TestRenderPerformance(unittest.TestCase):
    def test_single_page_render_p50_p95(self):
        from app.core import pdf
        raw = fixtures.make_pdf(_make_pages(20))
        timings: list[float] = []
        for index in range(30):
            start = time.perf_counter()
            self.assertIsNotNone(pdf.render_page_png(raw, index % 20, dpi=200))
            timings.append(time.perf_counter() - start)
        timings.sort()
        p50 = timings[len(timings) // 2]
        p95 = timings[int(len(timings) * 0.95) - 1]
        self.assertLess(p50, 1.5, f"render p50 {p50:.3f}s")
        self.assertLess(p95, 4.0, f"render p95 {p95:.3f}s")

    def test_fd_budget_across_many_documents(self):
        """反复打开/关闭文档后 fd 不泄漏（渲染 200 次后 fd 增量有界）。"""
        from app.core import pdf

        def _fd_count() -> int:
            return len(os.listdir("/proc/self/fd"))

        raw = fixtures.make_pdf(_make_pages(4))
        _fd_count()
        base = _fd_count()
        for i in range(200):
            self.assertIsNotNone(pdf.render_page_png(raw, i % 4, dpi=72))
        self.assertLessEqual(_fd_count() - base, 16)

    def test_peak_rss_bounded_for_300_pages(self):
        from app.core import pdf
        raw = fixtures.make_pdf(_make_pages(300))
        pdf.page_texts(raw)
        pdf.render_page_png(raw, 0, dpi=200)
        self.assertLess(_peak_rss_mb(), 1200.0)


if __name__ == "__main__":
    unittest.main()
