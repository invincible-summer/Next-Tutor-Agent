"""Targeted edits preserve the deck and keep AI input bounded."""
import asyncio
import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from tests.support.storage_sandbox import authenticated_client

from tests.classroom.test_classroom_revisions import RevisionTestBase, NoLLM, _deps
from tests.classroom.test_classroom_pipeline import OWNER, WS
from app.classroom.block_edit import block_context, regenerate_block
from app.classroom.errors import ClassroomError
from app.classroom.revisions_ops import apply_block_change
from app.classroom import storage as store
from app.schemas import classroom as sc


class BlockEditTests(RevisionTestBase):
    def test_manual_block_edit_publishes_without_llm(self):
        lesson, _, base = self._published_course()
        slide = base.slides[0]
        block = next(b for b in slide.blocks if b.kind == "paragraph")
        replacement = block.model_copy(update={"spans": [sc.SpanText(text="先选定系统边界。")]})
        job = self._run_operation(lesson, self._request(1, sc.EditContentOperation(changes=[
            sc.ReplaceBlockChange(slide_id=slide.slide_id, block_id=block.id,
                                  block=replacement)])), _deps(llm=NoLLM()))
        self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
        updated = store.load_revision(OWNER, WS, lesson, 2)
        self.assertEqual(updated.slides[1:], base.slides[1:])
        self.assertEqual(updated.slides[0].segments, slide.segments)
        self.assertEqual(store.load_revision(OWNER, WS, lesson, 1), base)
        self.assertEqual(updated.slides[0].blocks[0], replacement)

    def test_ai_only_sees_target_and_publishes_one_component(self):
        lesson, _, base = self._published_course()
        slide = base.slides[0]
        block = next(b for b in slide.blocks if b.kind == "paragraph")
        replacement = block.model_copy(update={"spans": [sc.SpanText(text="先判断系统所受的合外力。")]})
        llm = type("LLM", (), {})()
        llm.complete = AsyncMock(return_value=(json.dumps({"block": replacement.model_dump()}), {}))
        operation = sc.RegenerateBlockOperation(slide_id=slide.slide_id,
            block_id=block.id, instruction="更清楚")
        job = self._run_operation(lesson, self._request(1, operation), _deps(llm=llm))
        self.assertEqual(job.state, sc.JobState.succeeded, job.last_error)
        llm.complete.assert_awaited_once()
        args = llm.complete.call_args
        payload = json.loads(args.args[0][1]["content"])
        self.assertEqual(payload["block"]["id"], block.id)
        self.assertNotIn("slides", payload)
        self.assertNotIn("html", payload)
        self.assertLessEqual(args.kwargs["max_tokens"], 3000)
        for other in base.slides[1:]:
            self.assertNotIn(other.slide_id, args.args[0][1]["content"])
        updated = store.load_revision(OWNER, WS, lesson, 2)
        self.assertEqual(updated.slides[1:], base.slides[1:])
        self.assertEqual(updated.slides[0].segments, slide.segments)

    def test_invalid_ai_is_not_retried_or_applied(self):
        _, _, base = self._published_course()
        slide, block = base.slides[0], base.slides[0].blocks[0]
        llm = type("LLM", (), {})()
        llm.complete = AsyncMock(return_value=("{truncated", {}))
        with self.assertRaises(ClassroomError):
            asyncio.run(regenerate_block(llm, base, sc.RegenerateBlockOperation(
                slide_id=slide.slide_id, block_id=block.id, instruction="精简")))
        llm.complete.assert_awaited_once()
        changed_id = block.model_copy(update={"id": "blk_" + "f" * 24})
        with self.assertRaises(ClassroomError):
            apply_block_change(base, slide.slide_id, block.id, changed_id)
        checkpoint_slide = next(s for s in base.slides if any(b.kind == "checkpoint" for b in s.blocks))
        checkpoint = next(b for b in checkpoint_slide.blocks if b.kind == "checkpoint")
        with self.assertRaises(ClassroomError):
            block_context(base, sc.RegenerateBlockOperation(slide_id=checkpoint_slide.slide_id,
                block_id=checkpoint.id, instruction="修改"))

    def test_revision_route_accepts_component_and_enforces_owner(self):
        from app.main import create_app
        from app.identity.deps import resolve_student_id
        lesson, _, base = self._published_course()
        slide, block = base.slides[0], base.slides[0].blocks[0]
        app = create_app()
        app.dependency_overrides[resolve_student_id] = lambda: OWNER
        client = authenticated_client(app, OWNER)
        body = {"base_revision": 1, "operation": {"op": "regenerate_block",
            "slide_id": slide.slide_id, "block_id": block.id, "instruction": "精简"}}
        url = f"/api/v1/workspaces/{WS}/classroom/lessons/{lesson}/revisions"
        response = client.post(url, json=body, headers={"Idempotency-Key": "component-edit-route-001"})
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        app.dependency_overrides[resolve_student_id] = lambda: "usr_otherowner"
        self.assertEqual(client.post(url, json=body, headers={
            "Idempotency-Key": "component-edit-route-002"}).status_code, 404)
