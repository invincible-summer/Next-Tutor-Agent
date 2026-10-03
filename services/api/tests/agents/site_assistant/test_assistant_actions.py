"""受控动作协议回归（A10）。

覆盖：§9.2 策略表（automatic/user_click/无动作）、execute 受理与
invocation 锁（同 invocation 幂等、异 invocation 409）、ack 凭证与
幂等、过期 410、needs_attention 读时推进、open_workspace_form 归属
校验、快照动作对外形状（剥离内部字段并通过 §9.1 schema）。
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

from app.agents.site_assistant import catalog, intent, policy
from app.agents.site_assistant import store


def _navigate_results(unique: bool = True) -> list[dict]:
    candidates = ([{"kind": "module", "route_id": "course",
                    "title": "备课上课",
                    "target": {"kind": "module", "route_id": "course"}}]
                  if unique else
                  [{"kind": "module", "route_id": "course", "title": "A",
                    "target": {"kind": "module", "route_id": "course"}},
                   {"kind": "workspace", "workspace_id": "wsp_x",
                    "title": "B",
                    "target": {"kind": "workspace_chat",
                               "workspace_id": "wsp_x"}}])
    return [{"tool": "resolve_destination", "status": "ready",
             "data": {"candidates": candidates, "unique": unique},
             "notices": [], "sources": []}]


class PolicyTest(unittest.TestCase):
    def _decide(self, kind: str, results=None, parsed=None):
        parsed = parsed or intent.ParsedIntent(kind=kind, text="去备课")
        return policy.decide_actions(
            parsed, results or [], {},
            conversation_id="astc_t", turn_id="astt_t", lang="zh",
            page_epoch=3)

    def test_navigate_unique_module_automatic(self) -> None:
        parsed = intent.ParsedIntent(
            kind="navigate", text="带我去备课上课",
            module_route=catalog.AssistantRouteId.COURSE)
        actions = self._decide("navigate", _navigate_results(), parsed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["execution"], "automatic")
        self.assertEqual(actions[0]["payload"]["kind"], "navigate")
        self.assertEqual(actions[0]["payload"]["target"]["route_id"],
                         "course")
        self.assertEqual(actions[0]["state"], "proposed")
        self.assertEqual(actions[0]["route_epoch"], 3)
        self.assertIn("备课", actions[0]["label"])

    def test_search_unique_user_click(self) -> None:
        parsed = intent.ParsedIntent(kind="search", text="我的课程在哪")
        actions = self._decide("search", _navigate_results(), parsed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["execution"], "user_click")

    def test_ambiguous_candidates_no_action(self) -> None:
        parsed = intent.ParsedIntent(kind="navigate", text="去看看")
        actions = self._decide("navigate", _navigate_results(unique=False),
                               parsed)
        self.assertEqual(actions, [])

    def test_guide_and_report_no_actions(self) -> None:
        for kind in ("guide", "learning_report", "teaching_report",
                     "general_chat", "clarify"):
            self.assertEqual(self._decide(kind), [])

    # -- P1-2 回归：resolve_destination 的唯一实体候选优先于意图阶段
    #    的模块命中（「打开我的笔记《X》」出实体深链卡，不是模块卡）。

    def test_unique_entity_destination_beats_primary_module(self) -> None:
        parsed = intent.ParsedIntent(
            kind="navigate", text="打开我的笔记《定积分与可积性》",
            module_route=catalog.AssistantRouteId.NOTES)
        results = [{"tool": "resolve_destination", "status": "ready",
                    "data": {"candidates": [
                        {"kind": "note", "entity_id": "note_p12",
                         "title": "定积分与可积性",
                         "target": {"kind": "note",
                                    "note_id": "note_p12"}}],
                        "unique": True},
                    "notices": [], "sources": []}]
        actions = self._decide("navigate", results, parsed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["execution"], "automatic")
        self.assertEqual(actions[0]["payload"]["target"],
                         {"kind": "note", "note_id": "note_p12"})
        self.assertIn("笔记", actions[0]["label"])
        self.assertIn("定积分与可积性", actions[0]["label"])

    def test_destination_absent_falls_back_to_module(self) -> None:
        parsed = intent.ParsedIntent(
            kind="navigate", text="带我去记忆中心",
            module_route=catalog.AssistantRouteId.MEMORY)
        actions = self._decide("navigate", [], parsed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["payload"]["target"],
                         {"kind": "module", "route_id": "memory"})

    def test_entity_label_without_title_uses_kind(self) -> None:
        parsed = intent.ParsedIntent(kind="search", text="我的文件在哪")
        results = [{"tool": "resolve_destination", "status": "ready",
                    "data": {"candidates": [
                        {"kind": "file", "entity_id": "libf_9",
                         "title": "",
                         "target": {"kind": "file", "file_id": "libf_9",
                                    "page": 2}}], "unique": True},
                    "notices": [], "sources": []}]
        actions = self._decide("search", results, parsed)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["execution"], "user_click")
        self.assertEqual(actions[0]["payload"]["target"]["page"], 2)
        self.assertEqual(actions[0]["label"], "打开文件")


class _ActionApiCase(StorageSandboxTestCase):
    """端到端基座：确定性路径 + 持久 TestClient。"""

    def setUp(self) -> None:
        super().setUp()
        from app.agents.site_assistant import runtime as rt_mod
        rt_mod.reset_runtime()
        from app.api.v1 import assistant as api_mod
        api_mod._turn_limiter.reset()
        api_mod._guide_limiter.reset()
        from fastapi.testclient import TestClient
        from app.main import create_app
        caps_patch = mock.patch(
            "app.agents.site_assistant.capabilities.settings")
        fake_caps = caps_patch.start()
        fake_caps.site_assistant_enabled = True
        fake_caps.classroom_enabled = False
        fake_caps.llm_api_key = ""
        self._caps_patch = caps_patch
        self.app = create_app()
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="act@example.com", username="",
            password_hash=hash_password("secret123"))
        self.auth = {"Authorization": f"Bearer {create_token(self.user.id)}"}
        self.sid = self.user.id
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        from app.agents.site_assistant import runtime as rt_mod
        rt_mod.reset_runtime()
        self._client_cm.__exit__(None, None, None)
        self._caps_patch.stop()
        super().tearDown()

    def _run_navigate_turn(self, text: str = "带我去备课上课。") -> dict:
        resp = self.client.post(
            "/api/v1/assistant/conversations",
            json={"client_request_id": str(uuid.uuid4())},
            headers=self.auth)
        self.assertEqual(resp.status_code, 201, resp.text)
        cid, revision = (resp.json()["conversation_id"],
                         resp.json()["revision"])
        resp = self.client.post(
            f"/api/v1/assistant/conversations/{cid}/turns",
            json={"schema_version": 1,
                  "client_message_id": str(uuid.uuid4()),
                  "expected_conversation_revision": revision,
                  "text": text, "lang": "zh",
                  "timezone": "Asia/Shanghai",
                  "scope": {"mode": "follow_page"},
                  "page_context": {"schema_version": 1,
                                   "route_id": "dashboard",
                                   "route_epoch": 7}},
            headers=self.auth)
        self.assertEqual(resp.status_code, 202, resp.text)
        turn_id = resp.json()["turn_id"]
        end = time.monotonic() + 5.0
        snap = None
        while time.monotonic() < end:
            got = self.client.get(f"/api/v1/assistant/turns/{turn_id}",
                                  headers=self.auth)
            self.assertEqual(got.status_code, 200, got.text)
            snap = got.json()
            if snap["state"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.05)
        self.assertEqual(snap["state"], "completed")
        return snap

    def _execute(self, action_id: str, *, invocation: str | None = None,
                 instance: str = "client-instance-0001",
                 epoch: int = 7):
        return self.client.post(
            f"/api/v1/assistant/actions/{action_id}/execute",
            json={"invocation_id": invocation or str(uuid.uuid4()),
                  "client_instance_id": instance,
                  "route_epoch": epoch},
            headers=self.auth)

    def _ack(self, action_id: str, command_id: str, ack_token: str,
             result: str = "succeeded", error_code: str | None = None):
        return self.client.post(
            f"/api/v1/assistant/actions/{action_id}/ack",
            json={"command_id": command_id, "ack_token": ack_token,
                  "result": result, "error_code": error_code},
            headers=self.auth)


class ActionProtocolApiTest(_ActionApiCase):
    def test_navigate_turn_proposes_automatic_action(self) -> None:
        snap = self._run_navigate_turn()
        self.assertTrue(snap["actions"])
        action = snap["actions"][0]
        self.assertEqual(action["execution"], "automatic")
        self.assertEqual(action["state"], "proposed")
        self.assertEqual(action["payload"]["kind"], "navigate")
        # 对外形状：不泄漏内部运行字段（§9.1 schema 闭集）
        import pydantic
        from app.schemas import assistant as sc
        pydantic.TypeAdapter(sc.AssistantAction).validate_python(action)
        # 消息内 actions 块引用同一 action
        kinds = [b["type"] for b in snap["assistant_message"]["blocks"]]
        self.assertIn("actions", kinds)

    def test_execute_ack_full_cycle(self) -> None:
        snap = self._run_navigate_turn()
        action_id = snap["actions"][0]["action_id"]
        invocation = str(uuid.uuid4())

        resp = self._execute(action_id, invocation=invocation)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["action"]["state"], "awaiting_ack")
        self.assertEqual(body["command"]["kind"], "navigate")
        self.assertEqual(body["command"]["target"]["route_id"], "course")
        self.assertTrue(body["ack_token"])
        self.assertEqual(body["business_result"]["kind"], "none")

        # 同 invocation 幂等：同一 command，不再运行准备
        again = self._execute(action_id, invocation=invocation)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["command_id"],
                         body["command_id"])

        # 不同执行者 409/action_in_progress，且拿不到 token
        other = self._execute(action_id, instance="client-instance-0002")
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.json()["error"]["code"], "action_in_progress")

        # GET：非执行者无 command/token；有 ready_to_deliver 语义
        got = self.client.get(
            f"/api/v1/assistant/actions/{action_id}"
            "?client_instance_id=client-instance-0001", headers=self.auth)
        self.assertEqual(got.status_code, 200)
        self.assertTrue(got.json()["ready_to_deliver"])
        self.assertEqual(got.json()["command_id"], body["command_id"])
        got2 = self.client.get(
            f"/api/v1/assistant/actions/{action_id}"
            "?client_instance_id=client-instance-0002", headers=self.auth)
        self.assertFalse(got2.json()["ready_to_deliver"])
        self.assertIsNone(got2.json()["command"])

        # ack：错误 token 409；正确 token → succeeded
        bad = self._ack(action_id, body["command_id"], "x" * 32)
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(bad.json()["error"]["code"], "ack_invalid")
        ok = self._ack(action_id, body["command_id"], body["ack_token"])
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["action"]["state"], "succeeded")
        # 重复相同 ack 幂等
        repeat = self._ack(action_id, body["command_id"], body["ack_token"])
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.json()["action"]["state"], "succeeded")
        # 已终结 execute 返回原结果，不再发新命令
        done = self._execute(action_id, invocation=str(uuid.uuid4()))
        self.assertEqual(done.status_code, 200)
        self.assertIsNone(done.json()["command"])

    def test_ack_failed_sets_retryable(self) -> None:
        snap = self._run_navigate_turn("我想搜一下我的课程")
        action = next((a for a in snap["actions"]
                       if a["execution"] == "user_click"), None)
        self.assertIsNotNone(action)
        resp = self._execute(action["action_id"])
        body = resp.json()
        resp = self._ack(action["action_id"], body["command_id"],
                         body["ack_token"], result="failed",
                         error_code="page_not_ready")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["action"]["state"], "failed")
        self.assertTrue(result["retryable"])

    def test_expired_action_returns_410(self) -> None:
        snap = self._run_navigate_turn()
        action_id = snap["actions"][0]["action_id"]
        # 直接改存储把 expires_at 置于过去
        located = None
        from app.agents.site_assistant import actions as actions_svc
        located = actions_svc.locate_action(self.sid, action_id)
        record, _cid, action = located
        action["expires_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(seconds=1)).isoformat()
        store.save_conversation(self.sid, record)
        resp = self._execute(action_id)
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error"]["code"], "action_expired")

    def test_needs_attention_after_ack_window(self) -> None:
        snap = self._run_navigate_turn()
        action_id = snap["actions"][0]["action_id"]
        resp = self._execute(action_id)
        body = resp.json()
        located = None
        from app.agents.site_assistant import actions as actions_svc
        located = actions_svc.locate_action(self.sid, action_id)
        record, _cid, action = located
        action["invocation"]["delivered_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(seconds=20)).isoformat()
        store.save_conversation(self.sid, record)
        got = self.client.get(f"/api/v1/assistant/actions/{action_id}",
                              headers=self.auth)
        self.assertEqual(got.json()["action"]["state"], "needs_attention")
        # needs_attention 仍可用原 token ack（§19.3）
        ok = self._ack(action_id, body["command_id"], body["ack_token"])
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["action"]["state"], "succeeded")

    def test_workspace_form_ownership_check(self) -> None:
        from app.core import workspace as ws_core
        path = ws_core._WORKSPACES_DIR / "wsp_act.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "workspace_id": "wsp_act", "student_id": self.sid,
            "name": "我的区", "session_ids": [], "knowledge_files": [],
            "updated_at": 1}), encoding="utf-8")
        from app.agents.site_assistant import actions as actions_svc
        record = {"conversation_id": "astc_c1", "revision": 1,
                  "messages": [], "turns": {}, "actions": {},
                  "accepted": {}}
        action = {"action_id": "asta_form1", "conversation_id": "astc_c1",
                  "turn_id": "astt_x", "label": "打开工作区设置",
                  "payload": {"kind": "open_workspace_form",
                              "workspace_id": "wsp_act"},
                  "execution": "user_click", "state": "proposed",
                  "created_at": store.utc_now_iso(),
                  "expires_at": (datetime.now(tz=timezone.utc)
                                 + timedelta(minutes=10)).isoformat(),
                  "business_result": {"kind": "none"}}
        record["actions"][action["action_id"]] = action
        store.save_conversation(self.sid, record)
        resp = self._execute("asta_form1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["command"]["kind"], "workspace_form")
        self.assertEqual(resp.json()["command"]["workspace_id"], "wsp_act")

        # 他人工作区：准备失败（capability_disabled 口径）
        record2 = dict(record)
        record2["actions"] = dict(record["actions"])
        foreign = dict(action, action_id="asta_form2")
        foreign["payload"] = {"kind": "open_workspace_form",
                              "workspace_id": "wsp_foreign"}
        record2["actions"]["asta_form2"] = foreign
        store.save_conversation(self.sid, record2)
        resp2 = self._execute("asta_form2")
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["action"]["state"], "failed")
        self.assertIsNone(body2["command"])

    def test_ack_before_execute_rejected(self) -> None:
        snap = self._run_navigate_turn()
        action_id = snap["actions"][0]["action_id"]
        resp = self._ack(action_id, "astx_" + "0" * 32, "y" * 32)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"],
                         "action_state_invalid")


if __name__ == "__main__":
    unittest.main()
