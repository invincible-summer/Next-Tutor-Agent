"""站内助手导览编排回归（A09）。

覆盖：意图快路闭集、时间窗口（§6.4 含夏令时）、轮预算（≤4 工具/
≤2 模型/60s）、只读 readers（empty≠error、disabled、范围解析 §6.2）、
确定性事实卡（数字只来自工具）、LLM 意图解析与修复回落、端到端
turn（模型不可用走确定性兜底、来源登记、消息 scope）。
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from tests.support.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import (
    catalog, intent, presenters, readers, tools)


class IntentFastPathTest(unittest.TestCase):
    """零 LLM 快路：意图闭集与时间窗口提取（不触存储根）。"""

    def _parse(self, text: str, lang: str = "zh") -> intent.ParsedIntent:
        return asyncio.run(intent.parse_intent(text, lang=lang))

    def test_navigate_verb_with_alias(self) -> None:
        parsed = self._parse("带我去备课上课。")
        self.assertEqual(parsed.kind, "navigate")
        self.assertEqual(parsed.module_route, catalog.AssistantRouteId.COURSE)

    def test_lesson_edit_navigate_intent(self) -> None:
        # 「编辑课件」：明确动作语气 → navigate + lesson_edit（§8.3 view=edit）
        parsed = self._parse("帮我编辑这个课件")
        self.assertEqual(parsed.kind, "navigate")
        self.assertTrue(parsed.lesson_edit)
        self.assertIsNone(parsed.module_route)

    def test_lesson_edit_question_stays_guide(self) -> None:
        # 查问语气（怎么编辑）不导航，仍走 guide（§2.2 区分查问与动作）。
        parsed = self._parse("课件怎么编辑？")
        self.assertNotEqual(
            (parsed.kind, parsed.lesson_edit), ("navigate", True))

    def test_question_marker_yields_guide(self) -> None:
        parsed = self._parse("备课入口在哪里？")
        self.assertEqual(parsed.kind, "guide")
        self.assertIsNotNone(parsed.primary_module)

    def test_learning_report_recent_window(self) -> None:
        parsed = self._parse("最近一周我学得怎么样？")
        self.assertEqual(parsed.kind, "learning_report")
        self.assertEqual(parsed.window, "recent")

    def test_learning_report_last_week(self) -> None:
        parsed = self._parse("上周的学习近况如何")
        self.assertEqual(parsed.kind, "learning_report")
        self.assertEqual(parsed.window, "last_week")

    def test_custom_days_window(self) -> None:
        parsed = self._parse("近30天的学习总结")
        self.assertEqual(parsed.kind, "learning_report")
        self.assertEqual(parsed.window, "custom")
        self.assertEqual(parsed.custom_days, 30)

    def test_custom_days_clamped(self) -> None:
        self.assertEqual(intent.detect_window("近365天")[1], 90)

    def test_teaching_report(self) -> None:
        parsed = self._parse("有没有AI教学评价？")
        self.assertEqual(parsed.kind, "teaching_report")

    def test_planning_advice(self) -> None:
        parsed = self._parse("今天学什么？")
        self.assertEqual(parsed.kind, "planning_advice")

    def test_prepare_action(self) -> None:
        parsed = self._parse("帮我备一节课")
        self.assertEqual(parsed.kind, "prepare_action")

    def test_greeting_general_chat(self) -> None:
        parsed = self._parse("你好")
        self.assertEqual(parsed.kind, "general_chat")

    def test_bare_anaphora_clarify(self) -> None:
        parsed = self._parse("第二个")
        self.assertEqual(parsed.kind, "clarify")

    def test_intent_kinds_closed_set(self) -> None:
        for kind in intent.INTENT_KINDS:
            self.assertIn(kind, {
                "guide", "navigate", "search", "learning_report",
                "teaching_report", "planning_advice", "prepare_action",
                "general_chat", "clarify"})


class TimeWindowTest(unittest.TestCase):
    """§6.4：本地日历边界、半开区间、夏令时安全、无效时区回退。"""

    def test_this_week_starts_monday(self) -> None:
        now = datetime(2026, 9, 26, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        start, end, label = intent.resolve_time_window(
            "this_week", "Asia/Shanghai", now=now)
        self.assertEqual(label, "本周")
        self.assertEqual(start.astimezone(ZoneInfo("Asia/Shanghai"))
                         .strftime("%a %H:%M"), "Mon 00:00")
        self.assertEqual(end, now)
        self.assertLess(start, end)

    def test_last_week_full_natural_week(self) -> None:
        now = datetime(2026, 9, 26, tzinfo=ZoneInfo("Asia/Shanghai"))
        start, end, label = intent.resolve_time_window(
            "last_week", "Asia/Shanghai", now=now)
        local_start = start.astimezone(ZoneInfo("Asia/Shanghai"))
        local_end = end.astimezone(ZoneInfo("Asia/Shanghai"))
        self.assertEqual(label, "上周")
        self.assertEqual(local_start.weekday(), 0)
        self.assertEqual(local_start.hour, 0)
        self.assertEqual((local_end - local_start).days, 7)

    def test_recent_is_seven_local_days(self) -> None:
        now = datetime(2026, 9, 26, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        start, end, _ = intent.resolve_time_window(
            "recent", "Asia/Shanghai", now=now)
        local_start = start.astimezone(ZoneInfo("Asia/Shanghai"))
        self.assertEqual(local_start.hour, 0)
        self.assertEqual((now.date() - local_start.date()).days, 6)

    def test_dst_spring_forward_local_midnight(self) -> None:
        # 2026-03-09 美东已进入夏令时；本地零点必须是 04:00Z 而非 05:00Z。
        now = datetime(2026, 3, 10, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        start, _end, _ = intent.resolve_time_window(
            "this_week", "America/New_York", now=now)
        utc_start = start.astimezone(timezone.utc)
        self.assertEqual(utc_start.hour, 4)
        self.assertEqual(utc_start.minute, 0)

    def test_invalid_timezone_falls_back_utc(self) -> None:
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        start, _end, _ = intent.resolve_time_window(
            "this_week", "Not/AZone", now=now)
        self.assertEqual(start.tzinfo, timezone.utc)

    def test_custom_window_capped(self) -> None:
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        start, _end, label = intent.resolve_time_window(
            "custom", "UTC", now=now, custom_days=400)
        self.assertEqual(label, "最近90天")
        self.assertEqual((now.date() - start.date()).days, 89)


class TurnBudgetTest(unittest.TestCase):
    def test_tool_and_model_caps(self) -> None:
        budget = tools.TurnBudget()
        for _ in range(4):
            self.assertTrue(budget.allow_tool())
            budget.tool_calls += 1
        self.assertFalse(budget.allow_tool())
        for _ in range(2):
            self.assertTrue(budget.allow_model())
            budget.use_model()
        self.assertFalse(budget.allow_model())

    def test_wall_clock_deadline(self) -> None:
        budget = tools.TurnBudget(deadline_seconds=0.0)
        self.assertTrue(budget.timed_out())
        self.assertFalse(budget.allow_tool())
        self.assertFalse(budget.allow_model())

    def test_tool_selection_by_intent(self) -> None:
        self.assertEqual(tools._pick_tool_names("teaching_report"),
                         ["get_teaching_summary"])
        self.assertEqual(tools._pick_tool_names("general_chat"), [])
        names = tools._pick_tool_names("learning_report")
        self.assertIn("get_learning_summary", names)
        self.assertLessEqual(len(names), 4)


class IntentLlmPathTest(unittest.TestCase):
    def _llm(self, payload: str):
        calls = []

        async def call(system: str, user: str) -> str:
            calls.append(user)
            return payload
        return call, calls

    def test_valid_json_parsed(self) -> None:
        llm, calls = self._llm(json.dumps({
            "kind": "learning_report", "module_id": "", "workspace_name": "",
            "time_window": "this_week", "custom_days": 0,
            "confidence": 0.8}))
        parsed = asyncio.run(intent.parse_intent(
            "那这期间表现怎么样呢", lang="zh", llm_call=llm))
        self.assertEqual(parsed.kind, "learning_report")
        self.assertEqual(parsed.confidence, "llm")
        self.assertEqual(parsed.window, "this_week")
        self.assertEqual(len(calls), 1)

    def test_fenced_json_parsed(self) -> None:
        llm, _ = self._llm("```json\n{\"kind\": \"guide\", \"module_id\": "
                           "\"course\", \"confidence\": 0.7}\n```")
        parsed = asyncio.run(intent.parse_intent(
            "嗯这个嘛", lang="zh", llm_call=llm))
        self.assertEqual(parsed.kind, "guide")
        self.assertEqual(parsed.module_route.value, "course")

    def test_garbage_twice_falls_back(self) -> None:
        llm, calls = self._llm("not json at all")
        parsed = asyncio.run(intent.parse_intent(
            "嗯这个嘛", lang="zh", llm_call=llm))
        self.assertIn(parsed.kind, ("guide", "general_chat"))
        self.assertEqual(parsed.confidence, "fallback")
        self.assertEqual(len(calls), 2)  # 一次解析 + 一次受限修复

    def test_kind_outside_closed_set_rejected(self) -> None:
        llm, calls = self._llm(json.dumps({"kind": "delete_everything"}))
        parsed = asyncio.run(intent.parse_intent(
            "嗯这个嘛", lang="zh", llm_call=llm))
        self.assertEqual(parsed.confidence, "fallback")
        self.assertEqual(len(calls), 2)


class ReadersTest(StorageSandboxTestCase):
    """只读适配器：empty≠error、disabled、范围解析与归属校验。"""

    def _make_workspace(self, wid: str, name: str) -> None:
        from app.core import workspace as ws_core
        path = ws_core._WORKSPACES_DIR / f"{wid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "workspace_id": wid, "student_id": self.student_id,
            "name": name, "session_ids": [], "knowledge_files": [],
            "updated_at": 1,
        }, ensure_ascii=False), encoding="utf-8")

    def setUp(self) -> None:
        super().setUp()
        self.student_id = "stu_orch"
        self.registry = readers.SourceRegistry(self.student_id)

    def test_product_help_ready_with_version(self) -> None:
        result = readers.read_product_help(query="怎么备课", lang="zh",
                                           registry=self.registry)
        self.assertEqual(result["status"], "ready")
        self.assertRegex(result["data_revision"], r"^catalog:")
        modules = result["data"]["modules"]
        self.assertTrue(modules)
        self.assertTrue(result["data"]["catalog_version"])

    def test_product_help_empty_for_unknown(self) -> None:
        result = readers.read_product_help(query="量子占卜术", lang="zh")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["data"]["modules"], [])

    def test_scope_resolution_order(self) -> None:
        self._make_workspace("wsp_a", "大学物理")
        self._make_workspace("wsp_b", "初中数学")
        # 文字指定优先且唯一
        scope, cands = readers.resolve_scope(
            self.student_id, {"mode": "follow_page"}, None,
            workspace_name="大学物理")
        self.assertEqual(scope["mode"], "workspace")
        self.assertEqual(scope["workspace_ids"], ["wsp_a"])
        self.assertEqual(cands, [])
        # follow_page + 页面工作区
        scope, _ = readers.resolve_scope(
            self.student_id, {"mode": "follow_page"},
            {"workspace_id": "wsp_b"})
        self.assertEqual(scope["workspace_ids"], ["wsp_b"])
        # 无工作区页面 → 全部工作区
        scope, _ = readers.resolve_scope(
            self.student_id, {"mode": "follow_page"}, None)
        self.assertEqual(scope["mode"], "all_workspaces")
        self.assertEqual(set(scope["workspace_ids"]), {"wsp_a", "wsp_b"})
        # 固定工作区不可访问 → 诚实降级全部
        scope, _ = readers.resolve_scope(
            self.student_id, {"mode": "workspace", "workspace_id": "wsp_x"},
            None)
        self.assertEqual(scope["mode"], "all_workspaces")
        # 多命中同名 → 候选不猜测
        self._make_workspace("wsp_c", "大学物理副本")
        scope, cands = readers.resolve_scope(
            self.student_id, {"mode": "follow_page"}, None,
            workspace_name="大学物理")
        self.assertTrue(len(cands) >= 2)

    def test_scope_excludes_other_users(self) -> None:
        self._make_workspace("wsp_a", "大学物理")
        from app.core import workspace as ws_core
        path = ws_core._WORKSPACES_DIR / "wsp_other.json"
        path.write_text(json.dumps({
            "workspace_id": "wsp_other", "student_id": "stu_else",
            "name": "别人的", "session_ids": [], "knowledge_files": [],
            "updated_at": 1}), encoding="utf-8")
        scope, _ = readers.resolve_scope(
            self.student_id, {"mode": "all_workspaces"}, None)
        self.assertEqual(scope["workspace_ids"], ["wsp_a"])

    def test_learning_summary_empty_not_error(self) -> None:
        start = datetime.now(timezone.utc) - timedelta(days=7)
        end = datetime.now(timezone.utc)
        result = readers.read_learning_summary(
            self.student_id, start_at=start, end_at=end,
            timezone_name="Asia/Shanghai", workspace_ids=None,
            registry=self.registry)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["data"]["recorded_learning_days"], 0)
        self.assertEqual(result["data"]["answers"]["answer_attempt_count"], 0)

    def test_learning_summary_error_on_broken_root(self) -> None:
        from app.agents import activity_aggregator
        start = datetime.now(timezone.utc) - timedelta(days=7)
        end = datetime.now(timezone.utc)
        with mock.patch.object(
                activity_aggregator, "learning_activity_snapshot",
                side_effect=RuntimeError("boom")):
            result = readers.read_learning_summary(
                self.student_id, start_at=start, end_at=end,
                timezone_name="UTC")
        self.assertEqual(result["status"], "error")

    def test_teaching_summary_empty_and_account_scope(self) -> None:
        start = datetime.now(timezone.utc) - timedelta(days=7)
        end = datetime.now(timezone.utc)
        result = readers.read_teaching_summary(
            self.student_id, start_at=start, end_at=end,
            registry=self.registry)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["scope"]["mode"], "account")
        self.assertEqual(result["data"]["total_turns"], 0)

    def test_saved_tasks_read_only_snapshot(self) -> None:
        result = asyncio.run(readers.read_saved_tasks(self.student_id))
        self.assertIn(result["status"], ("ready", "empty"))
        self.assertFalse(result["complete"])
        self.assertFalse(result["data"]["today"])

    def test_find_course_disabled_when_classroom_off(self) -> None:
        result = readers.read_courses(self.student_id)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["notices"][0]["code"], "classroom_disabled")

    def test_destination_matches_workspace_and_module(self) -> None:
        self._make_workspace("wsp_a", "大学物理")
        result = readers.read_destination(
            self.student_id, query="大学物理", lang="zh",
            workspace_name="大学物理", registry=self.registry)
        self.assertEqual(result["status"], "ready")
        kinds = {c["kind"] for c in result["data"]["candidates"]}
        self.assertIn("workspace", kinds)

    def test_destination_lesson_edit_page_entity(self) -> None:
        # 页面实体课程 → 唯一候选，目标带 view=edit（B 阶段课程对齐）。
        self._make_workspace("wsp_a", "大学物理")
        result = readers.read_destination(
            self.student_id, query="帮我编辑这个课件", lang="zh",
            page_context={
                "route_id": "course", "workspace_id": "wsp_a",
                "entity": {"kind": "lesson", "id": "les_edit1"},
            }, registry=self.registry, lesson_edit=True)
        self.assertEqual(result["status"], "ready")
        cands = result["data"]["candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["target"]["kind"], "lesson")
        self.assertEqual(cands[0]["target"]["view"], "edit")
        self.assertEqual(cands[0]["target"]["lesson_id"], "les_edit1")
        self.assertEqual(cands[0]["target"]["workspace_id"], "wsp_a")

    def test_destination_lesson_edit_name_match(self) -> None:
        # 无页面实体：按名称匹配课程（§8.4 名称包含），候选带工作区名。
        from app.core import classroom_store as cstore
        self._make_workspace("wsp_a", "大学物理")
        cstore.update_index(self.student_id, "wsp_a", lambda idx: (
            idx["lessons"].update({
                "les_n1": {
                    "lesson_id": "les_n1", "title": "牛顿第二定律",
                    "updated_at": "2026-09-20T10:00:00",
                },
            })))
        result = readers.read_destination(
            self.student_id, query="编辑牛顿第二定律那节课", lang="zh",
            registry=self.registry, lesson_edit=True)
        self.assertEqual(result["status"], "ready")
        cands = result["data"]["candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["target"]["view"], "edit")
        self.assertEqual(cands[0]["target"]["lesson_id"], "les_n1")
        self.assertEqual(cands[0]["workspace_name"], "大学物理")

    def test_destination_lesson_edit_falls_back_to_module(self) -> None:
        # 无课程候选：回落课程模块入口（用户在课程列表里选择）。
        result = readers.read_destination(
            self.student_id, query="编辑课件", lang="zh",
            registry=self.registry, lesson_edit=True)
        kinds = {c["kind"] for c in result["data"]["candidates"]}
        self.assertIn("module", kinds)

    # -- P1-2/P1-3 回归：具名实体候选与 file 页码 -------------------------

    def _add_note(self, note_id: str, title: str) -> None:
        from app.core import notes as notes_store
        vault = notes_store.load_vault(self.student_id)
        vault.notes.append({
            "id": note_id, "title": title, "content": "", "tags": [],
            "summary": "", "updated_at": 50.0, "revision": 1,
            "created_at": 50.0,
        })
        notes_store.save_vault(vault)

    def test_destination_entity_unique_displaces_weak_module(self) -> None:
        # 「找到我的笔记《X》」：笔记模块名「笔记」只是弱包含命中，
        # 唯一精确命中的具名笔记顶替它 → 单一 note 候选。
        self._add_note("note_d1", "定积分与可积性")
        result = readers.read_destination(
            self.student_id, query="找到我的笔记《定积分与可积性》",
            lang="zh", registry=self.registry)
        self.assertEqual(result["status"], "ready")
        cands = result["data"]["candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], "note")
        self.assertEqual(cands[0]["entity_id"], "note_d1")
        self.assertEqual(cands[0]["target"],
                         {"kind": "note", "note_id": "note_d1"})

    def test_destination_best_tier_unique_wins_over_weak_matches(self) -> None:
        # 笔记《定积分与可积性》exact 命中；课程《定积分》经反向包含
        # 弱 title 命中 —— 最佳档位唯一者顶替，弱命中不稀释唯一性。
        from app.core import classroom_store as cstore
        self._add_note("note_bt1", "定积分与可积性")
        self._make_workspace("wsp_bt", "高等数学")
        cstore.update_index(self.student_id, "wsp_bt", lambda idx: (
            idx["lessons"].update({
                "les_bt": {
                    "lesson_id": "les_bt", "title": "定积分",
                    "updated_at": "2026-09-20T10:00:00",
                },
            })))
        result = readers.read_destination(
            self.student_id, query="找到我的笔记《定积分与可积性》",
            lang="zh", registry=self.registry)
        cands = result["data"]["candidates"]
        self.assertEqual([c["kind"] for c in cands], ["note"])
        self.assertEqual(cands[0]["entity_id"], "note_bt1")

    def test_destination_strong_module_skips_entities(self) -> None:
        # 「带我去记忆中心」：模块别名强命中 → 模糊命中的实体不参与，
        # 防止笔记「记忆中心使用心得」劫持模块导航。
        self._add_note("note_m1", "记忆中心使用心得")
        result = readers.read_destination(
            self.student_id, query="带我去记忆中心", lang="zh",
            registry=self.registry)
        self.assertEqual(result["status"], "ready")
        cands = result["data"]["candidates"]
        kinds = {c["kind"] for c in cands}
        self.assertEqual(kinds, {"module"})
        self.assertEqual(cands[0]["target"]["route_id"], "memory")

    def test_destination_file_target_carries_page(self) -> None:
        import fitz
        from app.core.library import Library, save_library
        with fitz.open() as doc:
            for number in (1, 2):
                doc.new_page().insert_text((30, 50), f"Page {number}")
            raw = doc.tobytes()
        lib = Library(student_id=self.student_id)
        fid = lib.add_file("", "微积分讲义.pdf", "讲义", raw=raw,
                           orig_ext=".pdf")["id"]
        save_library(lib)
        result = readers.read_destination(
            self.student_id, query="打开微积分讲义第3页", lang="zh",
            registry=self.registry, page=3)
        self.assertEqual(result["status"], "ready")
        file_cands = [c for c in result["data"]["candidates"]
                      if c["kind"] == "file"]
        self.assertEqual(len(file_cands), 1)
        self.assertEqual(file_cands[0]["target"]["file_id"], fid)
        self.assertEqual(file_cands[0]["target"]["page"], 3)

    def test_destination_multiple_entities_keep_choices(self) -> None:
        # 两个标题都含「定积分」的笔记 → 不猜测，两个候选都给出。
        self._add_note("note_multi1", "定积分基础")
        self._add_note("note_multi2", "定积分进阶")
        result = readers.read_destination(
            self.student_id, query="打开定积分", lang="zh",
            registry=self.registry)
        cands = result["data"]["candidates"]
        self.assertEqual({c["entity_id"] for c in cands},
                         {"note_multi1", "note_multi2"})
        self.assertFalse(result["data"]["unique"])

    def test_reference_detail_requires_turn_source(self) -> None:
        source_id = self.registry.mint(
            kind="product_help", title="备课上课",
            locator={"kind": "module", "route_id": "course"},
            origin=("product_catalog", "course@1.0.0"))
        ok = readers.read_reference_detail(source_id, self.registry)
        self.assertEqual(ok["status"], "ready")
        self.assertEqual(ok["data"]["title"], "备课上课")
        missing = readers.read_reference_detail("asts_deadbeef",
                                                self.registry)
        self.assertEqual(missing["status"], "error")

    def test_source_registry_entries(self) -> None:
        self.registry.mint(kind="product_help", title="备课",
                           locator={"kind": "module", "route_id": "course"},
                           origin=("product_catalog", "course@1"))
        entries = self.registry.ref_entries("astc_x", "astm_y")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["conversation_id"], "astc_x")
        stripped = self.registry.all_sources()[0]
        self.assertNotIn("_origin", stripped)


class PresentersTest(unittest.TestCase):
    def _learning_result(self, status: str = "ready") -> dict:
        return {
            "tool": "get_learning_summary", "status": status,
            "complete": False,
            "data": {
                "window": {"start_at": "2026-09-21T00:00:00+08:00",
                           "end_at": "2026-09-28T00:00:00+08:00"},
                "recorded_learning_days": 4,
                "answers": {"status": "ready", "answer_attempt_count": 10,
                            "graded_answer_count": 6,
                            "pending_answer_count": 4},
                "teaching_turns": 3,
                "completed_tasks": {"value": None, "known_minimum": 2,
                                    "complete": False},
                "unscoped": {"included": True, "days": 1},
            },
            "notices": [],
            "sources": [],
        }

    def test_learning_report_block_fixed_structure(self) -> None:
        parsed = intent.ParsedIntent(kind="learning_report")
        blocks = presenters.build_blocks(
            parsed, [self._learning_result()],
            {"scope": {"mode": "all_workspaces", "workspace_ids": [],
                       "scope_revisions": {}}},
            lang="zh", window_label="最近7天", timezone_name="Asia/Shanghai",
            now_iso="2026-09-28T00:00:00+00:00")
        reports = [b for b in blocks if b["type"] == "learning_report"]
        self.assertEqual(len(reports), 1)
        report = reports[0]["report"]
        # §11.4 固定字段齐备；数字只来自工具数据。
        for key in ("window", "scope", "facts", "workspace_summaries",
                    "unscoped_activity", "next_steps", "generated_at",
                    "complete", "notices"):
            self.assertIn(key, report)
        items = {i["key"]: i for i in report["facts"]}
        self.assertEqual(items["answer_attempt_count"]["value"], 10)
        self.assertEqual(items["recorded_learning_days"]["value"], 4)
        completed = items["completed_task_count"]
        self.assertIsNone(completed["value"])
        self.assertEqual(completed["known_minimum"], 2)
        self.assertEqual(report["window"]["label"], "最近7天")
        self.assertEqual(report["window"]["timezone"], "Asia/Shanghai")
        self.assertLessEqual(len(report["next_steps"]), 3)
        # 指标块不再与报告块并存（§7.2 固定结构取代散点指标）。
        self.assertFalse([b for b in blocks if b["type"] == "metrics"])

    def test_learning_metrics_only_from_tool_data(self) -> None:
        parsed = intent.ParsedIntent(kind="planning_advice")
        blocks = presenters.build_blocks(
            parsed, [self._learning_result()], {"scope": {}}, lang="zh")
        metrics = [b for b in blocks if b["type"] == "metrics"]
        self.assertEqual(len(metrics), 1)
        items = {i["key"]: i for i in metrics[0]["items"]}
        self.assertEqual(items["answer_attempt_count"]["value"], 10)
        self.assertEqual(items["recorded_learning_days"]["value"], 4)
        completed = items["completed_task_count"]
        self.assertIsNone(completed["value"])
        self.assertEqual(completed["known_minimum"], 2)
        self.assertEqual(completed["completeness"], "partial")

    def test_deterministic_learning_answer_numbers(self) -> None:
        parsed = intent.ParsedIntent(kind="learning_report")
        text = presenters.deterministic_answer(
            parsed, [self._learning_result()], {}, lang="zh",
            window_label="最近7天")
        self.assertIn("4 天", text)
        self.assertIn("10 次", text)
        self.assertIn("至少 2 项", text)

    def test_empty_learning_answer_honest(self) -> None:
        parsed = intent.ParsedIntent(kind="learning_report")
        empty = dict(self._learning_result())
        empty["status"] = "empty"
        empty["data"]["recorded_learning_days"] = 0
        text = presenters.deterministic_answer(
            parsed, [empty], {}, lang="zh")
        self.assertIn("目前没有足够记录", text)

    def test_teaching_answer_suppresses_best_claim_under_5_samples(self) -> None:
        result = {
            "tool": "get_teaching_summary", "status": "ready", "complete": True,
            "data": {"window": {"start_at": "2026-09-21T00:00:00Z",
                                "end_at": "2026-09-28T00:00:00Z"},
                     "total_turns": 12,
                     "top_strategies": [{"name": "direct",
                                         "attempts": 3, "successes": 2,
                                         "sample_count": 3}],
                     "pending_proposals": 2},
            "notices": [], "sources": []}
        parsed = intent.ParsedIntent(kind="teaching_report")
        text = presenters.deterministic_answer(
            parsed, [result], {}, lang="zh", window_label="最近7天")
        self.assertIn("12", text)
        self.assertIn("样本", text)
        self.assertNotIn("最有效", text)

    def test_teaching_report_block_account_scope_and_notices(self) -> None:
        result = {
            "tool": "get_teaching_summary", "status": "ready",
            "complete": True, "generated_at": "2026-09-28T00:00:00+00:00",
            "data": {"window": {"start_at": "2026-09-21T00:00:00+00:00",
                                "end_at": "2026-09-28T00:00:00+00:00",
                                "timezone": "Asia/Shanghai"},
                     "total_turns": 12,
                     "failure_distribution": {"retrieval_miss": 3},
                     "top_strategies": [
                         {"name": "direct", "attempts": 12, "successes": 9,
                          "sample_count": 12},
                         {"name": "analogy", "attempts": 2, "successes": 1,
                          "sample_count": 2}],
                     "proposals": [{"proposal_id": "prop_1",
                                    "title": "缩短讲解长度",
                                    "status": "proposed"}],
                     "pending_proposals": 1,
                     "coverage": {"complete": True, "inspected_count": 40,
                                  "invalid_count": 0,
                                  "earliest_available_at": None}},
            "notices": [], "sources": []}
        parsed = intent.ParsedIntent(kind="teaching_report")
        blocks = presenters.build_blocks(
            parsed, [result], {}, lang="zh", window_label="本周",
            timezone_name="Asia/Shanghai", now_iso="2026-09-28T00:00:00+00:00")
        reports = [b for b in blocks if b["type"] == "teaching_report"]
        self.assertEqual(len(reports), 1)
        report = reports[0]["report"]
        self.assertEqual(report["scope"]["mode"], "account")
        self.assertEqual(report["total_turns"], 12)
        self.assertEqual(report["proposals"][0]["status"], "proposed")
        codes = [n["code"] for n in report["notices"]]
        self.assertIn("small_sample_suppressed", codes)

    def test_choices_block_from_candidates(self) -> None:
        parsed = intent.ParsedIntent(kind="guide")
        dest = {"tool": "resolve_destination", "status": "ready",
                "data": {"candidates": [
                    {"kind": "module", "route_id": "course",
                     "title": "备课上课"},
                    {"kind": "module", "route_id": "chat",
                     "title": "聊天辅导"}]},
                "notices": [], "sources": []}
        blocks = presenters.build_blocks(parsed, [dest], {}, lang="zh")
        choices = [b for b in blocks if b["type"] == "choices"]
        self.assertEqual(len(choices), 1)
        self.assertEqual(len(choices[0]["options"]), 2)

    def test_notice_block_for_truncated_notice(self) -> None:
        parsed = intent.ParsedIntent(kind="planning_advice")
        tasks = {"tool": "get_saved_tasks", "status": "empty", "complete": False,
                 "data": {"today": [], "open": []},
                 "notices": [{"code": "task_history_incomplete",
                              "message": "任务为当前快照。"}],
                 "sources": []}
        blocks = presenters.build_blocks(parsed, [tasks], {}, lang="zh")
        notice = [b for b in blocks if b["type"] == "notice"]
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]["tone"], "warning")


class _OrchestrationApiCase(StorageSandboxTestCase):
    """端到端基座：模型不可用（确定性路径）+ 持久 TestClient。"""

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
        fake_caps.llm_api_key = ""   # 模型不可用：走确定性兜底
        self._caps_patch = caps_patch
        self.app = create_app()
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="orch@example.com", username="",
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

    def _create_conversation(self) -> tuple[str, int]:
        resp = self.client.post(
            "/api/v1/assistant/conversations",
            json={"client_request_id": str(uuid.uuid4())},
            headers=self.auth)
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        return body["conversation_id"], body["revision"]

    def _post_turn(self, cid: str, text: str, revision: int):
        return self.client.post(
            f"/api/v1/assistant/conversations/{cid}/turns",
            json={"schema_version": 1,
                  "client_message_id": str(uuid.uuid4()),
                  "expected_conversation_revision": revision,
                  "text": text, "lang": "zh",
                  "timezone": "Asia/Shanghai",
                  "scope": {"mode": "follow_page"},
                  "page_context": {"schema_version": 1,
                                   "route_id": "dashboard",
                                   "route_epoch": 1}},
            headers=self.auth)

    def _wait_turn_terminal(self, turn_id: str, timeout: float = 5.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            resp = self.client.get(f"/api/v1/assistant/turns/{turn_id}",
                                   headers=self.auth)
            self.assertEqual(resp.status_code, 200, resp.text)
            state = resp.json().get("state")
            if state in ("completed", "cancelled", "failed", "interrupted"):
                return resp.json()
            time.sleep(0.05)
        self.fail("turn did not reach terminal state in time")


class ServiceTurnTest(_OrchestrationApiCase):
    def test_learning_report_turn_deterministic(self) -> None:
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "最近一周我学得怎么样？", revision)
        self.assertEqual(resp.status_code, 202, resp.text)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed", json.dumps(
            snap.get("assistant_message"), ensure_ascii=False))
        message = snap["assistant_message"]
        self.assertEqual(message["status"], "complete")
        kinds = [b["type"] for b in message["blocks"]]
        self.assertEqual(kinds[0], "markdown")
        # 空数据也出固定结构报告卡（§7.2），但 facts 全部为零/空。
        self.assertIn("learning_report", kinds)
        self.assertNotIn("metrics", kinds)
        text = message["blocks"][0]["text"]
        self.assertIn("目前没有足够记录", text)
        # 契约校验：持久化的消息必须通过 §11.4 schema。
        import pydantic
        from app.schemas import assistant as sc
        adapter = pydantic.TypeAdapter(sc.AssistantMessage)
        for role in ("user_message", "assistant_message"):
            adapter.validate_python(snap[role])
        # 消息记录解析后的范围（全部工作区、无本人工作区）
        self.assertEqual(message["scope"]["mode"], "all_workspaces")
        # 快路无模型调用 → 状态事件含 reading/composing
        self.assertTrue(message["blocks"])

    def test_guide_turn_module_answer(self) -> None:
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "带我去备课上课。", revision)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed")
        text = snap["assistant_message"]["blocks"][0]["text"]
        module = catalog.get_module(catalog.AssistantRouteId.COURSE)
        self.assertIn(module.name.zh, text)

    def test_teaching_report_turn(self) -> None:
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "有没有AI教学评价？", revision)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed")
        text = snap["assistant_message"]["blocks"][0]["text"]
        self.assertIn("教学", text)

    def test_sources_registered_with_workspace_scope(self) -> None:
        from app.core import workspace as ws_core
        path = ws_core._WORKSPACES_DIR / "wsp_orch.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "workspace_id": "wsp_orch", "student_id": self.sid,
            "name": "大学物理", "session_ids": [], "knowledge_files": [],
            "updated_at": 1}, ensure_ascii=False), encoding="utf-8")
        cid, revision = self._create_conversation()
        resp = self.client.post(
            f"/api/v1/assistant/conversations/{cid}/turns",
            json={"schema_version": 1,
                  "client_message_id": str(uuid.uuid4()),
                  "expected_conversation_revision": revision,
                  "text": "最近一周我学得怎么样？", "lang": "zh",
                  "timezone": "Asia/Shanghai",
                  "scope": {"mode": "workspace", "workspace_id": "wsp_orch"},
                  "page_context": {"schema_version": 1,
                                   "route_id": "chat", "route_epoch": 1,
                                   "workspace_id": "wsp_orch"}},
            headers=self.auth)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed")
        message = snap["assistant_message"]
        self.assertEqual(message["scope"]["mode"], "workspace")
        self.assertEqual(message["scope"]["workspace_ids"], ["wsp_orch"])
        self.assertTrue(message["sources"])
        self.assertTrue(all(s["source_id"].startswith("asts_")
                            for s in message["sources"]))
        # 反向索引登记（§12.3-7）
        refs = readers.store.lookup_origin_refs(
            self.sid, "learning_evidence_journal", "wsp_orch")
        self.assertTrue(refs)

    def test_choice_selects_module(self) -> None:
        cid, revision = self._create_conversation()
        resp = self.client.post(
            f"/api/v1/assistant/conversations/{cid}/turns",
            json={"schema_version": 1,
                  "client_message_id": str(uuid.uuid4()),
                  "expected_conversation_revision": revision,
                  "text": "course", "lang": "zh",
                  "timezone": "Asia/Shanghai",
                  "scope": {"mode": "follow_page"},
                  "page_context": {"schema_version": 1,
                                   "route_id": "dashboard", "route_epoch": 1},
                  "choice": {"block_id": "b2", "option_id": "course"}},
            headers=self.auth)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed")
        text = snap["assistant_message"]["blocks"][0]["text"]
        module = catalog.get_module(catalog.AssistantRouteId.COURSE)
        self.assertIn(module.name.zh, text)

    def test_model_budget_respected(self) -> None:
        budget = tools.TurnBudget()
        for _ in range(2):
            budget.use_model()
        from app.agents.site_assistant import service
        llm = service._make_llm_call(budget)

        async def drive() -> str:
            with self.assertRaises(RuntimeError):
                await llm("sys", "user")
            return "ok"
        self.assertEqual(asyncio.run(drive()), "ok")

    # -- P1-1/P1-2/P1-3 回归：自然语言实体轮次（快路、无模型）------------

    def _seed_note(self, note_id: str, title: str) -> None:
        from app.core import notes as notes_store
        vault = notes_store.load_vault(self.sid)
        vault.notes.append({
            "id": note_id, "title": title, "content": "", "tags": [],
            "summary": "", "updated_at": 50.0, "revision": 1,
            "created_at": 50.0,
        })
        notes_store.save_vault(vault)

    def test_search_note_turn_yields_entity_card_and_sources(self) -> None:
        # 「找到我的笔记《X》」→ user_click 实体深链卡 + 可点击来源。
        self._seed_note("note_turn1", "定积分与可积性")
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "找到我的笔记《定积分与可积性》",
                               revision)
        self.assertEqual(resp.status_code, 202, resp.text)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed", json.dumps(
            snap.get("assistant_message"), ensure_ascii=False))
        message = snap["assistant_message"]
        actions_blocks = [b for b in message["blocks"]
                          if b["type"] == "actions"]
        self.assertEqual(len(actions_blocks), 1)
        action = actions_blocks[0]["items"][0]
        self.assertEqual(action["payload"]["kind"], "navigate")
        self.assertEqual(action["payload"]["target"],
                         {"kind": "note", "note_id": "note_turn1"})
        self.assertEqual(action["execution"], "user_click")
        self.assertIn("定积分与可积性", action["label"])
        sources = message.get("sources") or []
        self.assertTrue(any(s["kind"] == "site_search"
                            and s["locator"].get("kind") == "note"
                            and s["locator"].get("note_id") == "note_turn1"
                            for s in sources),
                        msg=json.dumps(sources, ensure_ascii=False))
        # 契约校验：实体深链动作与 site_search 来源必须通过 schema。
        import pydantic
        from app.schemas import assistant as sc
        pydantic.TypeAdapter(sc.AssistantMessage).validate_python(message)

    def test_open_note_turn_yields_automatic_entity_action(self) -> None:
        # 「打开我的笔记《X》」：明确动词 + 唯一实体 → automatic 深链卡。
        self._seed_note("note_turn2", "定积分与可积性")
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "打开我的笔记《定积分与可积性》",
                               revision)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed", json.dumps(
            snap.get("assistant_message"), ensure_ascii=False))
        message = snap["assistant_message"]
        actions_blocks = [b for b in message["blocks"]
                          if b["type"] == "actions"]
        self.assertEqual(len(actions_blocks), 1)
        action = actions_blocks[0]["items"][0]
        self.assertEqual(action["execution"], "automatic")
        self.assertEqual(action["payload"]["target"],
                         {"kind": "note", "note_id": "note_turn2"})

    def test_open_file_page_turn_yields_page_target(self) -> None:
        # 「打开微积分讲义第3页」→ file 深链目标携带 page=3（NAV-03
        # 生产路径：页码由后端确定性产出）。
        import fitz
        from app.core.library import Library, save_library
        with fitz.open() as doc:
            for number in (1, 2, 3):
                doc.new_page().insert_text((30, 50), f"Page {number}")
            raw = doc.tobytes()
        lib = Library(student_id=self.sid)
        fid = lib.add_file("", "微积分讲义.pdf", "讲义", raw=raw,
                           orig_ext=".pdf")["id"]
        save_library(lib)
        cid, revision = self._create_conversation()
        resp = self._post_turn(cid, "打开微积分讲义第3页", revision)
        snap = self._wait_turn_terminal(resp.json()["turn_id"])
        self.assertEqual(snap["state"], "completed", json.dumps(
            snap.get("assistant_message"), ensure_ascii=False))
        message = snap["assistant_message"]
        actions_blocks = [b for b in message["blocks"]
                          if b["type"] == "actions"]
        self.assertEqual(len(actions_blocks), 1, json.dumps(
            message["blocks"], ensure_ascii=False))
        action = actions_blocks[0]["items"][0]
        self.assertEqual(action["execution"], "automatic")
        target = action["payload"]["target"]
        self.assertEqual(target["kind"], "file")
        self.assertEqual(target["file_id"], fid)
        self.assertEqual(target["page"], 3)


if __name__ == "__main__":
    unittest.main()
