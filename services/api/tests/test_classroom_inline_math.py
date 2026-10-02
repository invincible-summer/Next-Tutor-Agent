"""Math in prose and diagrams, with storage redirected by the shared sandbox."""
import html

from app.classroom.render.blocks import render_text, render_block
from app.classroom.render.compiler import compile_html
from app.classroom.render.inline_math import math_parts
from app.schemas import classroom as sc
from tests import classroom_fixtures as fx
from tests.storage_sandbox import StorageSandboxTestCase


def inline_math_revision():
    def text(value):
        return [sc.SpanText(text=value)]
    pages = [
        [fx.make_para_block(1, r'小写 \delta，大写 \Delta，三角形 \triangle，梯度 \nabla。'),
         sc.BulletsBlock(id=fx.hex_id('blk', 2), items=[text(r'变化量 \Delta t；角度 \theta。')]),
         sc.CalloutBlock(id=fx.hex_id('blk', 3), spans=[sc.SpanEmphasis(text=r'误差 \delta x；面积 $S=\pi r^2$。')])],
        [sc.StepsBlock(id=fx.hex_id('blk', 4), steps=[sc.StepItem(label=r'计算 \Delta t',
             spans=text(r'平均速度 \frac{\Delta x}{\Delta t}；根式 \sqrt{x^2+y^2}。'))]),
         sc.TableBlock(id=fx.hex_id('blk', 5), headers=[r'\Delta', r'\delta'], rows=[
             [r'\Delta t', r'\delta x'], [r'\alpha_1', r'\beta^{2}']])],
        [sc.DiagramBlock(id=fx.hex_id('blk', 6), diagram=sc.FlowDiagram(
            nodes=[sc.FlowNode(id='a', label=r'\Delta x'), sc.FlowNode(id='b', label=r'\delta t')],
            edges=[sc.FlowEdge.model_validate({'from': 'a', 'to': 'b', 'label': r'\to'})], alt='变化量关系'))],
        [fx.make_para_block(12, r'用变化量 \Delta t 与 \delta x 观察曲线。这里保留完整图形与说明，验证图形出现在正文之后时不会压缩容器、遮挡其他要点。'),
         sc.FormulaBlock(id=fx.hex_id('blk', 13), latex=r'\bar v=\frac{\delta x}{\Delta t}', spoken='平均变化率'),
         sc.DiagramBlock(id=fx.hex_id('blk', 7), diagram=sc.CartesianPlot(
            x_label=r'\Delta t', y_label=r'\delta x', x_range=(0, 2), y_range=(0, 2),
            series=[sc.PlotSeries(label=r'\alpha', points=[(0, 0), (1, 1)])], alt='示例曲线')),
         sc.BulletsBlock(id=fx.hex_id('blk', 14), items=[text(r'比较 \Delta t 的大小'), text(r'观察 \delta x 的变化'), text(r'解释 \alpha 的意义')])],
        [sc.DiagramBlock(id=fx.hex_id('blk', 8), diagram=sc.ForceDiagram(
            bodies=[sc.ForceBody(id='m', shape='box', x=.4, y=.6, label=r'\Delta m')],
            arrows=[sc.ForceArrow(body_id='m', dx=.4, dy=.4, label=r'\vec{F}')], alt='示例受力'))],
        [sc.CodeBlock(id=fx.hex_id('blk', 9), language='python',
                      code=r'value = "\delta + $x^2$ + \Delta"', caption=r'说明：变化量 \Delta t'),
         sc.ImageBlock(id=fx.hex_id('blk', 10), asset_id=fx.hex_id('ast', 1), alt='示意图', caption=r'图注：\delta 与 \Delta')],
    ]
    slides = []
    for order, blocks in enumerate(pages, 1):
        slide = fx.make_slide(order, title=fr'符号 \Delta 与 \delta：示例 {order}', blocks=blocks)
        slide.composition = sc.SlideComposition(mode='stack')
        if order == 4:
            slide.composition = sc.SlideComposition(mode='sidebar', focal_block_id=fx.hex_id('blk', 7))
        slides.append(slide)
    brief = fx.make_brief().model_copy(update={'theme_id': 'academic_clear@2', 'topic': r'符号 \Delta'})
    return fx.make_revision(slides=slides, brief=brief).model_copy(update={'renderer_version': '2.0.0'})


class InlineMathTests(StorageSandboxTestCase):
    def test_bare_commands_arguments_and_prose(self):
        value = r'变化量 \Delta t = \frac{\Delta x}{\Delta t}，误差 \delta x；\sqrt[3]{x_1}。'
        self.assertEqual([s for math, s in math_parts(value) if math], [
            r'\Delta t = \frac{\Delta x}{\Delta t}', r'\delta x', r'\sqrt[3]{x_1}'])
        self.assertEqual(''.join(s for _, s in math_parts(value)), value)

    def test_explicit_math_is_not_parsed_twice(self):
        rendered = render_text(r'$\delta$、\(\Delta t\)、\[\triangle ABC\]、$$\nabla f$$')
        self.assertEqual(rendered.count('data-katex='), 4)

    def test_currency_paths_and_incomplete_commands_stay_text(self):
        for text in [r'价格 $5 和 $10', r'C:\delta\data.txt', r'\\delta',
                     r'\unknown{x}', r'\fraction', r'\frac{x}', r'\sqrt{unclosed']:
            with self.subTest(text=text):
                self.assertNotIn('data-katex=', render_text(text))
                self.assertEqual(html.unescape(render_text(text)), text)

    def test_markup_is_escaped(self):
        rendered = render_text(r'<img src=x onerror=alert(1)> \delta 与 $\text{"<script>"}$')
        self.assertNotIn('<img', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)

    def test_prose_blocks_diagrams_and_code_exclusion(self):
        revision = inline_math_revision()
        for slide in revision.slides:
            for block in slide.blocks:
                with self.subTest(kind=block.kind):
                    rendered = render_block(block, assets_data={}, checkpoint_prompts={})
                    self.assertIn('data-katex=', rendered)
                    if block.kind == 'code':
                        code = rendered.split('<pre><code>')[1].split('</code>')[0]
                        self.assertNotIn('data-katex=', code)
                        self.assertIn(r'\delta + $x^2$ + \Delta', html.unescape(code))
        self.assertIn('<h1>符号 <span class="span-math"', compile_html(revision))

    def test_checkpoint_prompt_uses_same_renderer(self):
        block = sc.CheckpointBlock(id=fx.hex_id('blk', 11), checkpoint_id=fx.hex_id('ckp', 1))
        rendered = render_block(block, assets_data={}, checkpoint_prompts={
            block.checkpoint_id: (r'解释 \Delta 与 \delta 的区别', 'reflect')})
        self.assertEqual(rendered.count('data-katex='), 2)
