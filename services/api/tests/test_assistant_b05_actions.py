"""B05 聊天/资料/归档领域动作回归（10 操作）。

每操作覆盖：执行成功、预览正确、幂等重试不重复创建、归属校验 404；
另覆盖 §21.6.1 client_request_id 创建幂等、update_sources 最新集合合并不
覆盖并发新增、update_sources 移除升级 review_required、ACTIONS 开关。
"""
from __future__ import annotations

import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _B05Case(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b05_" + _hex()
        self.cid = "astc_b05"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    # -- 夹具 --------------------------------------------------------------

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_b05", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id=None, **kw):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-b05", route_epoch=1,
            approval_id=approval_id, **kw)

    def _make_session(self, title: str = "物理答疑",
                      session_id: str = "") -> str:
        from app.core.session import TutorSession, save_session
        s = TutorSession(session_id=session_id, title=title,
                         student_id=self.sid)
        save_session(s)
        return s.session_id

    def _make_workspace(self, name: str = "物理区") -> str:
        from app.core.workspace import Workspace, save_workspace
        ws = Workspace(name=name, student_id=self.sid)
        return save_workspace(ws)

    def _make_textbook(self, title: str = "物理必修一") -> tuple[str, str]:
        """注册一本教材（library 文件 + textbook 记录）；返回 (tb_id, file_id)。"""
        from app.core.library import load_library, save_library
        from app.core import textbook as tb_store
        lib = load_library(self.sid)
        meta = lib.add_file("", f"{title}.pdf", "教材正文文本")
        save_library(lib)
        tb_id = "tb_" + _hex()
        rows = tb_store._load_raw(self.sid)
        rows.append({
            "id": tb_id, "title": title, "file_id": meta["id"],
            "status": "ready", "kind": "single", "created_at": 0,
            "updated_at": 0})
        tb_store._save(self.sid, rows)
        return tb_id, str(meta["id"])


class WorkspaceCreateTest(_B05Case):
    def test_execute_preview_and_idempotent_reuse(self) -> None:
        from app.core.workspace import load_workspace, list_workspaces
        from app.agents.site_assistant import previews
        self._add_action("asta_ws", {
            "kind": "domain_write", "operation": "workspace.create",
            "input": {"name": "物理专区"}})
        preview = previews.build_preview(self.sid, "asta_ws")
        self.assertEqual(preview["approval"], "intent_sufficient")
        self.assertTrue(preview["reversible"])
        self.assertEqual(preview["changes"][0]["after"], "物理专区")
        result = self._execute("asta_ws")
        self.assertEqual(result["action"]["state"], "succeeded")
        wid = result["business_result"]["entity_id"]
        ws = load_workspace(wid)
        self.assertEqual(ws.name, "物理专区")
        self.assertEqual(ws.student_id, self.sid)
        # §21.6.1 幂等：模拟 failed→retry 后的领域重执行 → 复用同一实体。
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"]["asta_ws"]["business_result"] = {"kind": "none"}
        store.save_conversation(self.sid, rec)
        again = previews.execute_domain_write(
            self.sid, store.load_conversation(self.sid, self.cid)
            ["actions"]["asta_ws"])
        self.assertTrue(again.get("idempotent_reuse"))
        self.assertEqual(again["entity_id"], wid)
        self.assertEqual(
            len([w for w in list_workspaces() if w["name"] == "物理专区"
                 and w.get("student_id") == self.sid]), 1)

    def test_invalid_file_id_rejected(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_wsb", {
            "kind": "domain_write", "operation": "workspace.create",
            "input": {"name": "坏来源", "file_ids": ["file_missing_1"]}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_wsb")
        self.assertEqual(ctx.exception.code, "entity_not_found")
        with self.assertRaises(ActionRejected):
            self._execute("asta_wsb")

    def test_with_textbook_selection(self) -> None:
        from app.core.workspace import load_workspace
        _tb, fid = self._make_textbook()
        self._add_action("asta_wsc", {
            "kind": "domain_write", "operation": "workspace.create",
            "input": {"name": "有教材", "file_ids": [fid]}})
        result = self._execute("asta_wsc")
        ws = load_workspace(result["business_result"]["entity_id"])
        self.assertEqual(ws.selected_file_ids, [fid])


class WorkspaceUpdateSourcesTest(_B05Case):
    def test_merge_preserves_concurrent_additions(self) -> None:
        from app.core.workspace import load_workspace, save_workspace
        from app.agents.site_assistant import previews
        wid = self._make_workspace("合并区")
        _tb1, fid_a = self._make_textbook("教材A")
        _tb2, fid_b = self._make_textbook("教材B")
        _tb3, fid_c = self._make_textbook("教材C")
        # 动作提案时工作区只有 A。
        ws = load_workspace(wid)
        ws.selected_file_ids = [fid_a]
        save_workspace(ws)
        self._add_action("asta_us", {
            "kind": "domain_write", "operation": "workspace.update_sources",
            "input": {"workspace_id": wid, "add_file_ids": [fid_b]}})
        # 执行前模拟并发新增 C（例如用户在原页面同时保存）。
        ws = load_workspace(wid)
        ws.selected_file_ids = [fid_a, fid_c]
        save_workspace(ws)
        preview = previews.build_preview(self.sid, "asta_us")
        self.assertEqual(preview["approval"], "intent_sufficient")
        result = self._execute("asta_us")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(load_workspace(wid).selected_file_ids,
                         [fid_a, fid_c, fid_b])

    def test_remove_upgrades_to_review_required(self) -> None:
        from app.core.workspace import load_workspace, save_workspace
        from app.agents.site_assistant import previews
        wid = self._make_workspace("移除区")
        _tb, fid = self._make_textbook("教材D")
        ws = load_workspace(wid)
        ws.selected_file_ids = [fid]
        save_workspace(ws)
        self._add_action("asta_usr", {
            "kind": "domain_write", "operation": "workspace.update_sources",
            "input": {"workspace_id": wid, "remove_file_ids": [fid]}})
        preview = previews.build_preview(self.sid, "asta_usr")
        self.assertEqual(preview["approval"], "review_required")
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_usr")
        self.assertEqual(ctx.exception.code, "preview_stale")
        approval = previews.approve(
            self.sid, "asta_usr", preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        result = self._execute("asta_usr", approval["approval_id"])
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(load_workspace(wid).selected_file_ids, [])

    def test_foreign_workspace_404(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        other = "usr_b05_other_" + _hex()
        from app.core.workspace import Workspace, save_workspace
        wid = save_workspace(Workspace(name="他人区", student_id=other))
        self._add_action("asta_usf", {
            "kind": "domain_write", "operation": "workspace.update_sources",
            "input": {"workspace_id": wid, "add_file_ids": []}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_usf")
        self.assertEqual(ctx.exception.code, "entity_not_found")
        with self.assertRaises(ActionRejected):
            self._execute("asta_usf")


class ChatActionsTest(_B05Case):
    def test_move_workspace(self) -> None:
        from app.core.session import load_session
        from app.core.workspace import load_workspace
        from app.agents.site_assistant import previews
        sid = self._make_session("浮力讨论")
        wid = self._make_workspace("物理区")
        self._add_action("asta_mv", {
            "kind": "domain_write", "operation": "chat.move_workspace",
            "input": {"session_id": sid, "workspace_id": wid}})
        preview = previews.build_preview(self.sid, "asta_mv")
        self.assertEqual(preview["changes"][0]["before"], "未分组")
        self.assertEqual(preview["changes"][0]["after"], "物理区")
        self.assertEqual(preview["source_revisions"],
                         {"session_workspace": "loose"})
        result = self._execute("asta_mv", main_loop=None)
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(load_session(sid).workspace_id, wid)
        self.assertIn(sid, load_workspace(wid).session_ids)

    def test_move_to_foreign_workspace_404(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        other = "usr_b05_other_" + _hex()
        from app.core.workspace import Workspace, save_workspace
        wid = save_workspace(Workspace(name="他人区", student_id=other))
        sid = self._make_session()
        self._add_action("asta_mvf", {
            "kind": "domain_write", "operation": "chat.move_workspace",
            "input": {"session_id": sid, "workspace_id": wid}})
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_mvf")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def test_archive_and_restore_roundtrip(self) -> None:
        from app.core.session import load_session
        from app.core.trash import get_item, list_items
        from app.agents.site_assistant import previews
        sid = self._make_session("要归档的对话")
        self._add_action("asta_ar", {
            "kind": "domain_write", "operation": "chat.archive",
            "input": {"session_id": sid}})
        preview = previews.build_preview(self.sid, "asta_ar")
        self.assertIn("可恢复", preview["changes"][0]["after"])
        self.assertTrue(preview["reversible"])
        result = self._execute("asta_ar")
        item_id = result["business_result"]["entity_id"]
        self.assertEqual(result["business_result"]["kind"], "trash_item")
        self.assertIsNone(load_session(sid))          # 会话文件已移入归档
        self.assertIsNotNone(get_item(self.sid, item_id))
        # archive.restore：恢复回原位。
        self._add_action("asta_rs", {
            "kind": "domain_write", "operation": "archive.restore",
            "input": {"item_id": item_id}})
        rp = previews.build_preview(self.sid, "asta_rs")
        self.assertIn("（对话）", rp["summary"])
        restored = self._execute("asta_rs")
        self.assertEqual(restored["action"]["state"], "succeeded")
        self.assertIsNotNone(load_session(sid))
        self.assertIsNone(get_item(self.sid, item_id))  # 归档条目被消费
        # 幂等：对已消费条目再次恢复 → 404，不重复恢复。
        self._add_action("asta_rs2", {
            "kind": "domain_write", "operation": "archive.restore",
            "input": {"item_id": item_id}})
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_rs2")
        self.assertEqual(ctx.exception.code, "entity_not_found")
        self.assertEqual(len(list_items(self.sid, "session")), 0)

    def test_archive_foreign_session_404(self) -> None:
        from app.agents.site_assistant.actions import ActionRejected
        other = "usr_b05_other_" + _hex()
        from app.core.session import TutorSession, save_session
        sid = save_session(TutorSession(
            session_id="sess_foreign_" + _hex(), title="他人的",
            student_id=other))
        self._add_action("asta_arf", {
            "kind": "domain_write", "operation": "chat.archive",
            "input": {"session_id": sid}})
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_arf")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class LibraryActionsTest(_B05Case):
    def test_create_folder_idempotent(self) -> None:
        from app.core.library import load_library
        from app.agents.site_assistant import previews
        self._add_action("asta_lf", {
            "kind": "domain_write", "operation": "library.create_folder",
            "input": {"name": "实验资料"}})
        preview = previews.build_preview(self.sid, "asta_lf")
        self.assertEqual(preview["approval"], "intent_sufficient")
        result = self._execute("asta_lf")
        fid = result["business_result"]["entity_id"]
        self.assertEqual(result["business_result"]["kind"], "library_folder")
        # §21.6.1：清空 business_result 模拟重试 → 同一文件夹，不重复创建。
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"]["asta_lf"]["business_result"] = {"kind": "none"}
        store.save_conversation(self.sid, rec)
        again = previews.execute_domain_write(
            self.sid, store.load_conversation(self.sid, self.cid)
            ["actions"]["asta_lf"])
        self.assertTrue(again.get("idempotent_reuse"))
        self.assertEqual(again["entity_id"], fid)
        lib = load_library(self.sid)
        self.assertEqual(
            len([f for f in lib.folders if f.get("name") == "实验资料"]), 1)

    def test_rename_file(self) -> None:
        from app.core.library import load_library, save_library
        from app.agents.site_assistant import previews
        lib = load_library(self.sid)
        meta = lib.add_file("", "旧名.txt", "内容")
        save_library(lib)
        self._add_action("asta_rn", {
            "kind": "domain_write", "operation": "library.rename_file",
            "input": {"file_id": meta["id"], "filename": "新名.txt"}})
        preview = previews.build_preview(self.sid, "asta_rn")
        self.assertEqual(preview["changes"][0]["before"], "旧名.txt")
        self.assertEqual(preview["changes"][0]["after"], "新名.txt")
        result = self._execute("asta_rn")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["filename"],
            "新名.txt")

    def test_rename_missing_file_404(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_rnm", {
            "kind": "domain_write", "operation": "library.rename_file",
            "input": {"file_id": "file_missing_9", "filename": "x.txt"}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_rnm")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def test_move_file(self) -> None:
        from app.core.library import load_library, save_library
        from app.agents.site_assistant import previews
        lib = load_library(self.sid)
        meta = lib.add_file("", "游离.txt", "内容")
        folder = lib.create_folder("归档夹")
        save_library(lib)
        self._add_action("asta_mf", {
            "kind": "domain_write", "operation": "library.move_file",
            "input": {"file_id": meta["id"], "folder_id": folder["id"]}})
        preview = previews.build_preview(self.sid, "asta_mf")
        self.assertEqual(preview["changes"][0]["before"], "根目录")
        self.assertEqual(preview["changes"][0]["after"], "归档夹")
        result = self._execute("asta_mf")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(
            load_library(self.sid).find_file(meta["id"])["folder_id"],
            folder["id"])

    def test_move_to_missing_folder_404(self) -> None:
        from app.core.library import load_library, save_library
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        lib = load_library(self.sid)
        meta = lib.add_file("", "x.txt", "内容")
        save_library(lib)
        self._add_action("asta_mfm", {
            "kind": "domain_write", "operation": "library.move_file",
            "input": {"file_id": meta["id"], "folder_id": "f_missing_1"}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_mfm")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class TextbookActionsTest(_B05Case):
    def test_cancel_own_textbook(self) -> None:
        from app.core import textbook as tb_store
        from app.agents.site_assistant import previews
        tb_id, _fid = self._make_textbook("要取消的教材")
        self._add_action("asta_tc", {
            "kind": "domain_write", "operation": "textbook.cancel",
            "input": {"textbook_id": tb_id}})
        preview = previews.build_preview(self.sid, "asta_tc")
        self.assertEqual(preview["approval"], "intent_sufficient")
        self.assertFalse(preview["reversible"])
        result = self._execute("asta_tc")
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertIn(tb_store.find_textbook(self.sid, tb_id)["status"],
                      ("ready", "failed"))  # 合作式结算后的真实终态

    def test_cancel_public_requires_admin(self) -> None:
        from app.core import textbook as tb_store
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        # 在 public 命名空间注册一本公用教材。
        from app.core.library import load_library as _ll, save_library as _sl
        lib = _ll(tb_store.PUBLIC_STUDENT_ID)
        meta = lib.add_file("", "公用教材.pdf", "正文")
        _sl(lib)
        tb_id = "tb_pub_" + _hex()
        tb_store._save(tb_store.PUBLIC_STUDENT_ID, [{
            "id": tb_id, "title": "公用教材", "file_id": meta["id"],
            "status": "ready", "kind": "single", "created_at": 0,
            "updated_at": 0}])
        self._add_action("asta_tcp", {
            "kind": "domain_write", "operation": "textbook.cancel",
            "input": {"textbook_id": tb_id}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_tcp")
        self.assertEqual(ctx.exception.status_code, 403)
        # 管理员可执行。
        preview = previews.build_preview(self.sid, "asta_tcp", is_admin=True)
        self.assertEqual(preview["approval"], "intent_sufficient")
        result = self._execute("asta_tcp", is_admin=True)
        self.assertEqual(result["action"]["state"], "succeeded")

    def test_rebuild_review_required_and_spawn(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        import app.api.v1.textbook as tb_api
        tb_id, _fid = self._make_textbook("要刷新的教材")
        self._add_action("asta_rb", {
            "kind": "domain_write", "operation": "textbook.rebuild",
            "input": {"textbook_id": tb_id, "mode": "graph_only"}})
        preview = previews.build_preview(self.sid, "asta_rb")
        self.assertEqual(preview["approval"], "review_required")
        self.assertFalse(preview["reversible"])
        self.assertIn("只重建图谱", preview["summary"])
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_rb")
        self.assertEqual(ctx.exception.code, "preview_stale")
        approval = previews.approve(
            self.sid, "asta_rb", preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        # 刷新任务不在测试里真实跑：替换 _spawn_refresh 观察桥接调用。
        with mock.patch.object(tb_api, "_spawn_refresh",
                               return_value=True) as spawn:
            result = self._execute("asta_rb", approval["approval_id"])
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(result["business_result"]["kind"], "textbook_job")
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[2], "graph_only")
        # 状态已被 _start_rebuild 置为 building（真实终态由后台任务推进）。
        from app.core import textbook as tb_store
        self.assertEqual(
            tb_store.find_textbook(self.sid, tb_id)["status"], "building")

    def test_rebuild_with_real_event_loop(self) -> None:
        """main_loop 桥接：工作线程把 _start_rebuild 提交到事件循环执行。"""
        import asyncio
        from app.agents.site_assistant import previews
        import app.api.v1.textbook as tb_api
        tb_id, _fid = self._make_textbook("循环桥接教材")
        self._add_action("asta_rbl", {
            "kind": "domain_write", "operation": "textbook.rebuild",
            "input": {"textbook_id": tb_id}})
        preview = previews.build_preview(self.sid, "asta_rbl")
        approval = previews.approve(
            self.sid, "asta_rbl", preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")

        loop = asyncio.new_event_loop()
        started = threading.Event()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            started.set()
            loop.run_forever()

        thread = threading.Thread(target=_run_loop, daemon=True)
        thread.start()
        started.wait(2)
        try:
            with mock.patch.object(tb_api, "_spawn_refresh",
                                   return_value=True):
                result = self._execute("asta_rbl", approval["approval_id"],
                                       main_loop=loop)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=3)
            loop.close()
        self.assertEqual(result["action"]["state"], "succeeded")

    def test_rebuild_public_requires_admin(self) -> None:
        from app.core import textbook as tb_store
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        from app.core.library import load_library as _ll, save_library as _sl
        lib = _ll(tb_store.PUBLIC_STUDENT_ID)
        meta = lib.add_file("", "公用教材2.pdf", "正文")
        _sl(lib)
        tb_id = "tb_pub2_" + _hex()
        tb_store._save(tb_store.PUBLIC_STUDENT_ID, [{
            "id": tb_id, "title": "公用教材2", "file_id": meta["id"],
            "status": "ready", "kind": "single", "created_at": 0,
            "updated_at": 0}])
        self._add_action("asta_rbp", {
            "kind": "domain_write", "operation": "textbook.rebuild",
            "input": {"textbook_id": tb_id}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_rbp")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_textbook_404(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_tbm", {
            "kind": "domain_write", "operation": "textbook.cancel",
            "input": {"textbook_id": "tb_missing_1"}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_tbm")
        self.assertEqual(ctx.exception.code, "entity_not_found")


class ActionsSwitchTest(_B05Case):
    def test_switch_off_blocks_proposal_preview_execute(self) -> None:
        import asyncio
        from app.core.config import settings
        from app.agents.site_assistant import policy, previews
        from app.agents.site_assistant.actions import ActionRejected
        from app.agents.site_assistant.intent import parse_intent
        self._add_action("asta_sw", {
            "kind": "domain_write", "operation": "task.create",
            "input": {"title": "开关测试", "day": ""}})
        with mock.patch.object(settings, "site_assistant_actions_enabled",
                               False):
            parsed = asyncio.run(
                parse_intent("帮我创建一个任务：开关测试", lang="zh"))
            self.assertEqual(parsed.kind, "prepare_action")
            actions = policy.decide_actions(
                parsed, [], {}, conversation_id=self.cid, turn_id="t1",
                lang="zh")
            self.assertEqual(actions, [])  # 开关关闭：不提出领域写
            with self.assertRaises(ActionRejected) as ctx:
                previews.build_preview(self.sid, "asta_sw")
            self.assertEqual(ctx.exception.code, "capability_disabled")
            with self.assertRaises(ActionRejected):
                self._execute("asta_sw")

    def test_capabilities_reflect_switch(self) -> None:
        from app.core.config import settings
        from app.agents.site_assistant import capabilities as caps
        from app.identity.models import User
        user = User(id=self.sid, email="b05@example.com",
                    password_hash="x", role="student")
        with mock.patch.object(settings, "site_assistant_enabled", True), \
                mock.patch.object(settings, "site_assistant_actions_enabled",
                                  False):
            out = caps.build_assistant_capabilities(user)
            self.assertIn("navigate", out.action_kinds)
            self.assertNotIn("domain_write", out.action_kinds)
        with mock.patch.object(settings, "site_assistant_enabled", True), \
                mock.patch.object(settings, "site_assistant_actions_enabled",
                                  True):
            out = caps.build_assistant_capabilities(user)
            self.assertIn("domain_write", out.action_kinds)


class B05ProposalTest(_B05Case):
    """policy 确定性抽取（B05 句式）→ operation+input。"""

    @staticmethod
    def _page_chat(session_id: str) -> dict:
        return {"entity": {"kind": "chat", "id": session_id}}

    def _propose(self, text: str, results=None, page_context=None):
        import asyncio
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent
        parsed = asyncio.run(parse_intent(text, lang="zh"))
        self.assertEqual(parsed.kind, "prepare_action", text)
        return policy._propose_domain_write(
            parsed, results or [], zh=True, page_context=page_context)

    def test_workspace_create_proposal(self) -> None:
        out = self._propose("帮我创建一个学习区叫物理专区")
        self.assertEqual(out["operation"], "workspace.create")
        self.assertEqual(out["input"], {"name": "物理专区"})
        self.assertEqual(out["execution"], "automatic")
        # 参数不完整 → 不给动作（回落回答/表单）。
        self.assertIsNone(self._propose("创建一个工作区"))

    def test_folder_create_proposal(self) -> None:
        out = self._propose("创建文件夹：实验资料")
        self.assertEqual(out["operation"], "library.create_folder")
        self.assertEqual(out["input"], {"name": "实验资料"})

    def test_chat_archive_proposal_requires_page_chat(self) -> None:
        sid = self._make_session()
        out = self._propose("归档这个对话", page_context=self._page_chat(sid))
        self.assertEqual(out["operation"], "chat.archive")
        self.assertEqual(out["input"], {"session_id": sid})
        self.assertIsNone(self._propose("归档这个对话"))  # 无页面实体不猜

    def test_chat_move_proposal_resolves_workspace(self) -> None:
        sid = self._make_session()
        wid = self._make_workspace("物理区")
        _ = self._make_workspace("物理区二")
        results = [{"tool": "resolve_destination", "data": {"candidates": [
            {"kind": "workspace", "title": "物理区",
             "target": {"kind": "workspace_chat", "workspace_id": wid}},
            {"kind": "workspace", "title": "物理区二",
             "target": {"kind": "workspace_chat", "workspace_id": "w2"}},
        ]}}]
        out = self._propose("把这个对话移动到物理区", results=results,
                            page_context=self._page_chat(sid))
        self.assertEqual(out["operation"], "chat.move_workspace")
        self.assertEqual(out["input"],
                         {"session_id": sid, "workspace_id": wid})
        # 多候选命中 → 不给动作（choices 卡负责）。
        results_amb = [{"tool": "resolve_destination", "data": {"candidates": [
            {"kind": "workspace", "title": "物理区",
             "target": {"kind": "workspace_chat", "workspace_id": wid}},
            {"kind": "workspace", "title": "物理区",
             "target": {"kind": "workspace_chat", "workspace_id": "w2"}},
        ]}}]
        self.assertIsNone(self._propose(
            "把这个对话移动到物理区", results=results_amb,
            page_context=self._page_chat(sid)))

    def test_intent_routes_b05_text_to_prepare_action(self) -> None:
        import asyncio
        from app.agents.site_assistant.intent import parse_intent
        for text in ("创建工作区名为冲刺区", "新建一个资料文件夹 错题本",
                     "删除这个对话", "把这个对话移到物理区"):
            self.assertEqual(
                asyncio.run(parse_intent(text, lang="zh")).kind,
                "prepare_action", text)
        # 疑问语气不触发写路径。
        self.assertNotEqual(
            asyncio.run(parse_intent("怎么创建工作区？", lang="zh")).kind,
            "prepare_action")


if __name__ == "__main__":
    unittest.main()
