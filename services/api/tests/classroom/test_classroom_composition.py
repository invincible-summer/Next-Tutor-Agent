"""Design intent, safe code and mixed blocks; all storage is sandboxed."""
import asyncio

from pydantic import ValidationError

from app.classroom.generation_normalize import normalize_authored_slide
from app.classroom.render.blocks import render_block
from app.classroom.render.check import run_layout_check
from app.classroom.render.compiler import compile_html, validate_layout_slots
from app.classroom.validation import coerce_layout_blocks
from app.schemas import classroom as sc
from tests.support import classroom_fixtures as fx
from tests.support.storage_sandbox import StorageSandboxTestCase


def gallery_revision():
    def para(n, text):
        return fx.make_para_block(n, text)

    def bullets(n, items):
        return sc.BulletsBlock(id=fx.hex_id('blk', n), items=[[sc.SpanText(text=t)] for t in items])

    def note(n, text):
        return sc.CalloutBlock(id=fx.hex_id('blk', n), spans=[sc.SpanText(text=text)])

    pages = [
        ('先选系统，再判断动量是否守恒', 'editorial', 'plain', [
            para(1, '动量守恒是一种系统视角：先画清研究对象的边界，再考察外界在碰撞期间施加的冲量。'),
            bullets(2, ['系统：两辆相互碰撞的小车', '内力：两车之间的碰撞力', '外力：轨道摩擦与重力等']),
            note(3, '关键条件：碰撞时间足够短，外力冲量相对内力冲量可以忽略。合外力不必在每个瞬间都严格为零。'),
            fx.make_formula_block(4),
        ]),
        ('守恒的对象不同，判断条件也不同', 'columns', 'soft', [
            note(11, '动量守恒：系统所受合外力冲量为零。动量是矢量，需要选定正方向并按分量列式。'),
            note(12, '机械能守恒：系统中只有保守力做功。动能与势能可以相互转化，但两者总和保持不变。'),
            para(13, '完全非弹性碰撞：两车粘在一起时，动量仍可守恒；部分动能转化为内能，机械能通常不守恒。'),
            para(14, '弹簧连接两车：忽略摩擦时，弹性势能与动能互换；系统总动量和机械能可以同时守恒。'),
        ]),
        ('用一小段代码验证碰撞速度', 'sidebar', 'plain', [
            sc.CodeBlock(id=fx.hex_id('blk', 21), language='python', code='def collide(m1, v1, m2, v2):\n    p_before = m1 * v1 + m2 * v2\n    return p_before / (m1 + m2)\n\nv = collide(2, 3, 1, 0)\nprint(v)  # 2.0 m/s', caption='完全非弹性碰撞：两物体具有共同末速度'),
            para(22, '输入质量与速度时先统一单位。速度带正负号，代表沿选定坐标轴的方向。'),
            note(23, '检验：初动量为 6 kg·m/s；末动量为 (2 + 1) × 2 = 6 kg·m/s，前后一致。'),
        ]),
        ('从冲量关系推导共同末速度', 'stack', 'outlined', [
            para(31, '假设两车碰撞后粘合。取两车为系统，碰撞时间内外力冲量可忽略，故系统总动量不变。'),
            sc.FormulaBlock(id=fx.hex_id('blk', 32), latex=r'\begin{aligned}p_{\mathrm{before}}&=m_1v_1+m_2v_2\\p_{\mathrm{after}}&=(m_1+m_2)v\\v&=\frac{m_1v_1+m_2v_2}{m_1+m_2}\end{aligned}', spoken='共同末速度等于初动量除以总质量'),
            note(33, '边界检验：当两车初速度相同时，末速度应等于原速度；当初总动量为零时，粘合后的系统静止。'),
        ]),
        ('判断路径：条件决定方法', 'auto', 'plain', [
            sc.DiagramBlock(id=fx.hex_id('blk', 41), diagram=sc.FlowDiagram(
                nodes=[sc.FlowNode(id=f'n{i}', label=label) for i, label in enumerate(['确定研究对象', '选择系统边界', '列出所有外力', '估计碰撞时间', '计算外力冲量', '选择守恒或冲量方程'])],
                edges=[sc.FlowEdge.model_validate({'from': f'n{i}', 'to': f'n{i+1}', 'label': '下一步'}) for i in range(5)], alt='从选定系统到选择方程的判断流程')),
            para(42, '不要看到“碰撞”就直接套公式。若外力冲量不可忽略，应把它写在系统动量变化方程右侧。'),
            note(43, '同一个事件，系统选取不同，内力与外力的划分也不同；结论必须和系统边界一起说明。'),
        ]),
    ]
    slides = []
    for order, (title, mode, surface, blocks) in enumerate(pages, 1):
        slide = fx.make_slide(order, title=title, blocks=blocks)
        slide.composition = sc.SlideComposition(mode=mode, surface=surface,
            wide_block_ids=[blocks[-1].id] if order == 1 else [])
        slides.append(slide)
    brief = fx.make_brief().model_copy(update={'theme_id': 'academic_clear@2'})
    return fx.make_revision(slides=slides, brief=brief).model_copy(update={'renderer_version': '2.0.0'})


def code_revision():
    source = 'value = "<script>alert(1)</script> $not_math$"\n\n' + ''.join(
        f'    item_{i} = "' + 'long_value_' * 12 + '"\n' for i in range(32))
    code = sc.CodeBlock(id=fx.hex_id('blk', 90), code=source, language='python')
    slide = fx.make_slide(blocks=[code, fx.make_para_block(91, '检查每行数据，并保留完整缩进。')])
    slide.composition = sc.SlideComposition(mode='stack')
    return gallery_revision().model_copy(update={'slides': [slide]})


def hierarchy_revision():
    table = sc.TableBlock(id=fx.hex_id('blk', 101), headers=['Δt / 秒', 'Δx / 米', '平均速度'],
                         rows=[['1', '5', '5'], ['0.5', '2.25', '4.5'], ['0.1', '0.41', '4.1']], constructed=True)
    first = fx.make_slide(1, title='缩短时间间隔，先观察数据', blocks=[
        fx.make_para_block(100, '固定 t=2 秒。缩短时间间隔，比较位置变化与平均速度，预测接近哪个值。'),
        table, sc.CalloutBlock(id=fx.hex_id('blk', 102), spans=[sc.SpanText(text='数据越来越接近，仍需解释为什么。')])])
    first.composition = sc.SlideComposition(mode='sidebar', focal_block_id=table.id)
    wide = fx.make_formula_block(113).model_copy(update={
        'latex': r'\begin{aligned}\bar v(\Delta t)&=\frac{(2+\Delta t)^2-4}{\Delta t}\\&=4+\Delta t\qquad(\Delta t\ne0)\end{aligned}'})
    second = fx.make_slide(2, title='保留长式的通栏与完整推理', blocks=[
        fx.make_para_block(111, '固定观察时刻，先计算位置的变化量，再除以非零的时间间隔。'*4),
        fx.make_para_block(112, '增量非零时，才可以约去。'),
        fx.make_para_block(114, '最后让时间间隔趋近零，差商的极限给出这一时刻的瞬时速度。'*3),
        fx.make_para_block(115, '差商有意义，才能讨论其极限。'),
        wide,
        sc.CalloutBlock(id=fx.hex_id('blk', 116), spans=[sc.SpanText(text='取极限不能用把零直接代入原始分式来替代。')])])
    second.composition = sc.SlideComposition(mode='stack', wide_block_ids=[wide.id])
    third = fx.make_slide(3, title='主表在前，解释连续排列', blocks=[
        table.model_copy(update={'id': fx.hex_id('blk', 121)}),
        fx.make_para_block(120, '先观察平均速度列，再比较各个时间间隔。'),
        sc.CalloutBlock(id=fx.hex_id('blk', 122), spans=[sc.SpanText(text='右侧解释按顺序紧接，不被主表高度拉开。')])])
    third.composition = sc.SlideComposition(mode='sidebar', focal_block_id=third.blocks[0].id)
    return gallery_revision().model_copy(update={'slides': [first, second, third]})


class CompositionTests(StorageSandboxTestCase):
    def test_mixed_blocks_keep_pedagogical_layout_and_content(self):
        revision = gallery_revision()
        before = revision.model_dump_json()
        validate_layout_slots(revision)
        for slide in revision.slides:
            self.assertFalse(coerce_layout_blocks(slide, preserve_content=True))
        report = asyncio.run(run_layout_check(compile_html(revision)))
        self.assertTrue(report.ok, report.overflow_issue_summary())
        self.assertEqual(before, revision.model_dump_json())

    def test_code_is_escaped_and_never_interpreted_as_math(self):
        code = code_revision().slides[0].blocks[0]
        html = render_block(code, assets_data={}, checkpoint_prompts={})
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('data-katex', html)
        self.assertIn('$not_math$', html)
        report = asyncio.run(run_layout_check(compile_html(code_revision())))
        self.assertFalse(report.ok)
        self.assertTrue(any(i["code"] == "vertical_overflow" for i in report.issues))

    def test_design_short_ids_and_fenced_code_normalize(self):
        slide = fx.make_slide().model_dump(mode='json')
        slide['blocks'] = [{'kind': 'paragraph', 'id': 'b1', 'spans': [
            {'kind': 'text', 'text': '```python\ndef f():\n    return 1\n```'}]}]
        slide['composition'] = {'mode': 'editorial', 'focal_block_id': 'b1',
                                'emphasis_block_id': 'b1', 'wide_block_ids': ['b1']}
        result = sc.SlideSpec.model_validate(normalize_authored_slide({'slide': slide})['slide'])
        self.assertEqual(result.composition.emphasis_block_id, result.blocks[0].id)
        self.assertEqual(result.composition.focal_block_id, result.blocks[0].id)
        self.assertEqual(result.composition.wide_block_ids, [result.blocks[0].id])
        self.assertEqual(result.blocks[0].code, 'def f():\n    return 1')

    def test_focal_reference_must_belong_to_this_page(self):
        slide = fx.make_slide().model_dump(mode='json')
        slide['composition'] = {'focal_block_id': fx.hex_id('blk', 999)}
        with self.assertRaisesRegex(ValidationError, '视觉主角必须引用本页组件'):
            sc.SlideSpec.model_validate(slide)

    def test_arbitrary_css_is_not_design_intent(self):
        with self.assertRaises(ValidationError):
            sc.SlideComposition.model_validate({'mode': 'columns', 'css': 'position:fixed'})
        with self.assertRaises(ValidationError):
            sc.CodeBlock(id=fx.hex_id('blk', 1), code='x', language='" onclick="x')

    def test_authored_plot_expands_domain_without_clipping_points(self):
        slide = fx.make_slide().model_dump(mode='json')
        points = [[0, -1], [1, 1], [3, 9]]
        slide['blocks'] = [{'kind': 'diagram', 'id': 'b1', 'diagram': {
            'type': 'cartesian_plot', 'x_label': 't', 'y_label': 'x',
            'x_range': [0, 2], 'y_range': [0, 4],
            'series': [{'label': '构造样例', 'points': points}], 'alt': '三个数据点'}}]
        result = sc.SlideSpec.model_validate(normalize_authored_slide({'slide': slide})['slide'])
        diagram = result.blocks[0].diagram
        self.assertEqual(diagram.x_range, (0, 3))
        self.assertEqual(diagram.y_range, (-1, 9))
        self.assertEqual(diagram.series[0].points, [tuple(p) for p in points])

    def test_illustration_requires_explanation_and_one_image_limit(self):
        image = sc.ImageBlock(id=fx.hex_id('blk', 70), asset_id=fx.hex_id('ast', 70),
                              alt='实验装置', caption='观察两个小车的碰撞')
        slide = fx.make_slide(blocks=[image]).model_dump(mode='json')
        slide['composition'] = {'mode': 'sidebar'}
        with self.assertRaisesRegex(ValidationError, '同页解释'):
            sc.SlideSpec.model_validate(slide)
        slide['blocks'].append(fx.make_para_block(71).model_dump(mode='json'))
        sc.SlideSpec.model_validate(slide)
        slide['blocks'].append(image.model_dump(mode='json'))
        with self.assertRaisesRegex(ValidationError, '至多一张'):
            sc.SlideSpec.model_validate(slide)

    def test_math_environment_normalization(self):
        slide = fx.make_slide(blocks=[fx.make_formula_block().model_copy(update={
            'latex': r'\begin{align}x&=1\label{eq:a}\\y&=2\nonumber\end{align}'})])
        revision = gallery_revision().model_copy(update={'slides': [slide]})
        report = asyncio.run(run_layout_check(compile_html(revision)))
        self.assertTrue(report.ok, report.overflow_issue_summary())
