"""B03 预览与执行许可协议回归（plan.md §21.4/§21.2）。

覆盖：preview 确定性与同参数幂等；review_required（note.create）无许可
execute 返回 409/preview_stale、approve 后可执行；intent_sufficient
（task.create）直接执行成功且 business_result 绑定实体；参数变化使旧
许可失效；operation/input 配对校验。
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.storage_sandbox import StorageSandboxTestCase

from app.core import assistant_store as store


def _hex32() -> str:
    return uuid.uuid4().hex


class _PreviewCase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_preview_1"
        self.record = {"conversation_id": "astc_p", "revision": 1,
                       "messages": [], "turns": {}, "actions": {},
                       "accepted": {}}
        store.save_conversation(self.sid, self.record)

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, "astc_p")
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": "astc_p",
            "turn_id": "astt_p", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id=None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-preview-1", route_epoch=1,
            approval_id=approval_id)


class PreviewProtocolTest(_PreviewCase):
    def test_preview_deterministic_and_idempotent(self) -> None:
        from app.agents.site_assistant import previews
        payload = {"kind": "domain_write", "operation": "schedule.update",
                   "input": {"daily_minutes": 30}}
        self._add_action("asta_pv", payload)
        p1 = previews.build_preview(self.sid, "asta_pv")
        p2 = previews.build_preview(self.sid, "asta_pv")
        self.assertEqual(p1["preview_id"], p2["preview_id"])
        self.assertEqual(p1["approval"], "intent_sufficient")
        self.assertTrue(p1["reversible"])
        self.assertEqual(p1["changes"][0]["after"], "30")
        self.assertEqual(p1["parameter_hash"],
                         previews.parameter_hash("schedule.update",
                                                  {"daily_minutes": 30}))

    def test_note_create_requires_approval(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        payload = {"kind": "domain_write", "operation": "note.create",
                   "input": {"title": "浮力小结", "content": "AI 生成的正文"}}
        self._add_action("asta_note", payload)
        preview = previews.build_preview(self.sid, "asta_note")
        self.assertEqual(preview["approval"], "review_required")
        self.assertIn("AI 生成", preview["changes"][0]["label"])
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_note")
        self.assertEqual(ctx.exception.code, "preview_stale")
        approval = previews.approve(
            self.sid, "asta_note", preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        result = self._execute("asta_note", approval["approval_id"])
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(result["business_result"]["kind"], "note")

    def test_parameter_change_invalidates_approval(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        payload = {"kind": "domain_write", "operation": "note.create",
                   "input": {"title": "t", "content": "v1"}}
        self._add_action("asta_st", payload)
        preview = previews.build_preview(self.sid, "asta_st")
        approval = previews.approve(
            self.sid, "asta_st", preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        # 参数被篡改：旧许可的 hash 不再匹配 → 409/preview_stale。
        rec = store.load_conversation(self.sid, "astc_p")
        rec["actions"]["asta_st"]["payload"]["input"]["content"] = "v2"
        store.save_conversation(self.sid, rec)
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_st", approval["approval_id"])
        self.assertEqual(ctx.exception.code, "preview_stale")

    def test_task_create_executes_without_approval(self) -> None:
        from app.agents.learning_orchestration.store import load_state
        payload = {"kind": "domain_write", "operation": "task.create",
                   "input": {"title": "复习错题两道", "day": ""}}
        self._add_action("asta_tc", payload)
        result = self._execute("asta_tc")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(result["business_result"]["kind"], "task")
        task_id = result["business_result"]["entity_id"]
        state = load_state(self.sid)
        self.assertIn(task_id, {t.id for t in state.daily_tasks})
        # B04 幂等：模拟 failed→retry 场景，直接再次执行领域写入，
        # 已记录的 business_result 使其复用原实体而不重复创建。
        from app.agents.site_assistant import previews
        rec = store.load_conversation(self.sid, "astc_p")
        again = previews.execute_domain_write(self.sid, rec["actions"]["asta_tc"])
        self.assertEqual(again["entity_id"], task_id)
        state2 = load_state(self.sid)
        self.assertEqual(len([t for t in state2.daily_tasks
                              if t.id == task_id]), 1)

    def test_domain_write_reexecute_reuses_entity(self) -> None:
        # B04：已含 business_result 的动作直接返回原结果（幂等短路）。
        from app.agents.site_assistant import previews
        payload = {"kind": "domain_write", "operation": "note.create",
                   "input": {"title": "t", "content": "c"}}
        self._add_action("asta_idem", payload)
        rec = store.load_conversation(self.sid, "astc_p")
        rec["actions"]["asta_idem"]["business_result"] = {
            "kind": "note", "entity_id": "note_existing", "related_ids": {},
            "result_revision": "1"}
        store.save_conversation(self.sid, rec)
        out = previews.execute_domain_write(
            self.sid, store.load_conversation(self.sid, "astc_p")
            ["actions"]["asta_idem"])
        self.assertEqual(out["entity_id"], "note_existing")

    def test_operation_input_pairing_strict(self) -> None:
        from app.schemas.assistant import AssistantAction
        import pydantic
        base = dict(action_id=f"asta_{_hex32()}",
                    conversation_id=f"astc_{_hex32()}",
                    turn_id=f"astt_{_hex32()}", label="x",
                    created_at=datetime.now(tz=timezone.utc),
                    expires_at=datetime.now(tz=timezone.utc))
        with self.assertRaises(pydantic.ValidationError):
            AssistantAction(payload={
                "kind": "domain_write", "operation": "chat.rename",
                "input": {"title": "mismatch"}}, **base)

    def test_reject_decision_cancels_action(self) -> None:
        from app.agents.site_assistant import previews
        payload = {"kind": "domain_write", "operation": "note.create",
                   "input": {"title": "t", "content": "c"}}
        self._add_action("asta_rj", payload)
        preview = previews.build_preview(self.sid, "asta_rj")
        previews.approve(self.sid, "asta_rj",
                         preview_id=preview["preview_id"],
                         parameter_hash_in=preview["parameter_hash"],
                         decision="reject")
        rec = store.load_conversation(self.sid, "astc_p")
        self.assertEqual(rec["actions"]["asta_rj"]["state"], "cancelled")


class DomainWriteProposalTest(unittest.TestCase):
    """§21 领域写提案：policy 从明确指令确定性解析 operation+input。"""

    def _decide(self, text: str, *, results=None, page_context=None,
                timezone_name="UTC"):
        from app.agents.site_assistant import intent as intent_mod
        from app.agents.site_assistant import policy
        parsed = intent_mod.ParsedIntent(
            kind="prepare_action", text=text, confidence="strong")
        return policy.decide_actions(
            parsed, results or [], {}, conversation_id="astc_w",
            turn_id="astt_w", page_epoch=1,
            page_context=page_context, timezone_name=timezone_name)

    def test_task_create_proposal(self) -> None:
        actions = self._decide("帮我创建一个明天到期的任务：复习牛顿定律，预计40分钟")
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["kind"], "domain_write")
        self.assertEqual(payload["operation"], "task.create")
        self.assertEqual(payload["input"]["title"], "复习牛顿定律")
        self.assertEqual(payload["input"]["estimate_minutes"], 40)
        self.assertTrue(payload["input"]["day"])  # 明天 → 本地日期
        self.assertEqual(actions[0]["execution"], "automatic")

    def test_task_create_without_content_no_action(self) -> None:
        # 参数不完整不生成动作（不猜测）
        actions = self._decide("创建任务")
        self.assertEqual(actions, [])

    def test_task_complete_unique_match(self) -> None:
        results = [{"tool": "get_saved_tasks", "data": {
            "today": [{"id": "tsk_1", "title": "复习电磁感应",
                       "status": "pending"}],
            "open": [],
        }}]
        actions = self._decide("我完成了任务：复习电磁感应", results=results)
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["operation"], "task.complete")
        self.assertEqual(payload["input"]["task_id"], "tsk_1")
        self.assertEqual(payload["input"]["expected_status"], "pending")

    def test_task_complete_ambiguous_no_action(self) -> None:
        results = [{"tool": "get_saved_tasks", "data": {
            "today": [
                {"id": "tsk_1", "title": "复习电磁感应", "status": "pending"},
                {"id": "tsk_2", "title": "复习电磁感应进阶",
                 "status": "pending"},
            ],
            "open": [],
        }}]
        # 无标题限定 + 多个未完成任务 → 不猜测
        actions = self._decide("我完成任务了", results=results)
        self.assertEqual(actions, [])

    def test_chat_rename_requires_page_entity(self) -> None:
        actions = self._decide("把这个对话改名为物理答疑")
        self.assertEqual(actions, [])
        actions = self._decide(
            "把这个对话改名为物理答疑",
            page_context={"route_id": "chat", "workspace_id": "wsp_a",
                          "entity": {"kind": "chat", "id": "ses_1"}})
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["operation"], "chat.rename")
        self.assertEqual(payload["input"]["session_id"], "ses_1")
        self.assertEqual(payload["input"]["title"], "物理答疑")

    def test_schedule_update_proposal(self) -> None:
        actions = self._decide("把每天学习时间改成40分钟")
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["operation"], "schedule.update")
        self.assertEqual(payload["input"]["daily_minutes"], 40)
        self.assertEqual(actions[0]["execution"], "automatic")

    def test_note_create_is_review_required_user_click(self) -> None:
        actions = self._decide("帮我记一条笔记：标题为：电磁感应要点。法拉第定律…")
        self.assertEqual(len(actions), 1)
        payload = actions[0]["payload"]
        self.assertEqual(payload["operation"], "note.create")
        # AI/文本内容写入必须预览确认（§21.2 review_required）
        self.assertEqual(actions[0]["execution"], "user_click")
        self.assertIn("电磁感应要点", payload["input"]["title"])

    def test_schedule_out_of_range_no_action(self) -> None:
        actions = self._decide("把每天学习时间改成600分钟")
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
