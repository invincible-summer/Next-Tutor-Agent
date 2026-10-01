"""§23.1 六个流程模板（C02）：步骤编排与读步骤执行。

模板只产出登记过的操作（§21.3 白名单 + 只读工具）；输出绑定由模板
代码完成（selected_materials→workspace.create.file_ids 等），不接受
动态表达式。来源/目的/工作区必须确定到可执行 ID，缺失时步骤进入
waiting（paused_for_user 由引擎决定），其余只读准备可先行。
"""
from __future__ import annotations

from typing import Any

TEMPLATES = (
    "setup_learning_space", "weekly_review_to_plan",
    "weak_point_to_practice", "material_to_course",
    "organize_materials", "continue_learning_session",
)


def _step(step_id: str, title: str, kind: str, operation: str,
          depends_on: list[str] | None = None,
          input_data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "step_id": step_id, "title": title, "kind": kind,
        "depends_on": depends_on or [], "state": "pending",
        "operation": operation, "input": input_data or {},
        "action_id": None, "domain_job": None, "output_ref": None,
        "error": None,
    }


def build_steps(student_id: str, template: str, objective: str,
                scope: dict[str, Any],
                selection_ids: dict[str, list[str]]) -> list[dict[str, Any]]:
    """按模板构造步骤（写步骤输入在此绑定真实实体 ID）。"""
    if template == "setup_learning_space":
        file_ids = [str(f) for f in (selection_ids.get("file_ids")
                                     or scope.get("file_ids") or [])]
        name = str(selection_ids.get("workspace_name")
                   or scope.get("workspace_name") or "我的学习区")
        steps = [
            _step("step_01", "确认教材来源", "read", "check_sources",
                  input_data={"file_ids": file_ids}),
            _step("step_02", f"创建学习区「{name}」", "write",
                  "workspace.create", depends_on=["step_01"],
                  input_data={"name": name, "file_ids": file_ids}),
            _step("step_03", "创建学习目标并重排计划", "write",
                  "goal.create", depends_on=["step_02"],
                  input_data={"title": objective[:60]}),
            _step("step_04", "准备首次辅导入口", "handoff",
                  "open_chat", depends_on=["step_02"]),
        ]
        return steps
    if template == "weekly_review_to_plan":
        return [
            _step("step_01", "读取本周学习记录", "read",
                  "learning_snapshot", input_data={"window": "this_week"}),
            _step("step_02", "生成本周复盘与计划建议", "prepare",
                  "review_summary", depends_on=["step_01"]),
            _step("step_03", "保存你选定的任务安排", "write",
                  "plan.regenerate", depends_on=["step_02"],
                  input_data={"expected_plan_revision":
                              scope.get("plan_revision") or "0:0"}),
        ]
    if template == "weak_point_to_practice":
        concept_keys = [str(c) for c in (selection_ids.get("concept_keys")
                                         or [])[:1]]
        return [
            _step("step_01", "读取当前学习评价", "read",
                  "evaluation_view", input_data={"scope": scope}),
            _step("step_02", "预览测评参数", "prepare",
                  "assessment_params", depends_on=["step_01"],
                  input_data={"concept_keys": concept_keys}),
            _step("step_03", "创建练习测评", "write", "assessment.start",
                  depends_on=["step_02"],
                  input_data={"workspace_id":
                              str(scope.get("workspace_id") or ""),
                              "concept_keys": concept_keys or ["未指定"],
                              "count": 5}),
            _step("step_04", "交接到测评作答", "handoff",
                  "open_assessment", depends_on=["step_03"]),
        ]
    if template == "material_to_course":
        file_ids = [str(f) for f in (selection_ids.get("file_ids")
                                     or scope.get("file_ids") or [])]
        workspace_id = str(scope.get("workspace_id")
                           or selection_ids.get("workspace_id") or "")
        topic = str(selection_ids.get("topic") or objective[:60])
        return [
            _step("step_01", "校验教材来源范围", "read", "check_sources",
                  input_data={"file_ids": file_ids}),
            _step("step_02", f"生成课件「{topic}」", "write",
                  "lesson.generate", depends_on=["step_01"],
                  input_data={"workspace_id": workspace_id,
                              "topic": topic,
                              "source_file_ids": file_ids,
                              "start_mode":
                                  str(scope.get("start_mode")
                                      or "outline_first")}),
            _step("step_03", "等待课件生成完成", "wait_job",
                  "lesson.wait", depends_on=["step_02"]),
            _step("step_04", "在课程介绍页展示产物", "handoff",
                  "open_course", depends_on=["step_03"]),
        ]
    if template == "organize_materials":
        file_ids = [str(f) for f in (selection_ids.get("file_ids") or [])]
        folder = str(selection_ids.get("folder_name") or "整理资料")
        return [
            _step("step_01", "检查选中的文件", "read", "check_selection",
                  input_data={"file_ids": file_ids}),
            _step("step_02", f"创建资料文件夹「{folder}」", "write",
                  "library.create_folder", depends_on=["step_01"],
                  input_data={"name": folder}),
            _step("step_03", "生成摘要笔记草稿", "handoff",
                  "handoff_note", depends_on=["step_01"],
                  input_data={"title": f"{folder}摘要"}),
        ]
    if template == "continue_learning_session":
        return [
            _step("step_01", "查找最近的学习记录", "read",
                  "recent_activity", input_data={"limit": 10}),
            _step("step_02", "恢复原学习位置", "handoff", "resume_module",
                  depends_on=["step_01"]),
        ]
    return []


def describe_plan(student_id: str, wf: dict[str, Any]) -> dict[str, Any]:
    """§23.3 预览正文：将创建/修改的对象与用户参与点。"""
    steps = wf.get("steps") or []
    writes = [s for s in steps if s.get("kind") == "write"]
    return {
        "objective": wf.get("objective"),
        "scope": wf.get("scope"),
        "will_create_or_modify": [
            {"step_id": s.get("step_id"), "operation": s.get("operation"),
             "input": s.get("input")}
            for s in writes],
        "user_involvement": [
            {"step_id": s.get("step_id"), "title": s.get("title")}
            for s in steps if s.get("kind") in ("prepare", "handoff")],
        "cancellable": True,
    }


def run_read_step(student_id: str, wf: dict[str, Any],
                  step: dict[str, Any]) -> dict[str, Any]:
    """只读/准备步骤：确定性检查与事实读取（不写业务）。"""
    op = str(step.get("operation") or "")
    data = dict(step.get("input") or {})
    if op == "check_sources":
        from .previews import _textbook_names
        file_ids = [str(f) for f in (data.get("file_ids") or [])]
        names = _textbook_names(student_id, file_ids) if file_ids else {}
        missing = [f for f in file_ids if f not in names]
        if file_ids and missing:
            from .workflows import WorkflowRejected
            raise WorkflowRejected(
                404, "entity_not_found",
                f"来源不可用：{','.join(missing[:3])}")
        return {"ok": True, "sources": names}
    if op == "check_selection":
        from app.core.library import load_library
        lib = load_library(student_id)
        found = {}
        for fid in (data.get("file_ids") or []):
            meta = lib.find_file(str(fid))
            if meta is None:
                from .workflows import WorkflowRejected
                raise WorkflowRejected(404, "entity_not_found",
                                       f"文件不存在：{fid}")
            found[str(fid)] = str(meta.get("filename") or fid)
        return {"ok": True, "selection": found}
    if op == "learning_snapshot":
        from app.agents.activity_aggregator import learning_activity_snapshot
        from datetime import datetime, timedelta, timezone
        now = datetime.now(tz=timezone.utc)
        snap = learning_activity_snapshot(
            student_id, start_at=now - timedelta(days=7), end_at=now,
            timezone="UTC")
        return {"ok": True,
                "completed_tasks": snap.get("completed_tasks"),
                "days": snap.get("recorded_learning_days")}
    if op == "evaluation_view":
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        return {"ok": True, "sources": len(state.sources),
                "concepts": len(state.coverage or {})}
    if op == "recent_activity":
        # 最近有意义的学习记录：未完成课堂 run 优先，其次最近辅导会话
        #（真实业务记录，不读助手自身会话）。
        from . import readers
        courses = readers.read_courses(student_id, resume_only=True)
        resume = ((courses.get("data") or {}).get("resume")
                  if courses.get("status") == "ready" else None)
        if resume and resume.get("lesson_id"):
            run = resume.get("run") or {}
            return {"ok": True, "resume": {
                "workspace_id": str(resume.get("workspace_id") or ""),
                "lesson_id": str(resume.get("lesson_id") or ""),
                "title": str(resume.get("title") or ""),
                "run_id": str(run.get("run_id") or "")}}
        from app.core.session import list_sessions
        sessions = [s for s in list_sessions()
                    if s.get("student_id") in ("", None, student_id)][:1]
        if sessions:
            return {"ok": True, "chat_session": {
                "session_id": str(sessions[0].get("session_id") or "")}}
        return {"ok": True}
    if op in ("review_summary", "assessment_params"):
        return {"ok": True, "prepared": op}
    return {"ok": True}


def run_handoff_step(student_id: str, wf: dict[str, Any],
                     step: dict[str, Any]) -> dict[str, Any]:
    """交接步骤：给任务中心/原页面的落点（不自动开课/不自动发送）。"""
    op = str(step.get("operation") or "")
    steps = {str(s.get("step_id")): s for s in wf.get("steps") or []}
    if op == "open_chat":
        return {"navigation_target": {"kind": "module",
                                      "route_id": "chat"}}
    if op == "open_assessment":
        return {"navigation_target": {"kind": "module",
                                      "route_id": "assessment"}}
    if op == "open_course":
        gen = steps.get("step_02", {}).get("domain_job") or {}
        workspace_id = str(gen.get("workspace_id")
                           or (steps.get("step_02", {}).get("input")
                               or {}).get("workspace_id") or "")
        lesson_id = str(gen.get("lesson_id") or "")
        if workspace_id and lesson_id:
            # 课程介绍页（§8.2 lesson 目标；产物在介绍页呈现，不自动开课）。
            return {"navigation_target": {
                "kind": "lesson", "workspace_id": workspace_id,
                "lesson_id": lesson_id}}
        return {"ok": True}
    if op == "resume_module":
        read = steps.get("step_01", {}).get("output_ref") or {}
        resume = read.get("resume") or {}
        if resume.get("workspace_id") and resume.get("lesson_id"):
            target: dict[str, Any] = {
                "kind": "lesson",
                "workspace_id": str(resume["workspace_id"]),
                "lesson_id": str(resume["lesson_id"])}
            if resume.get("run_id"):
                # 播放页初始为选页预览态（§5.3-3），全屏开课由用户操作。
                target = {"kind": "classroom_run",
                          "workspace_id": str(resume["workspace_id"]),
                          "lesson_id": str(resume["lesson_id"]),
                          "run_id": str(resume["run_id"])}
            return {"navigation_target": target}
        chat = read.get("chat_session") or {}
        if chat.get("session_id"):
            return {"navigation_target": {
                "kind": "chat_session",
                "session_id": str(chat["session_id"])}}
        return {"navigation_target": {"kind": "module",
                                      "route_id": "dashboard"}}
    if op == "handoff_note":
        return {"navigation_target": {"kind": "module",
                                      "route_id": "notes"}}
    return {"ok": True}
