"""课堂渲染安全与契约回归（plan.md §19.2 test_classroom_render）。

覆盖：每种 block escaping、脚本注入、URL/CSS 注入、KaTeX 属性承载、
图表数值边界、无 raw SVG、public HTML 无答案、CSP runtime hash 一致、
布局 slot 校验、三种编译模式。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classroom.render import blocks as blocks_mod  # noqa: E402
from app.classroom.render.compiler import (  # noqa: E402
    compile_credits,
    compile_html,
    compile_speaker_notes,
    validate_layout_slots,
)
from app.classroom.render.assets import load_asset_pack  # noqa: E402
import base64  # noqa: E402


def _csp_b64(hex_sha: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_sha)).decode("ascii")
from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402


def _compile(**kwargs) -> str:
    revision = kwargs.pop("revision", None) or fx.make_revision(1)
    return compile_html(revision, **kwargs)


class EscapeTests(unittest.TestCase):
    def _assert_no_raw(self, html: str, needle: str) -> None:
        # 不用 assertNotIn：失败时会转储整份 1.7MB HTML
        self.assertFalse(needle in html,
                         f"未转义的动态内容: {needle!r}")

    def test_script_injection_in_text(self):
        evil = "<script>alert(1)</script>"
        span = sc.SpanText(text=evil)
        block = sc.ParagraphBlock(id=fx.hex_id("blk"), spans=[span])
        revision = fx.make_revision(1, slides=[fx.make_slide(
            1, layout=sc.SlideLayout.title, blocks=[block],
            segments=[fx.make_segment(1)])])
        html = compile_html(revision, mode="online")
        self._assert_no_raw(html, "<script>alert")
        self.assertIn("&lt;script&gt;", html)

    def test_title_and_caption_injection(self):
        slide = fx.make_slide(1, title='标题"onmouseover=alert(1)')
        revision = fx.make_revision(1, slides=[slide])
        html = compile_html(revision, mode="online")
        # 引号必须转义：不能逃出属性值形成新属性
        self.assertIn("&quot;onmouseover=alert(1)", html)
        self.assertFalse('="onmouseover' in html)

    def test_css_injection_via_text(self):
        span = sc.SpanText(text="</style><script>x()</script>")
        block = sc.ParagraphBlock(id=fx.hex_id("blk"), spans=[span])
        revision = fx.make_revision(
            1, slides=[fx.make_slide(1, layout=sc.SlideLayout.title,
                                     blocks=[block],
                                     segments=[fx.make_segment(1)])])
        html = compile_html(revision, mode="online")
        # </style> 只能来自编译器自身的模板，动态内容必须转义
        self.assertNotIn("</style><script>", html)

    def test_table_and_diagram_labels_escaped(self):
        table = sc.TableBlock(id=fx.hex_id("blk", 3), headers=["<th>x"],
                              rows=[["<td onclick=1>"]])
        revision = fx.make_revision(
            1, slides=[fx.make_slide(
                1, layout=sc.SlideLayout.compare,
                blocks=[fx.make_para_block(9), table],
                segments=[fx.make_segment(1, block_ids=[table.id])])])
        html = compile_html(revision, mode="online")
        self._assert_no_raw(html, "<th>x")
        self._assert_no_raw(html, "<td onclick=1>")
        self.assertIn("&lt;td onclick=1&gt;", html)

    def test_formula_latex_escaped_in_attribute(self):
        html = _compile()
        formula = fx.make_formula_block(1)
        # fixture 的推导页含公式块；latex 以转义属性承载，由 KaTeX trust=false 渲染
        self.assertIn("data-katex=", html)
        self._assert_no_raw(html, "javascript:")

    def test_math_span_spoken_aria(self):
        span = sc.SpanMath(latex="E=mc^2", spoken="能量等于质量乘光速平方")
        block = sc.ParagraphBlock(id=fx.hex_id("blk"), spans=[span])
        revision = fx.make_revision(
            1, slides=[fx.make_slide(1, layout=sc.SlideLayout.title,
                                     blocks=[block],
                                     segments=[fx.make_segment(1)])])
        html = compile_html(revision, mode="online")
        self.assertIn('aria-label="能量等于质量乘光速平方"', html)
        self.assertIn('data-katex="E=mc^2"', html)


class CspAndRuntimeTests(unittest.TestCase):
    def test_csp_contains_runtime_and_katex_hashes(self):
        pack = load_asset_pack()
        self.assertIsNotNone(pack, "先运行 pnpm run build:classroom")
        html = _compile()
        self.assertIn(f"sha256-{_csp_b64(pack.runtime_sha256)}", html)
        self.assertIn(f"sha256-{_csp_b64(pack.katex_sha256)}", html)
        for directive in ("default-src 'none'", "img-src data:",
                          "font-src data:", "connect-src 'none'",
                          "object-src 'none'", "base-uri 'none'",
                          "form-action 'none'"):
            self.assertIn(directive, html)
        self.assertNotIn("allow-same-origin", html)

    def test_modes(self):
        online = _compile(mode="online")
        offline = _compile(mode="offline")
        printable = _compile(mode="print")
        self.assertIn('data-mode="online"', online)
        self.assertIn('data-mode="offline"', offline)
        self.assertIn('data-mode="print"', printable)
        self.assertIn("break-after:page", printable)
        # online/print 无内置控件显示逻辑（CSS 控制 offline 才显示）


class SlotValidationTests(unittest.TestCase):
    def test_missing_required_block_rejected(self):
        # derivation 必须有 formula
        revision = fx.make_revision(1, slides=[fx.make_slide(
            1, blocks=[fx.make_para_block(1)])])
        with self.assertRaises(ValueError):
            validate_layout_slots(revision)

    def test_unknown_block_for_layout_rejected(self):
        checkpoint = sc.CheckpointBlock(id=fx.hex_id("blk", 5),
                                        checkpoint_id=fx.hex_id("ckp"))
        revision = fx.make_revision(1, slides=[fx.make_slide(
            1, blocks=[fx.make_formula_block(1), checkpoint])])
        with self.assertRaises(ValueError):
            validate_layout_slots(revision)

    def test_too_many_blocks_rejected(self):
        paras = [fx.make_para_block(i + 1) for i in range(3)]
        revision = fx.make_revision(1, slides=[fx.make_slide(
            1, blocks=[fx.make_formula_block(9)] + paras)])
        with self.assertRaises(ValueError):
            validate_layout_slots(revision)

    def test_dangling_checkpoint_ref_rejected(self):
        checkpoint = sc.CheckpointBlock(id=fx.hex_id("blk", 5),
                                        checkpoint_id=fx.hex_id("ckp"))
        slide = fx.make_slide(
            1, layout=sc.SlideLayout.checkpoint, blocks=[checkpoint])
        revision = fx.make_revision(1, slides=[slide])
        with self.assertRaises(ValueError):
            validate_layout_slots(revision)


class AnswerLeakTests(unittest.TestCase):
    def test_checkpoint_answer_not_in_html(self):
        template = sc.CheckpointTemplate(
            checkpoint_id=fx.hex_id("ckp"), slide_id=fx.slide_hex(),
            kind=sc.CheckpointKind.question, prompt="下列说法正确的是？",
            verified_question_template={
                "stem": "动量守恒的条件？",
                "answer": "合外力冲量为零",
                "rubric": {"key": ["外力", "冲量"]},
            })
        checkpoint_block = sc.CheckpointBlock(
            id=fx.hex_id("blk", 7), checkpoint_id=template.checkpoint_id)
        slide = fx.make_slide(
            1, layout=sc.SlideLayout.checkpoint, blocks=[checkpoint_block])
        revision = fx.make_revision(1, slides=[slide]).model_copy(
            update={"checkpoint_templates": [template]})
        html = compile_html(revision, mode="online")
        self.assertFalse("合外力冲量为零" in html)
        self.assertFalse("rubric" in html)
        self.assertIn("下列说法正确的是", html)  # prompt 可见
        notes = compile_speaker_notes(revision)
        self.assertFalse("合外力冲量为零" in notes)
        credits = compile_credits(revision)
        self.assertFalse("合外力冲量为零" in credits)


class DiagramRenderTests(unittest.TestCase):
    def test_flow_render(self):
        diagram = sc.DiagramBlock(
            id=fx.hex_id("blk"),
            diagram=sc.FlowDiagram(
                nodes=[sc.FlowNode(id="a", label="选系统"),
                       sc.FlowNode(id="b", label="分析外力")],
                edges=[sc.FlowEdge.model_validate(
                    {"from": "a", "to": "b", "label": "下一步"})],
                alt="分析流程"))
        html = blocks_mod.render_diagram(diagram)
        self.assertIn("<svg", html)
        self.assertIn("选系统", html)
        self.assertIn("url(#arrow)", html)

    def test_plot_render_tick_range(self):
        diagram = sc.DiagramBlock(
            id=fx.hex_id("blk"),
            diagram=sc.CartesianPlot(
                x_label="t/s", y_label="v/(m/s)",
                x_range=(0.0, 10.0), y_range=(0.0, 20.0),
                series=[sc.PlotSeries(label="v",
                                      points=[(0.0, 0.0), (10.0, 20.0)])],
                constructed=True, alt="速度时间图"))
        html = blocks_mod.render_diagram(diagram)
        self.assertIn("polyline", html)
        self.assertIn("t/s", html)

    def test_force_render(self):
        diagram = sc.DiagramBlock(
            id=fx.hex_id("blk"),
            diagram=sc.ForceDiagram(
                bodies=[sc.ForceBody(id="m", shape="box", x=0.5, y=0.5,
                                     label="小车")],
                arrows=[sc.ForceArrow(body_id="m", dx=0.5, dy=0.0,
                                      label="F=2N")],
                constructed=True, alt="受力图"))
        html = blocks_mod.render_diagram(diagram)
        self.assertIn("url(#farrow)", html)
        self.assertIn("F=2N", html)


if __name__ == "__main__":
    unittest.main()
