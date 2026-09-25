"""课堂排版全主题/全布局真实 Chromium 检查（plan.md §9.6/B 退出门）。

覆盖 5 主题 × 9 布局 × 3 视口（1280×720 / 960×540 / 390 阅读模式），
含最长中文标题、英文长词、公式、图表与无图情况。同时验证确定性
质量门：故意超预算的页面必须被检查器判 overflow。

依赖 build:classroom 生成的资产包与已安装的 Chromium；任一缺失时
跳过（记录原因，不假称通过）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classroom.render.check import run_layout_check  # noqa: E402
from app.classroom.render.compiler import compile_html  # noqa: E402
from app.classroom.render.themes import THEME_IDS  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402


def _png_bytes() -> bytes:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return b""
    img = Image.new("RGB", (800, 500), (210, 225, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _para(n: int, text: str) -> sc.ParagraphBlock:
    return sc.ParagraphBlock(id=fx.hex_id("blk", n),
                             spans=[sc.SpanText(text=text)])


def _bullets(n: int, items: list[str]) -> sc.BulletsBlock:
    return sc.BulletsBlock(id=fx.hex_id("blk", n),
                           items=[[sc.SpanText(text=t)] for t in items])


def _steps(n: int, labels: list[str]) -> sc.StepsBlock:
    return sc.StepsBlock(
        id=fx.hex_id("blk", n),
        steps=[sc.StepItem(label=label, spans=[sc.SpanText(text=label)])
               for label in labels])


def build_showcase_revision(theme_id: str, *, overfull: bool = False
                            ) -> sc.LessonRevision:
    """9 布局展示课；overfull=True 时 worked_example 页故意超预算。"""
    asset = sc.AssetRecord(
        asset_id=fx.hex_id("ast", 1), sha256="d" * 64, mime="image/png",
        width=800, height=500,
        provenance=sc.AssetProvenance(provider=sc.AssetProvider.pexels,
                                      creator="展示摄影师",
                                      fetched_at=fx.utcnow()),
        alt="碰撞实验", caption="气垫导轨上两小车的碰撞实验",
        role=sc.AssetRole.scene, bytes=1000)
    img = sc.ImageBlock(id=fx.hex_id("blk", 5), asset_id=asset.asset_id,
                        alt="碰撞实验", caption="气垫导轨上两小车的碰撞实验",
                        fit=sc.ImageFit.contain)
    formula = fx.make_formula_block(9)
    steps = _steps(10, ["确定系统和质量。", "标出各外力方向。",
                        "判断合外力冲量。", "应用守恒定律列方程。"])
    if overfull:
        # 合法但超预算：推导页 5 步长文本 + 公式 + 警示框，确定性质量门必须拦截
        steps = sc.StepsBlock(
            id=fx.hex_id("blk", 10),
            steps=[sc.StepItem(
                label=f"第{i}步",
                spans=[sc.SpanText(
                    text="这一步要仔细核对系统的边界与所有外力的冲量大小，"
                         "逐一列出来源、方向、作用时间与量纲，并对每一项"
                         "说明能否在近似条件下忽略以及忽略的依据是什么。")])
                for i in range(1, 6)])
    warn = sc.CalloutBlock(
        id=fx.hex_id("blk", 11), tone=sc.CalloutTone.warning,
        spans=[sc.SpanText(text="近似条件不满足时不可强行使用。")])
    table = sc.TableBlock(
        id=fx.hex_id("blk", 12),
        headers=["物体", "碰前/(m/s)", "碰后/(m/s)"],
        rows=[["甲", "+2.0", "+0.4"], ["乙", "-1.0", "+0.6"]])
    ckp_block = sc.CheckpointBlock(id=fx.hex_id("blk", 15),
                                   checkpoint_id=fx.hex_id("ckp"))
    ckp_tpl = sc.CheckpointTemplate(
        checkpoint_id=ckp_block.checkpoint_id, slide_id=fx.slide_hex(8),
        kind=sc.CheckpointKind.reflect, prompt="换成三辆车系统怎么选？",
        reflection_seconds=30)

    def slide(n: int, title: str, layout: sc.SlideLayout,
              blocks: list) -> sc.SlideSpec:
        return sc.SlideSpec(
            slide_id=fx.slide_hex(n), order=n, title=title, layout=layout,
            blocks=blocks,
            segments=[sc.NarrationSegment(
                segment_id=fx.hex_id("seg", n), role=sc.SegmentRole.explain,
                display_text="讲稿", spoken_text="讲稿内容。",
                show_block_ids=[b.id for b in blocks])],
            estimated_seconds=40)

    slides = [
        slide(1, "系统边界与动量守恒定律及其适用条件的完整分析",
              sc.SlideLayout.title,
              [_para(1, "大学物理 · 第三章 质点与质点系动力学"),
               _para(2, "antidisestablishmentarianism 与超长英文单词排版")]),
        slide(2, "本课要点", sc.SlideLayout.key_points,
              [_bullets(3, [f"要点{i}：" + "内容文字" * 4 for i in range(1, 6)]),
               sc.CalloutBlock(id=fx.hex_id("blk", 4), tone=sc.CalloutTone.note,
                               spans=[sc.SpanText(text="先选系统，再看外力。")])]),
        slide(3, "实验观察", sc.SlideLayout.image_explain,
              [img, _para(6, "两车碰撞后速度同时变化，说明存在相互作用。")]),
        slide(4, "内力 vs 外力", sc.SlideLayout.compare,
              [_para(7, "内力：系统内物体之间的相互作用，成对出现。"),
               _para(8, "外力：系统外物体对系统内物体施加的力。")]),
        slide(5, "动量守恒的推导前提", sc.SlideLayout.derivation,
              [formula, steps, warn]),
        slide(6, "例题：一维碰撞", sc.SlideLayout.worked_example,
              [_steps(13, ["列出动量守恒方程。", "代入碰前速度。"]),
               fx.make_formula_block(14), table]),
        slide(7, "动量概念的发展", sc.SlideLayout.timeline,
              [_bullets(14, ["伽利略：碰撞问题的开端", "笛卡尔：运动量守恒",
                             "牛顿：定律体系建立", "现代：粒子物理应用"])]),
        slide(8, "想一想", sc.SlideLayout.checkpoint, [ckp_block]),
        slide(9, "本课总结", sc.SlideLayout.summary,
              [_bullets(16, ["动量守恒需要系统观念。", "关键是判断合外力冲量。",
                             "近似条件要显式说明。"]),
               sc.CalloutBlock(id=fx.hex_id("blk", 17),
                               tone=sc.CalloutTone.summary,
                               spans=[sc.SpanText(text="下一课：动能定理。")])]),
    ]
    brief = fx.make_brief().model_copy(update={"theme_id": theme_id})
    return sc.LessonRevision(
        revision=1, schema_version=1, brief=brief,
        source_snapshot=[fx.make_file_source(1)], slides=slides,
        checkpoint_templates=[ckp_tpl],
        objectives=[sc.Objective(objective_id="objective_1", text="会判断")],
        glossary=[], assets=[asset], renderer_version="1.0.0",
        prompt_versions={}, review_report=None, content_hash="c" * 64,
        created_at=fx.utcnow())


def _prerequisites_ready() -> tuple[bool, str]:
    try:
        from app.classroom.render.assets import load_asset_pack
        if load_asset_pack() is None:
            return False, "renderer assets missing (pnpm run build:classroom)"
    except Exception as exc:  # pragma: no cover
        return False, f"asset pack error: {exc}"
    import shutil
    if shutil.which("node") is None:
        return False, "node unavailable"
    return True, ""


class FullThemeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ok, reason = _prerequisites_ready()
        if not ok:
            raise unittest.SkipTest(f"排版检查前置不满足: {reason}")
        cls.png = _png_bytes()
        assert cls.png, "Pillow 不可用"

    def _check(self, revision: sc.LessonRevision):
        html = compile_html(
            revision, mode="offline",
            asset_bytes={fx.hex_id("ast", 1): self.png})
        return asyncio.run(run_layout_check(html))

    def test_all_themes_all_layouts_fit(self):
        for theme_id in THEME_IDS:
            with self.subTest(theme=theme_id):
                report = self._check(build_showcase_revision(theme_id))
                self.assertTrue(
                    report.ok,
                    f"{theme_id} 溢出: {report.overflow_issue_summary()}")

    def test_overfull_slide_is_detected(self):
        report = self._check(build_showcase_revision(
            "academic_clear@1", overfull=True))
        self.assertFalse(report.ok)
        self.assertTrue(any(i["code"] == "overflow" and i["slide_order"] == 5
                            for i in report.issues),
                        f"质量门未拦截超预算页: {report.overflow_issue_summary()}")


if __name__ == "__main__":
    unittest.main()
