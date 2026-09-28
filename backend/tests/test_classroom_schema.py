"""课堂 schema 契约回归（plan.md §19.2 test_classroom_schema）。

覆盖：闭合字段、ID/引用/enum、空讲稿、长度、NaN/Infinity、坏 layout、
未知 action、不支持的 schema version。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402


class InlineSpanAndBlockTests(unittest.TestCase):
    def test_unknown_field_rejected(self):
        with self.assertRaises(ValidationError):
            sc.SpanText(text="x", extra_field="y")

    def test_math_span_spoken_limit(self):
        with self.assertRaises(ValidationError):
            sc.SpanMath(latex="E=mc^2", spoken="长" * 241)

    def test_discriminated_kind_required(self):
        with self.assertRaises(ValidationError):
            sc.SpanText.model_validate({"kind": "math", "latex": "x", "spoken": "x"})

    def test_bullets_item_limit(self):
        spans = [sc.SpanText(text="要点")]
        with self.assertRaises(ValidationError):
            sc.BulletsBlock(id=fx.hex_id("blk"), items=[spans] * 6)
        with self.assertRaises(ValidationError):
            sc.BulletsBlock(id=fx.hex_id("blk"),
                            items=[[sc.SpanText(text="超" * 41)]])

    def test_bullets_mixed_text_english_words(self):
        ok = [[sc.SpanText(text=" ".join(["word"] * 22))]]
        sc.BulletsBlock(id=fx.hex_id("blk"), items=ok)
        bad = [[sc.SpanText(text=" ".join(["word"] * 23))]]
        with self.assertRaises(ValidationError):
            sc.BulletsBlock(id=fx.hex_id("blk"), items=bad)

    def test_table_shape(self):
        kw = dict(id=fx.hex_id("blk"))
        with self.assertRaises(ValidationError):
            sc.TableBlock(**kw, headers=["a", "b"], rows=[["1", "2", "3"]])
        with self.assertRaises(ValidationError):
            sc.TableBlock(**kw, headers=["h"] * 6, rows=[])
        sc.TableBlock(**kw, headers=["a"] * 5, rows=[["1"] * 5] * 5)
        with self.assertRaises(ValidationError):
            sc.TableBlock(**kw, headers=["a"], rows=[["1"]] * 6)

    def test_steps_max_five(self):
        step = sc.StepItem(label="第1步", spans=[fx.make_span()])
        with self.assertRaises(ValidationError):
            sc.StepsBlock(id=fx.hex_id("blk"), steps=[step] * 6)


class DiagramTests(unittest.TestCase):
    def _flow(self, **kw) -> dict:
        base = dict(nodes=[sc.FlowNode(id="a", label="A")], edges=[],
                    alt="流程图")
        base.update(kw)
        return base

    def test_flow_node_edge_closure(self):
        with self.assertRaises(ValidationError):
            sc.FlowDiagram(**self._flow(edges=[
                sc.FlowEdge.model_validate({"from": "a", "to": "ghost"})]))
        with self.assertRaises(ValidationError):
            sc.FlowDiagram(**self._flow(nodes=[
                sc.FlowNode(id="a", label="A"),
                sc.FlowNode(id="a", label="B")]))
        with self.assertRaises(ValidationError):
            sc.FlowDiagram(**self._flow(nodes=[
                sc.FlowNode(id=f"n{i}", label=str(i)) for i in range(11)]))
        # JSON 侧使用 "from" 别名
        sc.FlowDiagram.model_validate({
            "type": "flow", "nodes": [{"id": "a", "label": "A"}],
            "edges": [{"from": "a", "to": "a", "label": "环"}], "alt": "流程图"})

    def _plot(self, **kw) -> dict:
        base = dict(x_label="t", y_label="v", x_range=(0.0, 10.0),
                    y_range=(0.0, 10.0), constructed=True, alt="速度-时间",
                    series=[sc.PlotSeries(label="v", points=[(1.0, 1.0)])])
        base.update(kw)
        return base

    def test_plot_rules(self):
        with self.assertRaises(ValidationError):
            sc.CartesianPlot(**self._plot(series=[
                sc.PlotSeries(label="v", points=[(11.0, 1.0)])]))
        with self.assertRaises(ValidationError):
            sc.CartesianPlot(**self._plot(x_range=(10.0, 0.0)))
        with self.assertRaises(ValidationError):
            sc.CartesianPlot(**self._plot(x_range=(0.0, 1e10)))
        with self.assertRaises(ValidationError):
            sc.CartesianPlot(**self._plot(series=[
                sc.PlotSeries(label=f"s{i}", points=[(1.0, 1.0)])
                for i in range(4)]))
        with self.assertRaises(ValidationError):
            sc.CartesianPlot(**self._plot(series=[
                sc.PlotSeries(label="v",
                              points=[(float(i), 1.0) for i in range(101)])]))

    def test_plot_nan_infinity_rejected(self):
        for bad_range in ((float("nan"), 10.0), (float("inf"), 10.0),
                          (0.0, float("-inf"))):
            with self.assertRaises(ValidationError):
                sc.CartesianPlot(**self._plot(x_range=bad_range))

    def _force(self, **kw) -> dict:
        base = dict(bodies=[sc.ForceBody(id="m", shape="point", x=0.5, y=0.5,
                                         label="小车")],
                    arrows=[], constructed=True, alt="受力图")
        base.update(kw)
        return base

    def test_force_bounds(self):
        sc.ForceDiagram(**self._force())
        with self.assertRaises(ValidationError):
            sc.ForceDiagram(**self._force(bodies=[
                sc.ForceBody(id="m", shape="point", x=1.5, y=0.5,
                             label="小车")]))
        with self.assertRaises(ValidationError):
            sc.ForceDiagram(**self._force(arrows=[
                sc.ForceArrow(body_id="m", dx=1.5, dy=0.0, label="F")]))
        with self.assertRaises(ValidationError):
            sc.ForceDiagram(**self._force(arrows=[
                sc.ForceArrow(body_id="ghost", dx=0.1, dy=0.0, label="F")]))


class SlideSpecTests(unittest.TestCase):
    def test_id_patterns(self):
        slide = fx.make_slide(1)
        bad = slide.model_dump()
        bad["slide_id"] = "not_a_valid_id"
        with self.assertRaises(ValidationError):
            sc.SlideSpec.model_validate(bad)

    def test_bad_layout_rejected(self):
        bad = fx.make_slide(1).model_dump()
        bad["layout"] = "hologram"
        with self.assertRaises(ValidationError):
            sc.SlideSpec.model_validate(bad)

    def test_title_cjk_limit(self):
        with self.assertRaises(ValidationError):
            fx.make_slide(1, title="长" * 37)
        fx.make_slide(1, title="长" * 36)

    def test_title_english_word_limit(self):
        fx.make_slide(1, title=" ".join(["word"] * 12))
        with self.assertRaises(ValidationError):
            fx.make_slide(1, title=" ".join(["word"] * 13))

    def test_blocks_and_segments_limits(self):
        blocks = [fx.make_para_block(i + 1) for i in range(17)]
        with self.assertRaises(ValidationError):
            fx.make_slide(1, blocks=blocks)
        segs = [fx.make_segment(i + 1) for i in range(25)]
        with self.assertRaises(ValidationError):
            fx.make_slide(1, segments=segs)

    def test_empty_spoken_rejected(self):
        with self.assertRaises(ValidationError):
            sc.NarrationSegment(
                segment_id=fx.hex_id("seg"), role=sc.SegmentRole.explain,
                display_text="显示文本", spoken_text="")

    def test_spoken_hard_cap_240(self):
        with self.assertRaises(ValidationError):
            sc.NarrationSegment(
                segment_id=fx.hex_id("seg"), role=sc.SegmentRole.explain,
                display_text="x", spoken_text="长" * 241)

    def test_pause_after_ms_bounds(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_segment(1), {"pause_after_ms": 3001})


class BriefTests(unittest.TestCase):
    def test_strict_textbook_requires_files(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(files=False),
                             {"source_policy": sc.SourcePolicy.strict_textbook})

    def test_goals_limits(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(), {"goals": ["g"] * 6})
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(), {"goals": ["x" * 121]})

    def test_chapter_total_limit(self):
        with self.assertRaises(ValidationError):
            sc.SourceSelection(files=[
                sc.SourceFileSelection(
                    file_id="f1", chapters=[sc.ChapterSelection(title=str(i))
                                            for i in range(6)]),
                sc.SourceFileSelection(
                    file_id="f2", chapters=[sc.ChapterSelection(title=str(i))
                                            for i in range(7)]),
            ])

    def test_topic_bounds(self):
        with self.assertRaises(ValidationError):
            sc.LessonBrief(topic="短")
        with self.assertRaises(ValidationError):
            sc.LessonBrief(topic="长" * 121)

    def test_bad_duration_rejected(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(), {"duration_minutes": 12})

    def test_bad_pedagogy_theme_rejected(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(), {"pedagogy_id": "custom@9"})
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_brief(), {"theme_id": "dark_mode@1"})


class SourceRecordTests(unittest.TestCase):
    def test_web_locator_https_only(self):
        with self.assertRaises(ValidationError):
            sc.WebLocator(url="http://example.com/a",
                          canonical_url="https://example.com/a",
                          domain="example.com", retrieved_at=fx.utcnow())

    def test_kind_locator_consistency(self):
        with self.assertRaises(ValidationError):
            sc.SourceRecord(
                source_id=fx.hex_id("src"), kind=sc.SourceKind.web,
                title="网页",
                locator=sc.FileLocator(namespace="public", file_id="f",
                                       chunk_ids=["c"], content_hash="a" * 64),
                excerpt_hash="b" * 64, retrieved_at=fx.utcnow())

    def test_excerpt_hash_format(self):
        with self.assertRaises(ValidationError):
            fx.strict_update(fx.make_file_source(), {"excerpt_hash": "zz"})


class EntityTests(unittest.TestCase):
    def test_schema_version_locked(self):
        data = fx.make_revision().model_dump()
        data["schema_version"] = 2
        with self.assertRaises(ValidationError):
            sc.LessonRevision.model_validate(data)

    def test_lesson_published_revisions_cap(self):
        now = fx.utcnow()
        with self.assertRaises(ValidationError):
            sc.Lesson(lesson_id=fx.hex_id("les"), owner_id="usr_a",
                      workspace_id="ws_x", title="课程", created_at=now,
                      updated_at=now, published_revisions=list(range(1, 22)))

    def test_unknown_operation_rejected(self):
        with self.assertRaises(ValidationError):
            sc.RevisionOpRequest(base_revision=1,
                                 operation={"op": "delete_everything"})

    def test_edit_change_union_closed(self):
        with self.assertRaises(ValidationError):
            sc.EditContentOperation(changes=[
                {"op": "patch_json", "path": "/slides/0", "value": {}}])

    def test_checkpoint_reflect_no_question(self):
        with self.assertRaises(ValidationError):
            sc.CheckpointTemplate(
                checkpoint_id=fx.hex_id("ckp"), slide_id=fx.slide_hex(),
                kind=sc.CheckpointKind.reflect, prompt="想一想",
                verified_question_template={"question": "..."})

    def test_run_cursor_bounds(self):
        with self.assertRaises(ValidationError):
            sc.Cursor(slide_id=fx.slide_hex(), segment_id=fx.hex_id("seg"),
                      chunk_index=0, offset_ms=-1)

    def test_audio_clip_state_enum(self):
        with self.assertRaises(ValidationError):
            sc.AudioClip(
                clip_id="clip_12345678", owner_id="usr_a",
                lesson_id=fx.hex_id("les"), revision=1,
                kind=sc.AudioKind.narration, synthesis_key="d" * 64,
                state="playing")

    def test_playback_speed_bounds(self):
        with self.assertRaises(ValidationError):
            sc.VoicePreferences(playback_speed=1.6)


class MixedTextHelperTests(unittest.TestCase):
    def test_check_mixed_text(self):
        self.assertTrue(sc.check_mixed_text("长" * 36, 36, 12))
        self.assertFalse(sc.check_mixed_text("长" * 37, 36, 12))
        self.assertTrue(sc.check_mixed_text(" ".join(["w"] * 12), 36, 12))
        self.assertFalse(sc.check_mixed_text(" ".join(["w"] * 13), 36, 12))
        # 混合按比例：18 个中文字 + 6 个英文词 = 0.5 + 0.5
        self.assertTrue(sc.check_mixed_text("长" * 18 + " " + " ".join(["w"] * 6),
                                            36, 12))


if __name__ == "__main__":
    unittest.main()
