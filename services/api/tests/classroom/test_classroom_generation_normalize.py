"""超长流程标签的无损整理；生成与重写共用，不调用真实模型。"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError

from app.classroom.generation_normalize import normalize_authored_slide
from app.classroom.llm_io import generate_json
from app.classroom.pipeline import _SlideModel, _slide_schema_hint
from app.classroom import revisions
from app.core import classroom_store as store
from app.schemas import classroom as sc
from tests.support import classroom_fixtures as fx
from tests.support.classroom_fake_llm import FakeClassroomLLM
from tests.support.storage_sandbox import StorageSandboxTestCase
from tests.classroom.test_classroom_pipeline import OWNER, WS, PipelineTestBase


LONG_LABEL = "先明确研究对象与系统边界，再逐一判断系统受到的外力冲量是否可以忽略，并说明所采用近似的依据。"


def flow_payload() -> dict:
    slide = fx.make_slide(blocks=[fx.make_para_block(1)])
    payload = {"slide": slide.model_dump(mode="json", by_alias=True),
               "claims": [{"block_id": "b1", "claim_kind": "author_explanation",
                           "text": "判断系统的守恒条件"}]}
    payload["slide"]["blocks"] = [{
        "kind": "diagram", "id": "b1", "diagram": {
            "type": "flow", "direction": "horizontal", "alt": "判断动量守恒的流程",
            "nodes": [{"id": "a", "label": "明确系统"},
                      {"id": "b", "label": LONG_LABEL},
                      {"id": "c", "label": "建立方程"}],
            "edges": [{"from": "a", "to": "b", "label": "分析外力"},
                      {"from": "b", "to": "c"}],
        }}]
    payload["slide"]["composition"] = {
        "focal_block_id": "b1", "emphasis_block_id": "b1", "wide_block_ids": ["b1"]}
    for segment in payload["slide"]["segments"]:
        segment["show_block_ids"] = ["b1"]
        segment["focus_block_ids"] = ["b1"]
    return payload


class FlowLabelRecoveryTests(StorageSandboxTestCase):
    def test_long_node_labels_pass_without_another_model_call(self):
        payload = flow_payload()
        payload["slide"]["blocks"][0]["diagram"]["nodes"][2]["label"] = LONG_LABEL + "再检验结果。"
        fake = SimpleNamespace(complete=mock.AsyncMock(
            return_value=(json.dumps(payload, ensure_ascii=False), None)))
        result, usages = asyncio.run(generate_json(
            fake, prompt_id="classroom_slide", user_text="test", model_cls=_SlideModel,
            pre_validate=normalize_authored_slide, repair_attempts=0))
        self.assertEqual(fake.complete.await_count, 1)
        self.assertEqual(len(usages), 1)
        block = result.slide.blocks[0]
        self.assertEqual(block.kind, "paragraph")
        text = "".join(span.text for span in block.spans)
        self.assertIn("2. " + LONG_LABEL, text)
        self.assertIn("3. " + LONG_LABEL + "再检验结果。", text)
        self.assertIn("1 → 2：分析外力", text)
        self.assertIn("2 → 3", text)
        self.assertIn("判断动量守恒的流程", text)
        self.assertEqual(result.claims[0].block_id, block.id)
        self.assertEqual(result.slide.composition.focal_block_id, block.id)
        self.assertEqual(result.slide.composition.emphasis_block_id, block.id)
        self.assertEqual(result.slide.composition.wide_block_ids, [block.id])
        for segment in result.slide.segments:
            self.assertEqual(segment.show_block_ids, [block.id])
            self.assertEqual(segment.focus_block_ids, [block.id])

    def test_branches_cycles_and_long_edge_labels_keep_their_direction(self):
        payload = flow_payload()
        diagram = payload["slide"]["blocks"][0]["diagram"]
        diagram["nodes"][1]["label"] = "判断条件"
        label = "If the external impulse cannot be ignored, reconsider the chosen system boundary."
        diagram["edges"] = [
            {"from": "a", "to": "b", "label": label},
            {"from": "a", "to": "c", "label": "成立"},
            {"from": "c", "to": "a", "label": "重新检查"},
            {"from": "b", "to": "b", "label": "重复判断"},
        ]
        result = _SlideModel.model_validate(normalize_authored_slide(payload))
        text = "".join(span.text for span in result.slide.blocks[0].spans)
        for relation in ("1 → 2：" + label, "1 → 3：成立", "3 → 1：重新检查", "2 → 2：重复判断"):
            self.assertIn(relation, text)

    def test_exact_limit_flow_keeps_its_diagram_and_all_fields(self):
        payload = flow_payload()
        diagram = payload["slide"]["blocks"][0]["diagram"]
        diagram["nodes"][1]["label"] = "字" * 40
        diagram["edges"][0]["label"] = "词" * 40
        expected = copy.deepcopy(diagram)
        result = _SlideModel.model_validate(normalize_authored_slide(payload))
        self.assertEqual(result.slide.blocks[0].kind, "diagram")
        actual = result.slide.blocks[0].diagram.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.assertEqual(actual, expected)

    def test_long_prose_and_latex_are_kept_in_full(self):
        payload = flow_payload()
        label = LONG_LABEL * 15 + r"此时 $\sum_{i=1}^{n}\vec{F}_{i,\mathrm{ext}}=\frac{d\vec{p}}{dt}$。"
        payload["slide"]["blocks"][0]["diagram"]["nodes"][1]["label"] = label
        normalized = normalize_authored_slide(payload)
        result = _SlideModel.model_validate(normalized)
        text = "".join(span.text for span in result.slide.blocks[0].spans)
        self.assertIn(label, text)
        self.assertTrue(all(len(span.text) <= sc.MAX_INLINE_TEXT for span in result.slide.blocks[0].spans))
        self.assertEqual(normalize_authored_slide(copy.deepcopy(normalized)), normalized)

    def test_bad_graphs_remain_invalid_instead_of_losing_structure(self):
        variants = []
        for change in (
            lambda d: d["edges"].append({"from": "a", "to": "missing"}),
            lambda d: d["nodes"].append({"id": "a", "label": "重复"}),
            lambda d: d.update(direction="diagonal"),
            lambda d: d.update(unknown="unexpected"),
            lambda d: d["nodes"][0].update(label=""),
            lambda d: d["edges"][0].update(label=123),
            lambda d: d["nodes"][0].update(id="invalid id"),
        ):
            payload = flow_payload()
            change(payload["slide"]["blocks"][0]["diagram"])
            variants.append(payload)
        payload = flow_payload()
        payload["slide"]["blocks"][0]["spans"] = [{"kind": "text", "text": "非法字段"}]
        variants.append(payload)
        for payload in variants:
            with self.subTest(diagram=payload["slide"]["blocks"][0]["diagram"]):
                normalized = normalize_authored_slide(payload)
                self.assertEqual(normalized["slide"]["blocks"][0]["kind"], "diagram")
                with self.assertRaises(ValidationError):
                    _SlideModel.model_validate(normalized)


class LongFlowLLM(FakeClassroomLLM):
    def _outline(self, payload):
        result = super()._outline(payload)
        for page in result["pages"]:
            if not page["key_points"]:
                page["key_points"] = ["检查系统边界与守恒条件"]
        return result

    def _slide(self, payload, raw_user):
        result = super()._slide(payload, raw_user)
        if payload["order"] == 2:
            block = result["slide"]["blocks"][0]
            block.update(kind="diagram", diagram=flow_payload()["slide"]["blocks"][0]["diagram"])
            block.pop("items", None)
        return result


class FlowPipelineRecoveryTests(PipelineTestBase):
    def test_preparation_and_single_page_regeneration_publish_without_schema_repair(self):
        self.enterContext(mock.patch("app.core.config.settings.classroom_enabled", True))
        fake = LongFlowLLM()
        lesson, job_id = self._make_job(self._brief(
            theme_id="academic_clear@2", checkpoint_density="none"), renderer_version="2.0.0")

        def use_current_prompts(job):
            job.slide_prompt_version = "2.6.0"

        store.update_job(OWNER, WS, lesson, job_id, use_current_prompts)
        job = asyncio.run(self._pipeline(lesson, job_id, self._deps(fake)).run())
        self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
        base = store.load_revision(OWNER, WS, lesson, 1)
        self.assertEqual(len(fake.calls), 1 + len(base.slides))
        self.assertEqual(base.slides[1].blocks[0].kind, "paragraph")
        request = sc.CreateRevisionRequest(base_revision=1, operation=sc.RegenerateSlideOperation(
            slide_id=base.slides[1].slide_id, instruction="保留完整的系统分析"))
        created = revisions.create_revision_job(OWNER, WS, lesson, request, idempotency_key="long-flow")
        calls_before = len(fake.calls)
        regenerated = asyncio.run(self._pipeline(lesson, created["job_id"], self._deps(fake)).run())
        self.assertEqual(regenerated.state, sc.JobState.succeeded, regenerated.last_error)
        self.assertEqual(len(fake.calls) - calls_before, 1)
        updated = store.load_revision(OWNER, WS, lesson, 2)
        self.assertEqual(updated.slides[1].blocks[0].kind, "paragraph")
        self.assertIn(LONG_LABEL, "".join(span.text for span in updated.slides[1].blocks[0].spans))
        self.assertEqual(updated.slides[0], base.slides[0])

    def test_generation_input_states_actual_diagram_limits(self):
        lesson, job_id = self._make_job(renderer_version="2.0.0")
        hint = _slide_schema_hint(store.load_job(OWNER, WS, lesson, job_id))
        self.assertIn("40", hint["field_limits"]["diagram"])
        self.assertIn("nodes", hint["field_limits"]["diagram"])
        self.assertIn("edges", hint["field_limits"]["diagram"])
