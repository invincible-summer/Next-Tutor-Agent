"""站内助手契约回归（A01）。

覆盖：extra=forbid、判别联合、ID 模式、PageContext 8KiB 上限、
数组上限、错误 envelope、TurnSnapshot 完整往返，以及前端生成类型
与 schema 同步（generate_assistant_types.py --check）。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pydantic
from pydantic import ValidationError

from app.schemas import assistant as sc

ROOT = Path(__file__).resolve().parents[3]


def _hex32() -> str:
    return uuid.uuid4().hex


def _ids() -> dict[str, str]:
    return {
        "conversation_id": f"astc_{_hex32()}",
        "turn_id": f"astt_{_hex32()}",
        "message_id": f"astm_{_hex32()}",
        "action_id": f"asta_{_hex32()}",
        "draft_id": f"astd_{_hex32()}",
        "command_id": f"astx_{_hex32()}",
        "source_id": f"asts_{_hex32()}",
    }


def _page_context(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "route_id": "chat",
        "route_epoch": 3,
        "workspace_id": "ws_1",
    }
    base.update(overrides)
    return base


def _turn_request(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "client_message_id": str(uuid.uuid4()),
        "expected_conversation_revision": 1,
        "text": "打开备课",
        "lang": "zh",
        "timezone": "Asia/Shanghai",
        "scope": {"mode": "follow_page"},
        "page_context": _page_context(),
    }
    base.update(overrides)
    return base


def _message(ids: dict[str, str], role: str, seq: int) -> dict:
    return {
        "message_id": ids["message_id"] if role == "user" else f"astm_{_hex32()}",
        "seq": seq,
        "role": role,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "turn_id": ids["turn_id"],
        "status": "streaming" if role == "assistant" else "complete",
        "scope": {"mode": "workspace", "workspace_ids": ["ws_1"],
                  "scope_revisions": {}},
        "blocks": [{"block_id": "b1", "type": "markdown", "text": "你好"}],
        "sources": [],
    }


def _snapshot(ids: dict[str, str]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "turn_id": ids["turn_id"],
        "conversation_id": ids["conversation_id"],
        "conversation_revision": 2,
        "client_message_id": str(uuid.uuid4()),
        "state": "running",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "last_event_seq": 4,
        "user_message": _message(ids, "user", 1),
        "assistant_message": _message(ids, "assistant", 2),
        "actions": [],
    }


class AssistantSchemaContractTest(unittest.TestCase):
    def test_settings_navigation_union_and_account_page_context(self) -> None:
        adapter = pydantic.TypeAdapter(sc.NavigationTarget)
        for section in ("general", "learning", "voice", "assistant",
                        "processing", "account", "about"):
            target = adapter.validate_python({"kind": "settings_section",
                                              "section": section})
            self.assertIsInstance(target, sc.SettingsSectionTarget)
        with self.assertRaises(ValidationError):
            adapter.validate_python({"kind": "settings_section",
                                     "section": "arbitrary_path"})
        for route in ("account", "settings"):
            self.assertEqual(sc.PageContext(**_page_context(route_id=route))
                             .route_id.value, route)

    def test_strict_model_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            sc.AssistantTurnRequest(**_turn_request(extra_field="x"))

    def test_turn_request_validates_and_trims_contract(self) -> None:
        req = sc.AssistantTurnRequest(**_turn_request())
        self.assertEqual(req.page_context.route_id, sc.AssistantRouteId.CHAT)

    def test_scope_selection_discriminated_union(self) -> None:
        req = sc.AssistantTurnRequest(**_turn_request(
            scope={"mode": "workspace", "workspace_id": "ws_9"}))
        self.assertEqual(req.scope.mode, "workspace")
        with self.assertRaises(ValidationError):
            sc.AssistantTurnRequest(**_turn_request(
                scope={"mode": "workspace"}))  # 缺 workspace_id
        with self.assertRaises(ValidationError):
            sc.AssistantTurnRequest(**_turn_request(
                scope={"mode": "galaxy"}))  # 非法判别值

    def test_id_patterns(self) -> None:
        good = _ids()
        for key, model in (
            ("conversation_id", sc.ConversationCreated),
            ("turn_id", sc.TurnSnapshot),
        ):
            self.assertIn(key, good)
        with self.assertRaises(ValidationError):
            sc.AssistantDraft(
                draft_id="not-a-valid-id",
                conversation_id=good["conversation_id"],
                action_id=good["action_id"],
                prefill={"kind": "note", "title": "t", "markdown": ""},
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            )
        with self.assertRaises(ValidationError):
            sc.AssistantTurnRequest(**_turn_request(
                client_message_id="not-uuid"))

    def test_page_context_size_limit(self) -> None:
        big = _page_context(
            selection={"text": "字" * sc.MAX_SELECTION_CHARS, "label": "选区"})
        # 仅选区本身合法，但叠加超长 view 之后超过 8KiB 总限。
        big["view"] = "v" * 7000
        with self.assertRaises(ValidationError):
            sc.PageContext(**big)

    def test_navigation_target_union_strict(self) -> None:
        target = {"kind": "lesson", "workspace_id": "ws_1", "lesson_id": "les_1"}
        payload = {"kind": "navigate", "target": target}
        action = sc.AssistantAction(
            action_id=f"asta_{_hex32()}",
            conversation_id=f"astc_{_hex32()}",
            turn_id=f"astt_{_hex32()}",
            label="打开课程",
            payload=payload,
            execution="automatic",
            state="proposed",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        self.assertEqual(action.payload.target.kind, "lesson")
        with self.assertRaises(ValidationError):
            sc.AssistantAction(
                action_id=f"asta_{_hex32()}",
                conversation_id=f"astc_{_hex32()}",
                turn_id=f"astt_{_hex32()}",
                label="x",
                payload={"kind": "navigate",
                         "target": {"kind": "lesson", "workspace_id": "ws_1"}},
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(ValidationError):
            sc.AssistantAction(
                action_id=f"asta_{_hex32()}",
                conversation_id=f"astc_{_hex32()}",
                turn_id=f"astt_{_hex32()}",
                label="x",
                payload={"kind": "delete_everything"},
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
            )

    def test_additional_navigation_targets_b01(self) -> None:
        # §20.3 完整扩展：各分支字段与闭集校验。
        ok = sc.AssessmentViewTarget(view="report",
                                     assessment_id="as_1")
        self.assertEqual(ok.view, "report")
        with self.assertRaises(ValidationError):
            # report 必须带 assessment_id（§20.3 错误组合 422）
            sc.AssessmentViewTarget(view="report")
        with self.assertRaises(ValidationError):
            # source_id 只能与 errors 组合
            sc.AssessmentViewTarget(view="start", source_id="src_1")
        # archive resource_type 是闭集，不接受任意值
        self.assertEqual(
            sc.ArchiveItemTarget(item_id="itm_1",
                                 resource_type="notes_note").resource_type,
            "notes_note")
        with self.assertRaises(ValidationError):
            sc.ArchiveItemTarget(item_id="itm_1", resource_type="anything")
        # page/revision/week_index 数值边界
        with self.assertRaises(ValidationError):
            sc.FileTarget(file_id="f_1", page=0)
        with self.assertRaises(ValidationError):
            sc.NoteRevisionTarget(note_id="n_1", revision=0)
        with self.assertRaises(ValidationError):
            sc.WeekTaskTarget(week_index=-1, week_task_id="wt_1")
        self.assertEqual(sc.WeekTaskTarget(week_index=0,
                                           week_task_id="wt_1").week_index, 0)
        # admin section 闭集
        self.assertEqual(
            sc.AdminSectionTarget(section="orphan_data").section,
            "orphan_data")
        with self.assertRaises(ValidationError):
            sc.AdminSectionTarget(section="secret_panel")
        # 通过 navigate payload 联合判别可正常路由新目标
        payload = sc.NavigatePayload(target={"kind": "dashboard_view",
                                             "range": "this_week"})
        self.assertEqual(payload.target.kind, "dashboard_view")

    def test_metric_key_closed_set(self) -> None:
        with self.assertRaises(ValidationError):
            sc.MetricItem(key="mastery_percent", label="掌握度",
                          value=83.0)  # 不允许模型自创掌握度
        ok = sc.MetricItem(key="answer_attempt_count", label="已受理作答",
                           value=3, completeness="complete")
        self.assertEqual(ok.completeness, "complete")

    def test_report_array_caps(self) -> None:
        window = {
            "start_at": datetime.now(timezone.utc).isoformat(),
            "end_at": datetime.now(timezone.utc).isoformat(),
            "timezone": "Asia/Shanghai",
            "label": "近 7 天",
        }
        report = {
            "window": window,
            "scope": {"mode": "account", "workspace_ids": [],
                      "scope_revisions": {}},
            "facts": [
                {"key": "recorded_learning_days", "label": f"d{i}", "value": i}
                for i in range(7)
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.assertRaises(ValidationError):
            sc.LearningReport(**report)  # 事实 > 6
        report["facts"] = report["facts"][:6]
        sc.LearningReport(**report)

    def test_error_envelope_shape(self) -> None:
        envelope = sc.ErrorResponse(error={
            "code": "scope_changed",
            "message": "教材范围已变化，请刷新后继续。",
            "retryable": True,
            "request_id": "req_example",
        })
        self.assertEqual(envelope.error.code, sc.AssistantErrorCode.SCOPE_CHANGED)

    def test_turn_snapshot_roundtrip(self) -> None:
        snap = sc.TurnSnapshot(**_snapshot(_ids()))
        data = snap.model_dump(mode="json")
        again = sc.TurnSnapshot(**data)
        self.assertEqual(again, snap)

    def test_sse_event_union(self) -> None:
        adapter = pydantic.TypeAdapter(sc.AssistantSseEvent)
        ids = _ids()
        event = adapter.validate_python({
            "schema_version": 1,
            "turn_id": ids["turn_id"],
            "event_seq": 2,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "event": "text_delta",
            "message_id": ids["message_id"],
            "block_id": "b1",
            "delta": "你好",
        })
        self.assertEqual(event.event, "text_delta")
        with self.assertRaises(ValidationError):
            adapter.validate_python({
                "schema_version": 1,
                "turn_id": ids["turn_id"],
                "event_seq": 3,
                "emitted_at": datetime.now(timezone.utc).isoformat(),
                "event": "heartbeat",  # 心跳是 SSE 注释，不是业务事件
            })

    def test_action_execute_request_minimal(self) -> None:
        sc.ActionExecuteRequest(
            invocation_id=str(uuid.uuid4()),
            client_instance_id="tab_abcdef123",
            route_epoch=2,
        )
        with self.assertRaises(ValidationError):
            sc.ActionExecuteRequest(
                invocation_id="nope",
                client_instance_id="tab_abcdef123",
                route_epoch=2,
            )

    def test_capabilities_and_guide(self) -> None:
        caps = sc.AssistantCapabilities(
            enabled=True,
            catalog_version="1.0.0",
            identity_mode="guest",
            conversation_enabled=False,
            model_available=True,
            modules=[{"route_id": "chat", "available": True}],
        )
        self.assertFalse(caps.conversation_enabled)
        guide = sc.GuideRequest(
            schema_version=1,
            question="这个网站怎么用？",
            history=[{"role": "user", "text": "你好"}] * 6,
        )
        self.assertEqual(len(guide.history), 6)
        with self.assertRaises(ValidationError):
            sc.GuideRequest(
                schema_version=1,
                question="x",
                history=[{"role": "user", "text": "y"}] * 7,
            )

    def test_generated_frontend_types_in_sync(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dev" / "generate_assistant_types.py"),
             "--check"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0,
                         f"生成类型与 schema 不一致:\n{result.stdout}{result.stderr}")


if __name__ == "__main__":
    unittest.main()
