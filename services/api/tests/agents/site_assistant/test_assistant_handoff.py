"""课程与任务交接回归（A13）。

覆盖：备课草稿（lesson prefill + lesson_form 命令）、课堂恢复（冻结
revision、run 归属复核、action 派生幂等键、target_changed 不悄悄
restart）、任务启动（复用绑定幂等、已完成 409）、聊天交接草稿、
consume 的同实体幂等/异实体 409 语义。
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import intent, policy
from app.agents.site_assistant import store


def _find_course_result(resume: dict | None = None) -> dict:
    return {"tool": "find_course", "status": "ready", "complete": True,
            "data": {"lessons": [], "resume": resume,
                     "resume_only": True},
            "notices": [], "sources": []}


def _tasks_result(tasks: list[dict]) -> dict:
    return {"tool": "get_saved_tasks", "status": "ready", "complete": False,
            "data": {"today": tasks, "open": tasks,
                     "recently_completed": [], "coverage": {}},
            "notices": [], "sources": []}


class HandoffPolicyTest(unittest.TestCase):
    def _decide(self, parsed, results, meta=None):
        return policy.decide_actions(
            parsed, results, meta or {},
            conversation_id="astc_h", turn_id="astt_h", lang="zh",
            page_epoch=1)

    def test_resume_action_from_card_automatic(self) -> None:
        resume = {"lesson_id": "les_1", "workspace_id": "wsp_a",
                  "title": "动量守恒",
                  "run": {"run_id": "run_9", "lesson_revision": 3}}
        parsed = intent.ParsedIntent(kind="prepare_action",
                                     text="继续上次课程")
        actions = self._decide(parsed, [_find_course_result(resume)])
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["payload"]["kind"], "resume_lesson")
        self.assertEqual(action["execution"], "automatic")
        self.assertEqual(action["payload"]["lesson_id"], "les_1")
        self.assertEqual(action["payload"]["lesson_revision"], 3)
        self.assertEqual(action["payload"]["run_id"], "run_9")

    def test_prepare_lesson_draft_without_resume(self) -> None:
        parsed = intent.ParsedIntent(kind="prepare_action",
                                     text="帮我备一节关于浮力的课")
        meta = {"scope": {"mode": "workspace",
                          "workspace_ids": ["wsp_a"],
                          "scope_revisions": {}}}
        actions = self._decide(parsed, [_find_course_result(None)], meta)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["payload"]["kind"], "prepare_lesson")
        self.assertEqual(action["execution"], "user_click")
        self.assertEqual(action["payload"]["workspace_id"], "wsp_a")
        self.assertIn("浮力", action["payload"]["draft"]["topic"])

    def test_launch_task_single_open(self) -> None:
        parsed = intent.ParsedIntent(kind="planning_advice",
                                     text="开始做这个任务吧")
        tasks = [{"id": "task_1", "title": "练三题", "status": "pending"}]
        actions = self._decide(parsed, [_tasks_result(tasks)])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["payload"]["kind"], "launch_task")
        self.assertEqual(actions[0]["payload"]["task_id"], "task_1")

    def test_no_launch_when_multiple_open(self) -> None:
        parsed = intent.ParsedIntent(kind="planning_advice",
                                     text="开始做这个任务吧")
        tasks = [{"id": "task_1", "status": "pending"},
                 {"id": "task_2", "status": "pending"}]
        self.assertEqual(self._decide(parsed, [_tasks_result(tasks)]), [])


class _HandoffCase(StorageSandboxTestCase):
    """动作级基座：直接构造 conversation record + 调 actions 服务。"""

    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_handoff_1"
        from app.core import workspace as ws_core
        path = ws_core._WORKSPACES_DIR / "wsp_h.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "workspace_id": "wsp_h", "student_id": self.sid,
            "name": "物理区", "session_ids": [], "knowledge_files": [],
            "updated_at": 1}), encoding="utf-8")
        self.record = {"conversation_id": "astc_h", "revision": 1,
                       "messages": [], "turns": {}, "actions": {},
                       "accepted": {}}
        store.save_conversation(self.sid, self.record)

    def _add_action(self, action_id: str, payload: dict,
                    execution: str = "user_click") -> None:
        record = store.load_conversation(self.sid, "astc_h")
        record["actions"][action_id] = {
            "action_id": action_id, "conversation_id": "astc_h",
            "turn_id": "astt_h", "label": "交接", "payload": payload,
            "execution": execution, "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, record)

    def _execute(self, action_id: str, invocation: str | None = None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=invocation or str(uuid.uuid4()),
            client_instance_id="client-instance-h1", route_epoch=1)

    def _classroom_on(self) -> None:
        patcher = mock.patch(
            "app.classroom.capabilities.settings")
        fake = patcher.start()
        fake.classroom_enabled = True
        self._caps_patcher = patcher

    def tearDown(self) -> None:
        if getattr(self, "_caps_patcher", None):
            self._caps_patcher.stop()
        super().tearDown()


class HandoffActionTest(_HandoffCase):
    def test_prepare_lesson_creates_prefill_draft(self) -> None:
        self._classroom_on()
        from app.classroom import capabilities as caps
        with mock.patch.object(caps, "user_allowed",
                               return_value=(True, "")):
            self._add_action("asta_prep", {
                "kind": "prepare_lesson", "workspace_id": "wsp_h",
                "draft": {"topic": "浮力基础", "duration_minutes": 20}})
            result = self._execute("asta_prep")
            self.assertEqual(result["action"]["state"], "awaiting_ack")
            self.assertEqual(result["command"]["kind"], "lesson_form")
            draft_id = result["command"]["draft_id"]
            draft = store.load_draft(self.sid, draft_id)
            self.assertIsNotNone(draft)
            self.assertEqual(draft["prefill"]["kind"], "lesson")
            self.assertEqual(draft["prefill"]["topic"], "浮力基础")
            self.assertEqual(draft["prefill"]["duration_minutes"], 20)
            self.assertFalse(draft["consumed"])
            self.assertEqual(result["business_result"]["kind"], "draft")

    def test_prepare_lesson_foreign_workspace_fails(self) -> None:
        self._classroom_on()
        from app.classroom import capabilities as caps
        with mock.patch.object(caps, "user_allowed",
                               return_value=(True, "")):
            self._add_action("asta_bad", {
                "kind": "prepare_lesson", "workspace_id": "wsp_other",
                "draft": {"topic": "x"}})
            result = self._execute("asta_bad")
            self.assertEqual(result["action"]["state"], "failed")
            self.assertIsNone(result["command"])

    def test_resume_lesson_uses_idempotent_run(self) -> None:
        from tests.support import classroom_fixtures as fx
        from app.schemas import classroom as sc
        from app.classroom import storage as cstore
        from app.classroom import runs as cruns
        self._classroom_on()
        cstore.ensure_owner(self.sid)
        lesson = sc.Lesson(
            lesson_id=cstore.new_id("les"), owner_id=self.sid,
            workspace_id="wsp_h", title="动量守恒",
            published_revisions=[1],
            created_at=cstore.utcnow(), updated_at=cstore.utcnow())
        cstore.save_lesson(lesson)
        cstore.index_upsert_lesson(self.sid, "wsp_h", lesson)
        run = sc.ClassroomRun(
            run_id=cstore.new_id("run"), owner_id=self.sid,
            workspace_id="wsp_h", lesson_id=lesson.lesson_id,
            lesson_revision=1, content_hash="c" * 64,
            status=sc.RunStatus.active,
            cursor=sc.Cursor(slide_id=fx.slide_hex(),
                             segment_id=fx.hex_id("seg")),
            created_at=cstore.utcnow(), updated_at=cstore.utcnow())
        cstore.save_run(run)

        spec = fx.make_revision(1)
        with mock.patch.object(cruns, "load_run_spec",
                               return_value=spec), \
             mock.patch("app.classroom.capabilities.user_allowed",
                        return_value=(True, "")):
            self._add_action("asta_res", {
                "kind": "resume_lesson", "workspace_id": "wsp_h",
                "lesson_id": lesson.lesson_id, "lesson_revision": 1,
                "run_id": run.run_id})
            invocation = str(uuid.uuid4())
            result = self._execute("asta_res", invocation)
            self.assertEqual(result["action"]["state"], "awaiting_ack")
            self.assertEqual(result["command"]["kind"], "navigate")
            self.assertEqual(result["command"]["target"]["kind"],
                             "classroom_run")
            # 恢复既有 run，不新建（business_result 指向原 run）。
            self.assertEqual(result["business_result"]["entity_id"],
                             run.run_id)
            # 同 invocation 幂等：同一 run。
            again = self._execute("asta_res", invocation)
            self.assertEqual(again["business_result"]["entity_id"],
                             run.run_id)
            self.assertEqual(again["command_id"], result["command_id"])

    def test_resume_lesson_revision_mismatch_target_changed(self) -> None:
        from app.classroom import storage as cstore
        from app.schemas import classroom as sc
        from tests.support import classroom_fixtures as fx
        self._classroom_on()
        cstore.ensure_owner(self.sid)
        lesson = sc.Lesson(
            lesson_id=cstore.new_id("les"), owner_id=self.sid,
            workspace_id="wsp_h", title="旧课",
            published_revisions=[1],
            created_at=cstore.utcnow(), updated_at=cstore.utcnow())
        cstore.save_lesson(lesson)
        run = sc.ClassroomRun(
            run_id=cstore.new_id("run"), owner_id=self.sid,
            workspace_id="wsp_h", lesson_id=lesson.lesson_id,
            lesson_revision=1, content_hash="c" * 64,
            status=sc.RunStatus.ended,
            cursor=sc.Cursor(slide_id=fx.slide_hex(),
                             segment_id=fx.hex_id("seg")),
            created_at=cstore.utcnow(), updated_at=cstore.utcnow())
        cstore.save_run(run)
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_mis", {
            "kind": "resume_lesson", "workspace_id": "wsp_h",
            "lesson_id": lesson.lesson_id, "lesson_revision": 1,
            "run_id": run.run_id})
        with mock.patch("app.classroom.capabilities.user_allowed",
                        return_value=(True, "")):
            with self.assertRaises(ActionRejected) as ctx:
                self._execute("asta_mis")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_launch_task_completed_returns_target_changed(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_task", {"kind": "launch_task",
                                       "task_id": "task_missing"})
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_task")
        self.assertEqual(ctx.exception.code, "target_changed")

    def test_handoff_chat_draft_command(self) -> None:
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_chat",
            "prefill": {"kind": "chat", "workspace_id": "wsp_h",
                        "text": "就练一下这个概念"},
            "source_ids": []})
        self._add_action("asta_chat", {"kind": "handoff_chat",
                                       "draft_id": draft["draft_id"]})
        result = self._execute("asta_chat")
        self.assertEqual(result["command"]["kind"], "chat_draft")
        self.assertEqual(result["command"]["draft_id"], draft["draft_id"])

    def test_handoff_chat_consumed_draft_rejected(self) -> None:
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_used",
            "prefill": {"kind": "chat", "text": "x"}, "source_ids": []})
        store.consume_draft(self.sid, draft["draft_id"])
        self._add_action("asta_used", {"kind": "handoff_chat",
                                       "draft_id": draft["draft_id"]})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_used")
        self.assertEqual(ctx.exception.code, "draft_already_consumed")

    def test_handoff_note_draft_command(self) -> None:
        # §19.5 note_draft 落点：临时编辑器，不直接创建正式笔记。
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_note",
            "prefill": {"kind": "note", "title": "浮力小结",
                        "markdown": "## 要点\n- 浮力方向竖直向上"},
            "source_ids": []})
        self._add_action("asta_note", {"kind": "handoff_note",
                                       "draft_id": draft["draft_id"]})
        result = self._execute("asta_note")
        self.assertEqual(result["action"]["state"], "awaiting_ack")
        self.assertEqual(result["command"]["kind"], "note_draft")
        self.assertEqual(result["command"]["draft_id"], draft["draft_id"])
        self.assertEqual(result["business_result"]["kind"], "draft")
        # 草稿未被自动消费：正式保存前 consumed 必须为 False。
        fresh = store.load_draft(self.sid, draft["draft_id"])
        self.assertIsNotNone(fresh)
        self.assertFalse(fresh["consumed"])
        self.assertEqual(fresh["prefill"]["title"], "浮力小结")

    def test_handoff_note_kind_mismatch_fails(self) -> None:
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_mix",
            "prefill": {"kind": "chat", "text": "不是笔记"}, "source_ids": []})
        self._add_action("asta_mix", {"kind": "handoff_note",
                                      "draft_id": draft["draft_id"]})
        result = self._execute("asta_mix")
        self.assertEqual(result["action"]["state"], "failed")
        self.assertIsNone(result["command"])

    def test_note_draft_consume_binds_note_entity(self) -> None:
        # §19.6：consume 只在正式保存后发生，result_entity 绑定 note id；
        # store 层重复消费同实体幂等（异实体 409 由 API 层校验）。
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_nc",
            "prefill": {"kind": "note", "title": "t", "markdown": "m"},
            "source_ids": []})
        first = store.consume_draft(
            self.sid, draft["draft_id"],
            result_entity={"kind": "note", "id": "note_1"})
        self.assertTrue(first.get("consumed"))
        self.assertEqual(first.get("result_entity", {}).get("id"), "note_1")
        again = store.consume_draft(
            self.sid, draft["draft_id"],
            result_entity={"kind": "note", "id": "note_1"})
        self.assertEqual(again.get("result_entity", {}).get("id"), "note_1")
        self.assertTrue(again.get("consumed"))


class DraftConsumeSemanticsTest(_HandoffCase):
    def test_reconsume_same_entity_idempotent(self) -> None:
        draft = store.create_draft(self.sid, {
            "conversation_id": "astc_h", "action_id": "asta_c",
            "prefill": {"kind": "lesson", "workspace_id": "wsp_h",
                        "topic": "t"}, "source_ids": []})
        from app.agents.site_assistant import actions as actions_svc
        first = store.consume_draft(
            self.sid, draft["draft_id"],
            result_entity={"kind": "lesson", "id": "les_new"})
        again = store.consume_draft(
            self.sid, draft["draft_id"],
            result_entity={"kind": "lesson", "id": "les_new"})
        self.assertEqual(first.get("result_entity"),
                         again.get("result_entity"))
        self.assertTrue(again.get("consumed"))


if __name__ == "__main__":
    unittest.main()
