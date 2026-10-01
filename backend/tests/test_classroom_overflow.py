"""Focused regressions for single-canvas overflow and math; no provider or storage I/O."""
import asyncio

from app.classroom.render.blocks import render_text
from app.classroom.render.check import run_layout_check
from app.classroom.render.compiler import compile_html
from app.schemas import classroom as sc
from tests import classroom_fixtures as fx
from tests.storage_sandbox import StorageSandboxTestCase


def dense_revision():
    text = "明确系统边界，逐一分析外力、方向和作用时间，判断近似条件是否成立。" * 12
    para = sc.ParagraphBlock(id=fx.hex_id("blk", 1), spans=[sc.SpanText(text=text)] * 4)
    formula = fx.make_formula_block(2).model_copy(update={"latex": r"$$\frac{p_1+p_2}{m_1+m_2}=v$$"})
    slide = fx.make_slide(1, layout=sc.SlideLayout.derivation, blocks=[para, formula],
                          segments=[fx.make_segment(1, block_ids=[para.id, formula.id])])
    brief = fx.make_brief().model_copy(update={"theme_id": "academic_clear@2"})
    return fx.make_revision(slides=[slide], brief=brief).model_copy(update={"renderer_version": "2.0.0"})


def focal_prose_revision(theme_id="academic_clear@2"):
    text = "\n".join([
        "判断流程：先确定研究系统，在给定时间内分析外力及其冲量，核对变量、单位和适用条件，再判断应当使用哪一种方程。",
        "1. 确定系统边界",
        r"2. 比较 $\sum\vec{F}_{\mathrm{ext}}$ 和外力冲量",
        r"3. 明确 $\Delta t$ 的取值与物理意义，并核对近似条件",
        "4. 合外力冲量可以忽略时采用守恒关系",
        "5. 外力冲量不可忽略时采用动量定理",
        "6. 根据测量结果检验方程是否成立，并重新判断条件",
        "1 → 2：系统确定后",
        "2 → 3：分析时间",
        "3 → 4：冲量可忽略",
        "3 → 5：冲量不可忽略",
        "4 → 6；5 → 6",
    ])
    paragraph = fx.make_para_block(1, text)
    bullets = sc.BulletsBlock(id=fx.hex_id("blk", 2), items=[
        [sc.SpanText(text=point)] for point in (
            "明确定义并写出适用条件", "先分析系统所受外力冲量", "将结论与系统边界相互核对")])
    callout = sc.CalloutBlock(id=fx.hex_id("blk", 3), spans=[sc.SpanText(
        text="系统边界、作用时间与近似条件必须对应同一事件，检验成立后再使用相应的方程。")])
    slide = fx.make_slide(1, title="用条件判断守恒关系", layout=sc.SlideLayout.summary,
                          blocks=[paragraph, bullets, callout],
                          segments=[fx.make_segment(1, block_ids=[paragraph.id, bullets.id, callout.id])])
    slide.composition = sc.SlideComposition(mode="sidebar", focal_block_id=paragraph.id,
                                            emphasis_block_id=callout.id)
    brief = fx.make_brief().model_copy(update={"theme_id": theme_id})
    return fx.make_revision(slides=[slide], brief=brief).model_copy(update={"renderer_version": "2.0.0"})


class OverflowTests(StorageSandboxTestCase):
    def test_multiline_focal_prose_fits_without_changing_content(self):
        for theme in ("academic_clear@2", "chalk_focus@2"):
            with self.subTest(theme=theme):
                revision = focal_prose_revision(theme)
                original = revision.model_dump_json()
                report = asyncio.run(run_layout_check(compile_html(revision)))
                self.assertTrue(report.ok, report.overflow_issue_summary())
                self.assertEqual({(v["width"], v["height"]) for v in report.viewports},
                                 {(1280, 720), (960, 540)})
                self.assertEqual(revision.model_dump_json(), original)
                self.assertEqual(len(revision.slides), 1)

    def test_explicit_math_in_text_is_escaped_and_recognized(self):
        html = render_text(r"<script>条件 \(x^2\) 与 $y_1$，价格 $5 和 $10")
        self.assertNotIn("<script>", html)
        self.assertEqual(html.count("data-katex="), 2)
        self.assertIn("价格 $5 和 $10", html)

    def test_dense_page_is_reported_without_mutating_content(self):
        revision = dense_revision()
        original = revision.model_dump_json()
        report = asyncio.run(run_layout_check(compile_html(revision)))
        self.assertFalse(report.ok)
        self.assertTrue(any(i["code"] == "vertical_overflow" for i in report.issues))
        self.assertEqual(revision.model_dump_json(), original)
        self.assertEqual(len(revision.slides), 1)

    def test_unrendered_tex_is_reported_on_noninitial_page(self):
        revision = dense_revision()
        first = fx.make_slide(1)
        second = fx.make_slide(2, layout=sc.SlideLayout.derivation,
            blocks=[fx.make_formula_block(8).model_copy(update={"latex": r"\notARealCommand{x}"})])
        revision = revision.model_copy(update={"slides": [first, second]})
        report = asyncio.run(run_layout_check(compile_html(revision)))
        self.assertFalse(report.ok)
        self.assertTrue(any(issue["code"] == "katex_fallback" and issue["slide_order"] == 2
                            for issue in report.issues))
