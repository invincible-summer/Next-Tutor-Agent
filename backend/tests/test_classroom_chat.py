"""课堂问答接入回归（plan.md §12.4、§19.2 test_classroom_chat）。

覆盖：服务端上下文构造（材料区边界、无答案泄漏）、qa_session 幂等创建
与 crash 恢复、外来 owner/坏 ref 拒绝、v2/legacy 两条执行路径的材料区
注入与用户原文保持、chat/stream 的 classroom_ref 验证与会话绑定。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.classroom import runs as runs_mod  # noqa: E402
from app.classroom.chat_context import (  # noqa: E402
    resolve_classroom_turn)
from app.classroom.errors import ClassroomError  # noqa: E402
from app.core import classroom_store as store  # noqa: E402
from app.core.session import load_session, session_path  # noqa: E402
from app.schemas.chat import ClassroomRef  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests.test_classroom_pipeline import OWNER, WS  # noqa: E402
from tests.test_classroom_revisions import RevisionTestBase, _deps  # noqa: E402


class _CapturingLLM:
    """记录收到的 messages，稳定返回短答案。"""

    def __init__(self):
        self.seen_messages: list[list[dict]] = []

    async def stream(self, messages, tools=None, temperature=None,
                     max_tokens=None, **kw):
        self.seen_messages.append([dict(m) for m in messages])
        yield {"kind": "answer", "delta": "内力成对抵消，总动量不变。"}
        yield {"kind": "done", "finish_reason": "stop",
               "usage": {"total_tokens": 10}}

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        return "内力成对抵消，总动量不变。", {"total_tokens": 10}


def _ref(run, slide, segment=None) -> ClassroomRef:
    return ClassroomRef(run_id=run.run_id, lesson_revision=run.lesson_revision,
                        slide_id=slide.slide_id, segment_id=segment,
                        workspace_id=run.workspace_id, lesson_id=run.lesson_id)


class ClassroomChatTests(RevisionTestBase):
    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        for p in [
            mock.patch.object(settings, "classroom_enabled", True),
            mock.patch.object(settings, "azure_speech_key", "k"),
            mock.patch.object(settings, "azure_speech_region", "eastasia"),
            mock.patch.object(settings, "classroom_tts_voice_zh",
                              "zh-CN-XiaoxiaoNeural"),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.lesson_id, _job, base = self._published_course()
        self.spec = base
        self.created = runs_mod.create_run(
            OWNER, WS, self.lesson_id, sc.CreateRunRequest(),
            idempotency_key="k-qa-create-00000001")
        self.run = store.load_run(OWNER, WS, self.lesson_id,
                                  self.created["run_id"])
        self.first_slide = min(base.slides, key=lambda s: s.order)

    def _resolve(self, ref=None):
        return resolve_classroom_turn(
            OWNER, ref or _ref(self.run, self.first_slide))

    # ---- 材料区与绑定 ------------------------------------------------------

    def test_material_block_contents(self):
        ctx = self._resolve()
        block = ctx.material_block
        self.assertIn("<classroom_context>", block)
        self.assertIn(f"第 {self.first_slide.order} 页", block)
        self.assertIn(self.first_slide.title, block)
        self.assertIn("<material_excerpt", block)
        # 当前段讲稿进入材料区；讲稿事实与学生消息分开
        seg = self.first_slide.segments[0]
        self.assertIn(seg.display_text[:12], block)
        self.assertIn("不得提前透露", block)

    def test_material_block_no_checkpoint_answer(self):
        # 检查点（含私有材料）不得出现在材料区（§12.4.3）
        ctx = self._resolve()
        for template in self.spec.checkpoint_templates:
            self.assertNotIn(template.checkpoint_id, ctx.material_block)
            private = getattr(template, "verified_question_template", None)
            if private is not None:
                blob = str(private)
                self.assertNotIn(blob[:40], ctx.material_block)

    def test_qa_session_created_once_and_bound(self):
        ctx = self._resolve()
        stored = store.load_run(OWNER, WS, self.lesson_id, self.run.run_id)
        self.assertEqual(stored.qa_session_id, ctx.qa_session.session_id)
        self.assertTrue(ctx.qa_session.title.startswith("课堂答疑 ·"))
        self.assertEqual(ctx.qa_session.workspace_id, WS)
        self.assertEqual(ctx.qa_session.student_id, OWNER)
        # 幂等：第二次 resolve 复用同一 session
        again = self._resolve()
        self.assertEqual(again.qa_session.session_id,
                         ctx.qa_session.session_id)

    def test_qa_session_crash_recovery_same_id(self):
        ctx = self._resolve()
        # 模拟「run 已预留 ID、session 文件尚未落盘」的崩溃窗口
        session_path(ctx.qa_session.session_id).unlink()
        recovered = self._resolve()
        self.assertEqual(recovered.qa_session.session_id,
                         ctx.qa_session.session_id)
        self.assertIsNotNone(load_session(ctx.qa_session.session_id))

    # ---- 拒绝路径 ------------------------------------------------------------

    def test_foreign_owner_gets_404_semantics(self):
        ref = _ref(self.run, self.first_slide)
        with self.assertRaises(ClassroomError) as ctx:
            resolve_classroom_turn("usr_classroom_intruder", ref)
        self.assertEqual(ctx.exception.code.value, "source_not_found")

    def test_bad_revision_slide_segment_rejected(self):
        with self.assertRaises(ClassroomError) as ctx:
            self._resolve(ClassroomRef(
                run_id=self.run.run_id, lesson_revision=99,
                slide_id=self.first_slide.slide_id,
                workspace_id=WS, lesson_id=self.lesson_id))
        self.assertEqual(ctx.exception.code.value, "revision_conflict")
        with self.assertRaises(ClassroomError) as ctx:
            self._resolve(ClassroomRef(
                run_id=self.run.run_id, lesson_revision=1,
                slide_id="s_ffffffffffff",
                workspace_id=WS, lesson_id=self.lesson_id))
        self.assertEqual(ctx.exception.code.value, "content_invalid")
        with self.assertRaises(ClassroomError) as ctx:
            self._resolve(ClassroomRef(
                run_id=self.run.run_id, lesson_revision=1,
                slide_id=self.first_slide.slide_id,
                segment_id="seg_ffffffffffffffffffffffff",
                workspace_id=WS, lesson_id=self.lesson_id))
        self.assertEqual(ctx.exception.code.value, "content_invalid")

    # ---- 执行路径注入（v2 / legacy 共用同一 helper） ----------------------

    def _run_turn_events(self, mode: str, ctx) -> tuple[list[dict], _CapturingLLM]:
        from app.agents import chat_agent as ca

        llm = _CapturingLLM()

        async def _collect() -> list[dict]:
            out = []
            async for ev in ca.run_turn(
                    "为什么内力不改变总动量？", ctx.qa_session, [],
                    llm=llm, student_id=OWNER, classroom_context=ctx):
                out.append(ev)
            return out

        with mock.patch.dict("os.environ", {"SUPERVISOR_MODE": mode}):
            events = asyncio.run(_collect())
        return events, llm

    def test_both_paths_inject_material_and_keep_user_text(self):
        for mode in ("v2", "legacy"):
            with self.subTest(mode=mode):
                ctx = self._resolve()
                ctx.qa_session.knowledge = type(ctx.qa_session.knowledge)()
                events, llm = self._run_turn_events(mode, ctx)
                kinds = {ev.get("type") for ev in events}
                self.assertIn("done", kinds)
                # build_context 把 preamble 放在 user 角色消息里（L2），
                # 材料区断言覆盖全部消息内容。
                flattened = "\n".join(
                    str(m.get("content", "")) for call in llm.seen_messages
                    for m in call)
                self.assertIn("<classroom_context>", flattened)
                self.assertIn(self.first_slide.title, flattened)
                # 用户消息原文保持原样（仅 <user_input> 定界包裹），不拼讲稿
                user_msgs = [m for call in llm.seen_messages for m in call
                             if m.get("role") == "user"]
                self.assertTrue(any(
                    "为什么内力不改变总动量？" in str(m.get("content", ""))
                    for m in user_msgs))
                # 材料区绝不能被伪装进用户消息
                self.assertFalse(any(
                    "<classroom_context>" in str(m.get("content", ""))
                    and str(m.get("content", "")).strip()
                        .startswith("<classroom_context>")
                    for m in user_msgs))

    def test_no_context_leaves_normal_chat_unchanged(self):
        from app.agents import chat_agent as ca
        from app.core.session import TutorSession

        llm = _CapturingLLM()
        session = TutorSession(session_id="sess_plain_chat", grade="本科")
        session.student_id = OWNER

        async def _collect() -> list[dict]:
            out = []
            async for ev in ca.run_turn("普通问题", session, [], llm=llm,
                                        student_id=OWNER):
                out.append(ev)
            return out

        with mock.patch.dict("os.environ", {"SUPERVISOR_MODE": "v2"}):
            asyncio.run(_collect())
        flattened = "\n".join(
            str(m.get("content", "")) for call in llm.seen_messages
            for m in call)
        self.assertNotIn("<classroom_context>", flattened)
    # ---- API 层：POST /chat/stream 携带 classroom_ref ----------------------

    def test_chat_stream_validates_and_threads_context(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.identity import deps as id_deps

        captured: dict[str, Any] = {}

        async def _fake_run_turn(message, session, tools, **kw):
            captured["message"] = message
            captured["session_id"] = session.session_id
            captured["classroom_context"] = kw.get("classroom_context")
            yield {"type": "answer", "content": "好的。", "is_delta": False}
            yield {"type": "done", "answer": "好的。", "thinking": "",
                   "tool_calls": [], "trace_id": ""}

        def _tools(session, **kw):
            captured["restrict"] = kw.get("restrict_to_file_ids")
            return []

        app = create_app()
        app.dependency_overrides[id_deps.resolve_student_id] = lambda: OWNER
        client = TestClient(app)
        ref = _ref(self.run, self.first_slide)
        with mock.patch("app.api.v1.chat._build_tools", _tools), \
             mock.patch("app.agents.chat_agent.run_turn", _fake_run_turn), \
             mock.patch("app.core.llm_async.get_llm", lambda: object()):
            r = client.post("/api/v1/chat/stream", json={
                "message": "没听懂这一段",
                "classroom_ref": ref.model_dump(),
            })
        self.assertEqual(r.status_code, 200)
        self.assertIn("history_saved", r.text)
        self.assertEqual(captured["message"], "没听懂这一段")
        self.assertIsNotNone(captured["classroom_context"])
        self.assertIn("<classroom_context>",
                      captured["classroom_context"].material_block)
        # 可信来源 override：受限集合来自冻结 spec（非 None）
        self.assertIsInstance(captured["restrict"], (set, frozenset))

    def test_chat_stream_rejects_foreign_session_binding(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.identity import deps as id_deps

        app = create_app()
        app.dependency_overrides[id_deps.resolve_student_id] = lambda: OWNER
        client = TestClient(app)
        ref = _ref(self.run, self.first_slide)
        r = client.post("/api/v1/chat/stream", json={
            "message": "提问", "session_id": "sess_someone_else",
            "classroom_ref": ref.model_dump(),
        })
        self.assertEqual(r.status_code, 409)

    def test_qa_session_endpoint_idempotent(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.identity import deps as id_deps

        app = create_app()
        app.dependency_overrides[id_deps.resolve_student_id] = lambda: OWNER
        client = TestClient(app)
        url = (f"/api/v1/workspaces/{WS}/classroom/lessons/{self.lesson_id}"
               f"/runs/{self.run.run_id}/qa-session")
        r1 = client.post(url, headers={"Idempotency-Key": "k-qa-ep-0000000001"})
        self.assertEqual(r1.status_code, 201)
        r2 = client.post(url, headers={"Idempotency-Key": "k-qa-ep-0000000002"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["session_id"], r2.json()["session_id"])
        stored = store.load_run(OWNER, WS, self.lesson_id, self.run.run_id)
        self.assertEqual(stored.qa_session_id, r1.json()["session_id"])


if __name__ == "__main__":
    unittest.main()
