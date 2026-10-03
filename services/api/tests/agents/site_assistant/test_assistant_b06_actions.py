"""B06 笔记/学习编排领域动作回归（10 操作 + 两阶段重规划）。

覆盖：执行、预览差异、review_required 审批链、revision 冲突 409、
归属 404、§21.6.3 候选两阶段（预览绑定候选、提交逐字应用、目标变化
使候选失效）、§21.5 撤销（版本恢复/字段回滚/子任务删除）。
"""
from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import store


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class _B06Case(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_b06_" + _hex()
        self.cid = "astc_b06"
        store.save_conversation(self.sid, {
            "conversation_id": self.cid, "revision": 1,
            "messages": [], "turns": {}, "actions": {}, "accepted": {}})

    def _add_action(self, action_id: str, payload: dict) -> None:
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"][action_id] = {
            "action_id": action_id, "conversation_id": self.cid,
            "turn_id": "astt_b06", "label": payload.get("operation", "写"),
            "payload": payload, "execution": "user_click",
            "state": "proposed",
            "created_at": store.utc_now_iso(),
            "expires_at": (datetime.now(tz=timezone.utc)
                           + timedelta(minutes=10)).isoformat(),
            "business_result": {"kind": "none"},
        }
        store.save_conversation(self.sid, rec)

    def _execute(self, action_id: str, approval_id: str | None = None):
        from app.agents.site_assistant import actions as actions_svc
        return actions_svc.execute_action(
            self.sid, action_id, invocation_id=str(uuid.uuid4()),
            client_instance_id="client-b06", route_epoch=1,
            approval_id=approval_id, main_loop=None)

    def _preview_and_approve(self, action_id: str) -> str:
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, action_id)
        approval = previews.approve(
            self.sid, action_id, preview_id=preview["preview_id"],
            parameter_hash_in=preview["parameter_hash"], decision="approve")
        return approval["approval_id"]

    def _undo(self, action_id: str):
        from app.agents.site_assistant import undo as undo_svc
        from app.agents.site_assistant.actions import locate_action
        _rec, _c, action = locate_action(self.sid, action_id)
        return undo_svc.undo_action(
            self.sid, action_id, client_request_id="cur_" + _hex(),
            expected_result_revision=str(
                (action.get("business_result") or {}).get(
                    "result_revision") or ""))

    def _make_note(self, title: str = "物理笔记",
                   content: str = "原有内容") -> tuple[str, int]:
        from app import notes as notes_store
        vault = notes_store.load_vault(self.sid)
        note = vault.create_note(title=title, content=content)
        notes_store.save_vault(vault)
        return str(note["id"]), int(note.get("revision") or 1)

    def _make_goal(self, title: str = "掌握力学") -> str:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        goal = get_orchestration_service().add_goal(self.sid, title=title)
        return str(goal.id)

    def _make_task(self, title: str = "复习任务") -> str:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        return get_orchestration_service().add_task(
            self.sid, title=title).id


class NoteActionsTest(_B06Case):
    def test_append_executes_and_undo_restores(self) -> None:
        from app import notes as notes_store
        note_id, revision = self._make_note(content="原有内容")
        self._add_action("asta_na", {
            "kind": "domain_write", "operation": "note.append",
            "input": {"note_id": note_id, "base_revision": revision,
                      "append_markdown": "追加的第一段"}})
        preview = self._preview_ok("asta_na", "intent_sufficient")
        self.assertEqual(preview["changes"][0]["after"], "追加的第一段")
        result = self._execute("asta_na")
        self.assertEqual(result["action"]["state"], "succeeded")
        vault = notes_store.load_vault(self.sid)
        self.assertIn("追加的第一段", vault.read_note(note_id))
        self.assertEqual(int(vault.find_note(note_id)["revision"]),
                         revision + 1)
        # 撤销：恢复 base_revision 版本（生成新当前版本）。
        out = self._undo("asta_na")
        self.assertTrue(out["undone"])
        vault = notes_store.load_vault(self.sid)
        self.assertEqual(vault.read_note(note_id), "原有内容")
        self.assertEqual(int(vault.find_note(note_id)["revision"]),
                         revision + 2)

    def test_append_stale_revision_rejected(self) -> None:
        note_id, revision = self._make_note()
        self._add_action("asta_nas", {
            "kind": "domain_write", "operation": "note.append",
            "input": {"note_id": note_id, "base_revision": revision + 5,
                      "append_markdown": "过期追加"}})
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_nas")
        self.assertEqual(ctx.exception.code, "revision_conflict")
        with self.assertRaises(ActionRejected):
            self._execute("asta_nas")

    def test_replace_requires_preview_and_shows_diff(self) -> None:
        from app import notes as notes_store
        note_id, revision = self._make_note(content="旧正文")
        self._add_action("asta_nr", {
            "kind": "domain_write", "operation": "note.replace",
            "input": {"note_id": note_id, "base_revision": revision,
                      "title": "", "content": "新正文", "summary": ""}})
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, "asta_nr")
        self.assertEqual(preview["approval"], "review_required")
        fields = {c["field"]: c for c in preview["changes"]}
        self.assertEqual(fields["content"]["before"], "旧正文")
        self.assertEqual(fields["content"]["after"], "新正文")
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_nr")  # 无许可
        self.assertEqual(ctx.exception.code, "preview_stale")
        result = self._execute("asta_nr", self._preview_and_approve("asta_nr"))
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(
            notes_store.load_vault(self.sid).read_note(note_id), "新正文")
        out = self._undo("asta_nr")
        self.assertTrue(out["undone"])
        self.assertEqual(
            notes_store.load_vault(self.sid).read_note(note_id), "旧正文")

    def test_move_single_direct_multi_requires_review(self) -> None:
        from app import notes as notes_store
        vault = notes_store.load_vault(self.sid)
        folder = vault.create_folder("复习夹")
        notes_store.save_vault(vault)
        n1, _r1 = self._make_note("笔记一")
        n2, _r2 = self._make_note("笔记二")
        self._add_action("asta_nm1", {
            "kind": "domain_write", "operation": "note.move",
            "input": {"note_ids": [n1], "folder_id": folder["id"]}})
        self._preview_ok("asta_nm1", "intent_sufficient")
        result = self._execute("asta_nm1")
        self.assertEqual(result["action"]["state"], "succeeded")
        vault = notes_store.load_vault(self.sid)
        self.assertEqual(vault.find_note(n1)["folder_id"], folder["id"])
        # 多篇移动 → review_required（§21.3 列完整目标确认）。
        self._add_action("asta_nm2", {
            "kind": "domain_write", "operation": "note.move",
            "input": {"note_ids": [n1, n2], "folder_id": ""}})
        self._preview_ok("asta_nm2", "review_required")
        result = self._execute(
            "asta_nm2", self._preview_and_approve("asta_nm2"))
        self.assertEqual(result["action"]["state"], "succeeded")
        vault = notes_store.load_vault(self.sid)
        self.assertEqual(vault.find_note(n1)["folder_id"], "")
        self.assertEqual(vault.find_note(n2)["folder_id"], "")
        # 撤销单篇移动。
        out = self._undo("asta_nm2")
        self.assertTrue(out["undone"])
        vault = notes_store.load_vault(self.sid)
        self.assertEqual(vault.find_note(n1)["folder_id"], folder["id"])

    def test_set_review_toggles_and_undo(self) -> None:
        from app import notes as notes_store
        note_id, _rev = self._make_note()
        self._add_action("asta_nsr", {
            "kind": "domain_write", "operation": "note.set_review",
            "input": {"note_id": note_id, "enabled": True}})
        self._preview_ok("asta_nsr", "intent_sufficient")
        result = self._execute("asta_nsr")
        self.assertEqual(result["action"]["state"], "succeeded")
        meta = notes_store.load_vault(self.sid).find_note(note_id)
        self.assertTrue((meta.get("review") or {}).get("enabled"))
        out = self._undo("asta_nsr")
        self.assertTrue(out["undone"])
        meta = notes_store.load_vault(self.sid).find_note(note_id)
        self.assertFalse((meta.get("review") or {}).get("enabled"))

    def test_restore_revision_flow(self) -> None:
        from app import notes as notes_store
        note_id, rev1 = self._make_note(content="第一版")
        vault = notes_store.load_vault(self.sid)
        vault.write_note(note_id, "第二版", author="user",
                         summary="编辑")   # rev1+1
        notes_store.save_vault(vault)
        current = int(vault.find_note(note_id)["revision"])
        self._add_action("asta_nrr", {
            "kind": "domain_write", "operation": "note.restore_revision",
            "input": {"note_id": note_id, "revision": rev1,
                      "expected_current_revision": current}})
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, "asta_nrr")
        self.assertEqual(preview["approval"], "review_required")
        fields = {c["field"]: c for c in preview["changes"]}
        self.assertEqual(fields["content"]["after"], "第一版")
        result = self._execute(
            "asta_nrr", self._preview_and_approve("asta_nrr"))
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(
            notes_store.load_vault(self.sid).read_note(note_id), "第一版")
        out = self._undo("asta_nrr")
        self.assertTrue(out["undone"])
        self.assertEqual(
            notes_store.load_vault(self.sid).read_note(note_id), "第二版")

    def _preview_ok(self, action_id: str, want_approval: str) -> dict:
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, action_id)
        self.assertEqual(preview["approval"], want_approval)
        return preview


class GoalPlanActionsTest(_B06Case):
    def test_goal_create_previews_then_executes(self) -> None:
        self._add_action("asta_gc", {
            "kind": "domain_write", "operation": "goal.create",
            "input": {"title": "两周攻克电磁学"}})
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, "asta_gc")
        self.assertEqual(preview["approval"], "review_required")
        self.assertTrue(any("重新规划" in s
                            for s in preview["side_effects"]))
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected):
            self._execute("asta_gc")
        result = self._execute("asta_gc", self._preview_and_approve("asta_gc"))
        self.assertEqual(result["action"]["state"], "succeeded")
        goal_id = result["business_result"]["entity_id"]
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        state = get_orchestration_service()._load(self.sid)
        self.assertTrue(any(g.id == goal_id for g in state.goals))
        self.assertGreater(state.last_plan_attempt, 0.0)  # 已触发规划

    def test_goal_update_404_and_execute(self) -> None:
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        self._add_action("asta_gu", {
            "kind": "domain_write", "operation": "goal.update",
            "input": {"goal_id": "g_missing_1", "title": "新标题"}})
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_gu")
        self.assertEqual(ctx.exception.code, "entity_not_found")
        goal_id = self._make_goal("旧目标")
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"]["asta_gu"]["payload"]["input"] = {
            "goal_id": goal_id, "title": "新目标名"}
        store.save_conversation(self.sid, rec)
        result = self._execute("asta_gu", self._preview_and_approve("asta_gu"))
        self.assertEqual(result["action"]["state"], "succeeded")
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        goal = next(g for g in get_orchestration_service()._load(
            self.sid).goals if g.id == goal_id)
        self.assertEqual(goal.title, "新目标名")

    def test_plan_regenerate_two_phase_commits_candidate(self) -> None:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        self._make_goal("计划目标")
        svc = get_orchestration_service()
        state0 = svc._load(self.sid)
        revision = (f"{len(state0.weekly_plan or [])}:"
                    f"{int(state0.last_plan_attempt or 0)}")
        self._add_action("asta_pr", {
            "kind": "domain_write", "operation": "plan.regenerate",
            "input": {"expected_plan_revision": revision}})
        # 阶段一：构建候选（确定性路径，无 LLM 配置）。
        candidate = asyncio.run(svc.build_plan_candidate(self.sid))
        self.assertTrue(candidate["candidate_id"].startswith("pc_"))
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, "asta_pr")
        self.assertEqual(preview["approval"], "review_required")
        self.assertEqual(preview["candidate_id"], candidate["candidate_id"])
        result = self._execute("asta_pr", self._preview_and_approve("asta_pr"))
        self.assertEqual(result["action"]["state"], "succeeded")
        self.assertEqual(result["business_result"]["result_revision"],
                         candidate["candidate_id"])
        # 候选已消费：再次提交同候选 → preview_stale。
        rec = store.load_conversation(self.sid, self.cid)
        rec["actions"]["asta_pr"]["business_result"] = {"kind": "none"}
        rec["actions"]["asta_pr"]["state"] = "proposed"
        store.save_conversation(self.sid, rec)
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            self._execute("asta_pr", self._approval_of_consumed("asta_pr"))
        self.assertEqual(ctx.exception.code, "preview_stale")

    def _approval_of_consumed(self, action_id: str) -> str:
        """对已消费候选重新 approve（许可能签发但 execute 会因候选缺失失败）。"""
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        try:
            return self._preview_and_approve(action_id)
        except ActionRejected:
            raise

    def test_plan_candidate_goals_changed_invalidates(self) -> None:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        self._make_goal("稳定目标")
        svc = get_orchestration_service()
        asyncio.run(svc.build_plan_candidate(self.sid))
        self._add_action("asta_pr2", {
            "kind": "domain_write", "operation": "plan.regenerate",
            "input": {"expected_plan_revision": "0:0"}})
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, "asta_pr2")
        candidate_id = preview["candidate_id"]
        # 候选生成后目标被修改（指纹变化）→ 提交拒绝。
        svc.add_goal(self.sid, title="后来的目标")
        ok, reason = svc.commit_plan_candidate(self.sid, candidate_id)
        self.assertFalse(ok)
        self.assertEqual(reason, "goals_changed")


class TaskSubtaskActionsTest(_B06Case):
    def _make_task(self, title: str = "复习任务") -> str:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        return get_orchestration_service().add_task(
            self.sid, title=title).id

    def test_task_update_single_field_direct(self) -> None:
        task_id = self._make_task()
        self._add_action("asta_tu", {
            "kind": "domain_write", "operation": "task.update",
            "input": {"task_id": task_id, "title": "改名后的任务"}})
        self._preview_ok("asta_tu", "intent_sufficient")
        result = self._execute("asta_tu")
        self.assertEqual(result["action"]["state"], "succeeded")
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        task = next(t for t in get_orchestration_service()._load(
            self.sid).daily_tasks if t.id == task_id)
        self.assertEqual(task.title, "改名后的任务")
        # 撤销恢复原标题。
        out = self._undo("asta_tu")
        self.assertTrue(out["undone"])
        task = next(t for t in get_orchestration_service()._load(
            self.sid).daily_tasks if t.id == task_id)
        self.assertEqual(task.title, "复习任务")

    def test_task_update_multi_field_requires_review(self) -> None:
        task_id = self._make_task()
        self._add_action("asta_tu2", {
            "kind": "domain_write", "operation": "task.update",
            "input": {"task_id": task_id, "title": "多字段",
                      "estimate_minutes": 30}})
        self._preview_ok("asta_tu2", "review_required")
        result = self._execute(
            "asta_tu2", self._preview_and_approve("asta_tu2"))
        self.assertEqual(result["action"]["state"], "succeeded")

    def test_task_update_missing_404(self) -> None:
        self._add_action("asta_tu3", {
            "kind": "domain_write", "operation": "task.update",
            "input": {"task_id": "user_missing_1", "title": "x"}})
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_tu3")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def test_subtask_create_and_undo(self) -> None:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        svc = get_orchestration_service()
        # 无图谱/掌握数据时确定性规划可能合法产出空计划：手工建 user 周
        # 与周任务，验证子任务动作本身（不依赖规划产物）。
        week = svc.add_week(self.sid, focus="力学周")
        week_task = svc.add_week_task(
            self.sid, week.week_index, title="复习牛顿定律")
        self._add_action("asta_sc", {
            "kind": "domain_write", "operation": "subtask.create",
            "input": {"week_index": week.week_index,
                      "week_task_id": week_task.id,
                      "title": "先看例题", "estimate_minutes": 15}})
        self._preview_ok("asta_sc", "intent_sufficient")
        result = self._execute("asta_sc")
        self.assertEqual(result["action"]["state"], "succeeded")
        state = svc._load(self.sid)
        wt = next(t for w in state.weekly_plan
                  if w.week_index == week.week_index
                  for t in w.tasks if t.id == week_task.id)
        self.assertTrue(any(s.title == "先看例题" for s in wt.subtasks))
        out = self._undo("asta_sc")
        self.assertTrue(out["undone"])
        state = svc._load(self.sid)
        wt = next(t for w in state.weekly_plan
                  if w.week_index == week.week_index
                  for t in w.tasks if t.id == week_task.id)
        self.assertFalse(any(s.title == "先看例题" for s in wt.subtasks))

    def test_subtask_missing_task_404(self) -> None:
        self._add_action("asta_sc2", {
            "kind": "domain_write", "operation": "subtask.create",
            "input": {"week_index": 0, "week_task_id": "wt_missing_1",
                      "title": "x", "estimate_minutes": 15}})
        from app.agents.site_assistant import previews
        from app.agents.site_assistant.actions import ActionRejected
        with self.assertRaises(ActionRejected) as ctx:
            previews.build_preview(self.sid, "asta_sc2")
        self.assertEqual(ctx.exception.code, "entity_not_found")

    def _preview_ok(self, action_id: str, want_approval: str) -> dict:
        from app.agents.site_assistant import previews
        preview = previews.build_preview(self.sid, action_id)
        self.assertEqual(preview["approval"], want_approval)
        return preview


class B06ProposalTest(_B06Case):
    """policy 确定性抽取（B06 句式）→ operation+input。"""

    def _propose(self, text: str, results=None, page_context=None,
                 student_id=None):
        import asyncio
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent
        parsed = asyncio.run(parse_intent(text, lang="zh"))
        if parsed.kind != "prepare_action":
            return "not-prepare"
        return policy._propose_domain_write(
            parsed, results or [], zh=True, page_context=page_context,
            student_id=student_id or self.sid)

    def test_goal_create_proposal(self) -> None:
        out = self._propose("帮我创建一个学习目标：两周攻克电磁学")
        self.assertEqual(out["operation"], "goal.create")
        self.assertEqual(out["input"]["title"], "两周攻克电磁学")
        self.assertEqual(out["execution"], "user_click")  # review_required
        self.assertIsNone(self._propose("创建一个目标"))  # 无标题不给动作

    def test_plan_regenerate_proposal(self) -> None:
        self.assertIsNone(self._propose("重新生成学习计划"))  # 无目标
        self._make_goal("目标")
        out = self._propose("重新生成学习计划")
        self.assertEqual(out["operation"], "plan.regenerate")
        self.assertTrue(out["input"]["expected_plan_revision"])

    def test_note_append_proposal_requires_page_note(self) -> None:
        note_id, revision = self._make_note()
        page = {"entity": {"kind": "note", "id": note_id}}
        out = self._propose("在这篇笔记末尾追加：浮力公式 F=ρgV",
                            page_context=page)
        self.assertEqual(out["operation"], "note.append")
        self.assertEqual(out["input"]["base_revision"], revision)
        self.assertEqual(out["execution"], "automatic")
        self.assertIsNone(self._propose("在这篇笔记末尾追加：内容"))
        self.assertIsNone(self._propose(
            "在这篇笔记末尾追加：内容", page_context=page,
            student_id="usr_other_" + _hex()))

    def test_note_review_proposal(self) -> None:
        note_id, _rev = self._make_note()
        page = {"entity": {"kind": "note", "id": note_id}}
        out = self._propose("开启这篇笔记的复习计划", page_context=page)
        self.assertEqual(out["operation"], "note.set_review")
        self.assertTrue(out["input"]["enabled"])
        out = self._propose("关闭这篇笔记复习", page_context=page)
        self.assertFalse(out["input"]["enabled"])

    def test_task_day_move_proposal(self) -> None:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        task_id = self._make_task("刷力学题")
        # 固定时区：提案与断言同侧计算，避免跨 UTC 日界时不稳定。
        tz_name = "Asia/Shanghai"
        today = datetime.now(ZoneInfo(tz_name)).date()
        results = [{"tool": "get_saved_tasks", "data": {
            "today": [{"id": task_id, "title": "刷力学题",
                       "status": "pending", "day": today.isoformat()}],
            "open": []}}]
        import asyncio
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent
        parsed = asyncio.run(parse_intent("把任务刷力学题改到明天", lang="zh"))
        out = policy._propose_domain_write(
            parsed, results, zh=True, page_context=None,
            timezone_name=tz_name, student_id=self.sid)
        self.assertEqual(out["operation"], "task.update")
        self.assertEqual(out["input"]["task_id"], task_id)
        want = (today + timedelta(days=1)).isoformat()
        self.assertEqual(out["input"]["day"], want)
        # 任务名歧义 → 不给动作。
        results_amb = [{"tool": "get_saved_tasks", "data": {
            "today": [
                {"id": task_id, "title": "刷力学题", "status": "pending",
                 "day": today.isoformat()},
                {"id": "t2", "title": "刷力学题", "status": "pending",
                 "day": today.isoformat()}],
            "open": []}}]
        self.assertIsNone(policy._propose_domain_write(
            parsed, results_amb, zh=True, page_context=None,
            timezone_name=tz_name, student_id=self.sid))

    def test_intent_routes_b06_text_to_prepare_action(self) -> None:
        import asyncio
        from app.agents.site_assistant.intent import parse_intent
        for text in ("帮我创建学习目标：一个月学完线代",
                     "重新生成我的学习计划"):
            self.assertEqual(
                asyncio.run(parse_intent(text, lang="zh")).kind,
                "prepare_action", text)


if __name__ == "__main__":
    unittest.main()
