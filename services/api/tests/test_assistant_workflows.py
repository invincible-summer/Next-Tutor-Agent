"""C01/C02 工作流执行器与模板回归。

覆盖：draft 创建（不写业务）与幂等；预览 plan_hash 绑定批准；启动后
步骤按依赖执行（写步骤串行、幂等不重复创建）；来源缺失步骤失败；
部分成功与整体成功的终态；取消（未开始步骤 cancelled、产物保留）；
失败步骤重试（复用同一 action_id 幂等）；每用户活跃工作流上限。
"""
from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest import mock

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import workflows as wf_svc
from app.agents.site_assistant import workflow_templates
from app.core import assistant_store as store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _WfCase(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_wf_" + _hex()
        self.cid = "astc_wf_" + _hex()
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _create(self, template: str, objective: str = "建立学习环境",
                selection_ids: dict | None = None, scope: dict | None = None,
                client_request_id: str = ""):
        return wf_svc.create_workflow(
            self.sid, conversation_id=self.cid, template=template,
            objective=objective, scope=scope or {},
            selection_ids=selection_ids or {},
            client_request_id=client_request_id or "cr_" + _hex())

    def _approve(self, wf: dict) -> dict:
        preview = wf_svc.workflow_preview(self.sid, wf)
        return wf_svc.approve_workflow(
            self.sid, wf["workflow_id"],
            expected_revision=preview["revision"],
            plan_hash_in=preview["plan_hash"],
            approved_step_ids=[s["step_id"] for s in wf["steps"]])

    def _make_textbook(self, title: str = "教材") -> str:
        from app.core.library import load_library, save_library
        from app.core import textbook as tb_store
        lib = load_library(self.sid)
        meta = lib.add_file("", f"{title}.pdf", "正文")
        save_library(lib)
        tb_store._save(self.sid, [{
            "id": "tb_wf_" + _hex(), "title": title,
            "file_id": meta["id"], "status": "ready", "kind": "single",
            "created_at": 0, "updated_at": 0}])
        return str(meta["id"])


class SetupSpaceFlowTest(_WfCase):
    def test_full_flow_creates_workspace_and_goal(self) -> None:
        fid = self._make_textbook()
        wf = self._create("setup_learning_space",
                          objective="两周学完力学基础",
                          selection_ids={"file_ids": [fid],
                                         "workspace_name": "力学区"})
        self.assertEqual(wf["state"], "draft")
        # 幂等：同 client_request_id 返回同一草稿。
        again = self._create("setup_learning_space",
                             objective="两周学完力学基础",
                             selection_ids={"file_ids": [fid]},
                             client_request_id=wf["client_request_id"])
        self.assertEqual(again["workflow_id"], wf["workflow_id"])
        approved = self._approve(wf)
        self.assertEqual(approved["state"], "awaiting_approval")
        started = wf_svc.start_workflow(
            self.sid, wf["workflow_id"],
            client_request_id="cr_start_" + _hex(),
            expected_revision=approved["revision"])
        self.assertEqual(started["state"], "queued")
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        final = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        self.assertEqual(final["state"], "succeeded")
        # 真实业务产物：工作区 + 目标。
        from app.core.workspace import load_workspace, list_workspaces
        names = [w["name"] for w in list_workspaces()
                 if w.get("student_id") == self.sid]
        self.assertIn("力学区", names)
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        goals = get_orchestration_service()._load(self.sid).goals
        self.assertTrue(any(g.title == "两周学完力学基础" for g in goals))
        # 重跑（恢复语义）：写步骤幂等，不重复创建。
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        names2 = [w["name"] for w in list_workspaces()
                  if w.get("student_id") == self.sid
                  and w["name"] == "力学区"]
        self.assertEqual(len(names2), 1)
        # 交接步骤产出落点。
        handoff = next(s for s in final["steps"]
                       if s["step_id"] == "step_04")
        self.assertEqual(handoff["output_ref"]["navigation_target"]
                         ["route_id"], "chat")

    def test_missing_source_fails_read_step(self) -> None:
        wf = self._create("setup_learning_space",
                          selection_ids={"file_ids": ["file_missing_wf"]})
        approved = self._approve(wf)
        wf_svc.start_workflow(
            self.sid, wf["workflow_id"],
            client_request_id="cr_start2_" + _hex(),
            expected_revision=approved["revision"])
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        final = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        read = final["steps"][0]
        self.assertEqual(read["state"], "failed")
        self.assertEqual(read["error"]["code"], "entity_not_found")
        # 依赖步骤未执行；终态为 failed（无已完成必要步骤）。
        self.assertEqual(final["steps"][1]["state"], "pending")
        self.assertEqual(final["state"], "failed")

    def test_retry_failed_step_reuses_idempotency(self) -> None:
        fid = self._make_textbook()
        wf = self._create("organize_materials",
                          selection_ids={"file_ids": [fid],
                                         "folder_name": "错题本"})
        # 步骤输入改为重命名一个尚不存在的文件：写步骤以真实领域 404
        # 失败；重试前在外部补齐该文件（同输入重试成功，§23.5 retry）。
        rec_wf = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        rec_wf["steps"][1]["operation"] = "library.rename_file"
        rec_wf["steps"][1]["input"] = {"file_id": "file_wf_target",
                                       "filename": "重命名.pdf"}
        wf_svc._persist(self.sid, rec_wf)
        approved = self._approve(rec_wf)
        wf_svc.start_workflow(
            self.sid, wf["workflow_id"],
            client_request_id="cr_r_" + _hex(),
            expected_revision=approved["revision"])
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        final = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        write = final["steps"][1]
        self.assertEqual(write["state"], "failed")
        self.assertEqual(write["error"]["code"], "entity_not_found")
        # 无已完成的必要步骤（零产物）→ failed（§23.4-10）。
        self.assertEqual(final["state"], "failed")
        # 补齐外部条件（文件出现），经 retry 端点语义重试同一失败步骤。
        from app.core.library import load_library, save_library
        lib = load_library(self.sid)
        lib.files.append({"id": "file_wf_target", "filename": "旧名.pdf",
                          "folder_id": "", "workspace_id": "",
                          "created_at": 0, "updated_at": 0})
        save_library(lib)
        wf2 = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        retried = wf_svc.retry_step(
            self.sid, wf["workflow_id"], step_id="step_02",
            expected_revision=wf2["revision"],
            client_request_id="cr_retry_" + _hex())
        self.assertEqual(retried["state"], "queued")
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        final = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        self.assertEqual(final["steps"][1]["state"], "succeeded")
        self.assertEqual(final["state"], "succeeded")
        lib = load_library(self.sid)
        target = next(f for f in lib.files if f["id"] == "file_wf_target")
        self.assertEqual(target["filename"], "重命名.pdf")
        # 幂等：步骤动作只有一个，重试未生成第二个动作记录。
        record = store.load_conversation(self.sid, self.cid)
        step_action_ids = [aid for aid, a in (record["actions"]).items()
                           if aid.startswith("astw_step:")]
        self.assertEqual(len(step_action_ids), 1)


class LifecycleRulesTest(_WfCase):
    def test_active_workflow_limit(self) -> None:
        fid = self._make_textbook()
        for i in range(2):
            wf = self._create("organize_materials",
                              objective=f"整理{i}",
                              selection_ids={"file_ids": [fid],
                                             "folder_name": f"夹{i}"})
            self._approve(wf)
        with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
            self._create("organize_materials", objective="第三个",
                         selection_ids={"file_ids": [fid]})
        self.assertEqual(ctx.exception.code, "workflow_limit")

    def test_cancel_keeps_completed_outputs(self) -> None:
        from app.core.library import load_library
        fid = self._make_textbook()
        wf = self._create("organize_materials",
                          selection_ids={"file_ids": [fid],
                                         "folder_name": "取消夹"})
        approved = self._approve(wf)
        wf_svc.start_workflow(
            self.sid, wf["workflow_id"],
            client_request_id="cr_c_" + _hex(),
            expected_revision=approved["revision"])
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        # 完成后取消：幂等返回当前状态，产物保留。
        cancelled = wf_svc.cancel_workflow(self.sid, wf["workflow_id"])
        self.assertIn(cancelled["state"], ("succeeded", "cancelled"))
        lib = load_library(self.sid)
        self.assertTrue(any(f.get("name") == "取消夹"
                            for f in lib.folders))
        # 新建工作流可取消：未开始步骤 cancelled。
        wf2 = self._create("continue_learning_session",
                           objective="继续学习")
        out = wf_svc.cancel_workflow(self.sid, wf2["workflow_id"])
        self.assertEqual(out["state"], "cancelled")
        self.assertTrue(all(s["state"] == "cancelled"
                            for s in out["steps"]))

    def test_revision_conflict_and_stale_plan(self) -> None:
        wf = self._create("continue_learning_session", objective="继续")
        with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
            wf_svc.approve_workflow(
                self.sid, wf["workflow_id"], expected_revision=999,
                plan_hash_in="wph_stale", approved_step_ids=["step_01"])
        self.assertEqual(ctx.exception.code, "revision_conflict")
        with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
            preview = wf_svc.workflow_preview(self.sid, wf)
            wf_svc.approve_workflow(
                self.sid, wf["workflow_id"],
                expected_revision=preview["revision"],
                plan_hash_in="wph_wrong",
                approved_step_ids=["step_01"])
        self.assertEqual(ctx.exception.code, "preview_stale")

    def test_all_six_templates_build(self) -> None:
        for template in workflow_templates.TEMPLATES:
            wf = self._create(template, objective="目标文字")
            self.assertTrue(wf["steps"], template)
            self.assertLessEqual(len(wf["steps"]), wf_svc.MAX_STEPS)
            writes = [s for s in wf["steps"] if s["kind"] == "write"]
            self.assertLessEqual(len(writes), wf_svc.MAX_WRITE_STEPS)
            preview = wf_svc.workflow_preview(self.sid, wf)
            self.assertTrue(preview["plan_hash"].startswith("wph_"))
            # 活跃上限 2：逐个取消释放配额（§23.4-1）。
            out = wf_svc.cancel_workflow(self.sid, wf["workflow_id"])
            self.assertEqual(out["state"], "cancelled")

    def test_unknown_template_rejected(self) -> None:
        with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
            self._create("nonexistent_template")
        self.assertEqual(ctx.exception.code, "template_unknown")

    def test_resume_from_interrupted(self) -> None:
        wf = self._create("continue_learning_session", objective="继续")
        approved = self._approve(wf)
        wf_svc.start_workflow(self.sid, wf["workflow_id"],
                              client_request_id="cr_s_" + _hex(),
                              expected_revision=approved["revision"])
        # 模拟进程重启：状态置 interrupted 后经 resume 继续。
        rec = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        rec["state"] = "interrupted"
        wf_svc._persist(self.sid, rec)
        rec = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        resumed = wf_svc.resume_workflow(self.sid, wf["workflow_id"],
                                         expected_revision=rec["revision"])
        self.assertEqual(resumed["state"], "queued")
        # 非等待状态重复 resume 拒绝。
        with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
            wf_svc.resume_workflow(self.sid, wf["workflow_id"],
                                   expected_revision=resumed["revision"])
        self.assertEqual(ctx.exception.code, "state_invalid")
        asyncio.run(wf_svc.run_workflow(self.sid, wf["workflow_id"]))
        final = wf_svc.load_workflow(self.sid, wf["workflow_id"])
        self.assertEqual(final["state"], "succeeded")

    def test_disabled_workflows_rejected(self) -> None:
        from app.core.config import settings
        with mock.patch.object(settings, "site_assistant_workflows_enabled",
                               False):
            with self.assertRaises(wf_svc.WorkflowRejected) as ctx:
                self._create("continue_learning_session", objective="目标")
            self.assertEqual(ctx.exception.code, "capability_disabled")

    def test_start_workflow_action_creates_draft_idempotently(self) -> None:
        """§21.1/§23.5：prepare_action 提案 → execute 只建 draft；重试
        复用同一 workflow_id（幂等），不重复创建。"""
        import asyncio
        from app.agents.site_assistant import actions as actions_svc
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent

        text = "把这些教材整理成一个学习区"
        parsed = asyncio.run(parse_intent(text, lang="zh"))
        self.assertEqual(parsed.kind, "prepare_action")
        proposed = policy.decide_actions(
            conversation_id=self.cid, turn_id="astt_wf", parsed=parsed,
            lang="zh", meta={}, results=[],
            page_context=None, page_epoch=0)
        self.assertEqual(len(proposed), 1)
        action = proposed[0]
        self.assertEqual(action["payload"]["kind"], "start_workflow")
        self.assertEqual(action["payload"]["template"],
                         "setup_learning_space")
        self.assertEqual(action["execution"], "user_click")
        # 落盘后 execute：创建 draft 工作流（不写业务）。
        record = store.load_conversation(self.sid, self.cid)
        record["actions"][action["action_id"]] = action
        store.save_conversation(self.sid, record)
        out = actions_svc.execute_action(
            self.sid, action["action_id"],
            invocation_id="inv_wf_1", client_instance_id="wf-test",
            route_epoch=0)
        self.assertEqual(out["action"]["state"], "succeeded")
        wid = out["business_result"]["entity_id"]
        created = wf_svc.load_workflow(self.sid, wid)
        self.assertEqual(created["state"], "draft")
        self.assertEqual(created["template"], "setup_learning_space")
        # 重复 execute（网络重试语义）：复用同一工作流，不重复创建。
        out2 = actions_svc.execute_action(
            self.sid, action["action_id"],
            invocation_id="inv_wf_1", client_instance_id="wf-test",
            route_epoch=0)
        self.assertEqual(out2["business_result"]["entity_id"], wid)
        _items, total = wf_svc.list_workflows(self.sid, limit=10)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
