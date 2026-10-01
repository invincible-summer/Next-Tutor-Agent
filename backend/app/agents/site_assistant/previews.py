"""领域写动作：预览、审批许可与执行分发（plan.md §21）。

预览的参数 hash 与审批绑定 owner/action/parameter_hash/source_revisions，
10 分钟有效；review_required 的 operation 必须持当前有效许可才可 execute。
B03 建立协议与首批操作；B05 补齐聊天/资料/归档 10 操作（§21.3），创建类
操作带 §21.6.1 的 client_request_id 幂等（去重标记与创建共用同一原子写）。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings

from . import actions as actions_svc
from .actions import ActionRejected, _save

APPROVAL_TTL_SECONDS = 600.0

# §21.2 三种执行策略；未列出的 operation 尚未实现（拒绝）。
# 静态表之外由 policy_for() 按输入动态升级（如 update_sources 的移除）。
OPERATION_POLICY: dict[str, str] = {
    "task.create": "intent_sufficient",
    "task.complete": "intent_sufficient",
    "note.create": "review_required",       # AI 生成正文先预览（§21.2）
    "chat.rename": "intent_sufficient",
    "schedule.update": "intent_sufficient",
    # -- B05（§21.3）------------------------------------------------------
    # 助手提案只来自用户明确文字（policy 确定性抽取），不猜资料组合；
    # update_sources 含移除时由 policy_for 升级为 review_required。
    "workspace.create": "intent_sufficient",
    "workspace.update_sources": "intent_sufficient",
    "chat.move_workspace": "intent_sufficient",
    "chat.archive": "intent_sufficient",    # 可恢复：进入归档而非删除
    "library.create_folder": "intent_sufficient",
    "library.rename_file": "intent_sufficient",
    "library.move_file": "intent_sufficient",
    "textbook.cancel": "intent_sufficient",  # 合作式取消、保留已有数据
    "textbook.rebuild": "review_required",   # 消耗后台资源的重建（§21.2）
    "archive.restore": "intent_sufficient",  # 已有记录恢复（§21.2）
    # -- B06（§21.3 笔记/学习编排） ----------------------------------------
    # note.append 只附加以精确 base_revision 守并发；note.replace 必须差异
    # 预览；note.move 单项直接、多项由 policy_for 升级 review_required。
    "note.append": "intent_sufficient",
    "note.replace": "review_required",
    "note.move": "intent_sufficient",
    "note.set_review": "intent_sufficient",   # 只改开关，不生成复习记录
    "note.restore_revision": "review_required",
    "goal.create": "review_required",         # 创建引发规划，先预览说明
    "goal.update": "review_required",         # 展示受影响周计划
    "plan.regenerate": "review_required",     # 强制预览候选差异（§21.6.3）
    "task.update": "intent_sufficient",       # 多字段由 policy_for 升级
    "subtask.create": "intent_sufficient",
    # -- B07（§21.3 测评/评价/教学指导） ------------------------------------
    # 测评启动/练习消耗生成资源；复核与综合评价绑定用户理由/范围版本；
    # 教学指导批准/应用/撤销各为独立确认（不能一次批准即应用）。
    "assessment.start": "review_required",
    "assessment.practice": "review_required",
    "evaluation.request_review": "review_required",
    "evaluation.retry": "intent_sufficient",   # 仅 failed 可重试，域内校验
    "evaluation.synthesize": "review_required",
    "teaching.approve": "review_required",
    "teaching.apply": "review_required",
    "teaching.revoke": "review_required",
    # -- B09（§21.3 画像/记忆/助手偏好） --------------------------------------
    # 用户明确给出的偏好/画像字段可直接写；改学段影响讲解基线，先预览。
    "memory.set_window": "intent_sufficient",
    "profile.update": "review_required",
    "assistant.preferences": "intent_sufficient",
    # -- B10（§21.3 课程高级动作） --------------------------------------------
    # 直接生成消耗任务预算（review_required）；取消/重试/导出是明确的
    # 生命周期操作。全部复用现有领域 job；构图不由助手参数化。
    "lesson.generate": "review_required",
    "lesson.retry": "intent_sufficient",
    "lesson.cancel": "intent_sufficient",
    "lesson.export": "intent_sufficient",
}

REVERSIBLE: dict[str, bool] = {
    "task.create": True,
    "task.complete": False,
    "note.create": True,
    "chat.rename": True,
    "schedule.update": True,
    "workspace.create": True,
    "workspace.update_sources": True,
    "chat.move_workspace": True,
    "chat.archive": True,                   # 归档可经 trash.restore 恢复
    "library.create_folder": True,
    "library.rename_file": True,
    "library.move_file": True,
    "textbook.cancel": False,               # 已停止的计算不自动续跑
    "textbook.rebuild": False,              # 已消耗的后台资源不可撤销
    "archive.restore": True,                # 可重新归档（生成新归档条目）
    # B06：goal/plan 写操作引发重规划，不在助手内整体撤销。
    "note.append": True,                    # 撤销 = 恢复 base_revision 版本
    "note.replace": True,                   # 同上；版本历史保留
    "note.move": True,
    "note.set_review": True,
    "note.restore_revision": True,          # 撤销 = 恢复恢复前当前版本
    "goal.create": False,
    "goal.update": False,
    "plan.regenerate": False,               # 旧周计划已被合并替换
    "task.update": True,
    "subtask.create": True,
    # B07：测评/评价/教学指导按原模块生命周期处理（§21.5）。
    "assessment.start": False,
    "assessment.practice": False,
    "evaluation.request_review": False,
    "evaluation.retry": False,
    "evaluation.synthesize": False,
    "teaching.approve": False,
    "teaching.apply": False,
    "teaching.revoke": False,
    # B09：偏好/画像写入保存前值，可反向恢复（§21.5）。
    "memory.set_window": True,
    "profile.update": True,
    "assistant.preferences": True,
    # B10：生成/重试消耗预算，取消改变 job 状态，导出产物可过期重建。
    "lesson.generate": False,
    "lesson.retry": False,
    "lesson.cancel": False,
    "lesson.export": False,
}


def actions_enabled() -> bool:
    """§26.5 SITE_ASSISTANT_ACTIONS_ENABLED：领域写入与预览许可总开关。

    助手总开关（SITE_ASSISTANT_ENABLED）由 API 路由层把关，这里只看
    动作开关本身，保证单测/内部调用不重复外层门控。
    """
    return bool(settings.site_assistant_actions_enabled)


def policy_for(operation: str, data: dict[str, Any]) -> str:
    """操作执行策略（含按输入的动态升级）；policy.py 提案层共用此函数。"""
    if operation == "workspace.update_sources":
        # §21.3：移除/替换需预览，不能覆盖并发新增；纯新增可直接执行。
        if (data.get("remove_file_ids") or []):
            return "review_required"
    if operation == "note.move" and len(data.get("note_ids") or []) > 1:
        # §21.3：多项移动列完整目标确认。
        return "review_required"
    if operation == "task.update":
        patched = sum(1 for key in ("title", "day", "kind", "phase",
                                    "estimate_minutes", "priority",
                                    "milestone_id")
                      if data.get(key) is not None)
        if patched > 1:
            # §21.3：多字段修改先预览。
            return "review_required"
    return OPERATION_POLICY.get(operation, "")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def parameter_hash(operation: str, payload_input: dict[str, Any]) -> str:
    return "ph_" + hashlib.sha256(
        _canonical({"op": operation, "input": payload_input})
        .encode("utf-8")).hexdigest()[:48]


def _preview_doc(action: dict[str, Any]) -> dict[str, Any] | None:
    return (action.get("preview") or None)


async def prepare_preview(student_id: str, action_id: str, *,
                          is_admin: bool = False) -> dict[str, Any]:
    """GET preview 的异步入口（路由层使用）。

    plan.regenerate 的预览需要先生成候选（§21.6.3 阶段一，含一次模型
    调用）；其余操作直接走确定性 build_preview。同状态重复预览命中
    同一候选（candidate_id 为内容哈希），不会重复消耗模型预算。
    """
    located = actions_svc.locate_action(student_id, action_id)
    if located is not None:
        _record, _cid, action = located
        payload = action.get("payload") or {}
        if (payload.get("kind") == "domain_write"
                and payload.get("operation") == "plan.regenerate"):
            input_data = payload.get("input") or {}
            state_hint = str(input_data.get("expected_plan_revision") or "")
            if state_hint:
                from app.agents.learning_orchestration.manager import (
                    get_orchestration_service)
                try:
                    await get_orchestration_service().build_plan_candidate(
                        student_id,
                        num_weeks=int(input_data.get("num_weeks") or 4))
                except ValueError:
                    pass  # no_goal：build_preview 以 404 拒绝
    import asyncio
    return await asyncio.to_thread(
        build_preview, student_id, action_id, is_admin=is_admin)


def build_preview(student_id: str, action_id: str, *,
                  is_admin: bool = False) -> dict[str, Any]:
    """GET /actions/{aid}/preview：确定性构造 ActionPreview（§21.4）。"""
    if not actions_enabled():
        raise ActionRejected(503, "capability_disabled",
                             "领域写动作当前未开放。")
    located = actions_svc.locate_action(student_id, action_id)
    if located is None:
        raise ActionRejected(404, "entity_not_found", "该动作不存在。")
    record, _cid, action = located
    payload = action.get("payload") or {}
    if payload.get("kind") != "domain_write":
        raise ActionRejected(422, "invalid_target",
                             "该动作不提供领域写预览。")
    operation = str(payload.get("operation") or "")
    policy = policy_for(operation, payload.get("input") or {})
    if policy == "":
        raise ActionRejected(503, "capability_disabled",
                             "该操作尚未开放。")
    existing = _preview_doc(action)
    input_data = payload.get("input") or {}
    ph = parameter_hash(operation, input_data)
    content = _preview_content(student_id, operation, input_data,
                               is_admin=is_admin)
    revisions = content["source_revisions"]
    if (existing and existing.get("parameter_hash") == ph
            and existing.get("source_revisions") == revisions):
        return existing  # 同参数且业务版本未变：幂等返回同一预览
    preview = {
        "preview_id": "apv_" + secrets.token_hex(12),
        "action_id": action_id,
        "title": action.get("label") or operation,
        "summary": content["summary"],
        "affected_entities": content["affected_entities"],
        "changes": content["changes"],
        "side_effects": content["side_effects"],
        "reversible": REVERSIBLE.get(operation, False),
        "parameter_hash": ph,
        "source_revisions": revisions,
        "expires_at": (datetime.now(tz=timezone.utc)
                       + timedelta(seconds=APPROVAL_TTL_SECONDS)).isoformat(),
        "approval": policy,
    }
    if content.get("candidate_id"):
        # §21.6.3：plan.regenerate 预览绑定具体候选；提交逐字应用同一候选。
        preview["candidate_id"] = str(content["candidate_id"])
    action["preview"] = preview
    _save(record, student_id)
    return preview


# -- 领域读取小工具（预览 before 值 / 归属复核共用） --------------------------

def _owned_workspace(student_id: str, ws_id: str):
    """加载本人工作区；不存在/非本人 404（与 /workspaces 路由同一语义）。"""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core.workspace import load_workspace
    ws = load_workspace(str(ws_id))
    if ws is None or (ws.student_id or DEFAULT_STUDENT_ID) != student_id:
        raise ActionRejected(404, "entity_not_found", "辅导区不存在。")
    return ws


def _owned_session(student_id: str, session_id: str):
    """加载本人会话；不存在/非本人 404。"""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core.session import load_session
    session = load_session(str(session_id))
    if session is None or (session.student_id or DEFAULT_STUDENT_ID) \
            != student_id:
        raise ActionRejected(404, "entity_not_found", "会话不存在。")
    return session


def _owned_textbook(student_id: str, textbook_id: str, *,
                    is_admin: bool) -> tuple[dict, str]:
    """自有优先、公用兜底；公用写操作需 admin（与 /textbooks 路由一致）。"""
    from app.core import textbook as tb_store
    found = tb_store.find_textbook_scoped(student_id, str(textbook_id))
    if found is None:
        raise ActionRejected(404, "entity_not_found", "教材不存在。")
    tb, owner_sid = found
    if owner_sid == tb_store.PUBLIC_STUDENT_ID and not is_admin:
        raise ActionRejected(403, "forbidden", "公用教材仅管理员可操作。")
    return tb, owner_sid


def _owned_note(student_id: str, note_id: str):
    """加载本人笔记元数据；不存在 404（笔记无跨用户共享）。"""
    from app.core import notes as notes_store
    meta = notes_store.load_vault(student_id).find_note(str(note_id))
    if meta is None:
        raise ActionRejected(404, "entity_not_found", "笔记不存在。")
    return meta


def _owned_task(student_id: str, task_id: str):
    """加载本人学习编排日任务；不存在 404。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    task = next((t for t in get_orchestration_service()._load(
        student_id).daily_tasks if t.id == str(task_id)), None)
    if task is None:
        raise ActionRejected(404, "entity_not_found", "任务不存在。")
    return task


def _latest_plan_candidate(state) -> dict[str, Any] | None:
    """最新的未提交重规划候选（§21.6.3 阶段一产物）。"""
    items = list((state.plan_candidates or {}).items())
    if not items:
        return None
    cand_id, cand = max(
        items, key=lambda kv: float(kv[1].get("created_at") or 0.0))
    return {**cand, "candidate_id": cand_id}


def _eval_journal_state(student_id: str):
    """学生模型评价 journal 状态（测评题库 / 复核 / 作业同源）。"""
    from app.agents.student_model.evaluation.store import get_journal
    return get_journal(student_id).state()


def _resolve_eval_scope(student_id: str, workspace_id: str):
    """解析评价范围；不存在 404（与 /learner-evaluation 同一语义）。"""
    from app.agents.student_model.evaluation.scope import (
        ScopeNotFound, get_scope_resolver)
    try:
        return get_scope_resolver().resolve(student_id, workspace_id)
    except ScopeNotFound:
        raise ActionRejected(404, "entity_not_found", "工作区不存在。")


def _http_to_rejected(exc) -> ActionRejected:
    """领域路由复用时把 fastapi HTTPException 翻译为 ActionRejected。"""
    detail = getattr(exc, "detail", "") or ""
    if isinstance(detail, dict):
        detail = str(detail.get("message") or detail.get("detail") or "")
    return ActionRejected(int(getattr(exc, "status_code", 503)),
                          "invalid_target", str(detail)[:120] or "操作失败。")


def _textbook_names(student_id: str, file_ids: list[str]) -> dict[str, str]:
    """file_id → 展示名（自有或公用教材）；解析不到的 id 不出现在结果。"""
    from app.core.workspace import resolve_textbook_file
    names: dict[str, str] = {}
    for fid in file_ids:
        meta, _owner = resolve_textbook_file(student_id, str(fid))
        if meta is not None:
            names[str(fid)] = str(meta.get("filename") or fid)
    return names


def _clip(text: str, limit: int = 500) -> str:
    return text[:limit]


def _names_label(names: dict[str, str], file_ids: list[str]) -> str:
    parts = [names.get(str(f), str(f)) for f in file_ids]
    return _clip("、".join(parts) if parts else "（无）")


# -- 预览内容构造（summary/changes/side_effects/source_revisions） ------------

def _preview_content(student_id: str, operation: str,
                     data: dict[str, Any], *,
                     is_admin: bool = False) -> dict[str, Any]:
    """确定性构造预览内容；目标实体不存在时 404（预览即暴露不可执行）。"""
    out: dict[str, Any] = {
        "summary": operation, "affected_entities": [], "changes": [],
        "side_effects": [], "source_revisions": {},
    }
    if operation == "task.create":
        out["summary"] = f"创建学习任务「{data.get('title', '')}」"
        out["changes"] = [{"field": "title", "label": "任务标题",
                           "before": None,
                           "after": _clip(str(data.get("title")))}]
        out["side_effects"] = ["任务只写入学习计划，不代表已学会。"]
        return out
    if operation == "task.complete":
        out["summary"] = f"把任务 {data.get('task_id', '')} 标记为已完成（self_report）"
        out["side_effects"] = ["completion_source=self_report；不写学习能力评价。"]
        return out
    if operation == "note.create":
        out["summary"] = f"创建笔记「{data.get('title') or '未命名'}」"
        out["changes"] = [{"field": "content", "label": "笔记正文（AI 生成，请审阅）",
                           "before": None,
                           "after": _clip(str(data.get("content")))}]
        out["side_effects"] = ["正式保存后进入笔记仓库并计入存储。"]
        return out
    if operation == "chat.rename":
        session = _owned_session(student_id, str(data.get("session_id")))
        out["summary"] = f"把对话重命名为「{data.get('title', '')}」"
        out["changes"] = [{"field": "title", "label": "对话标题",
                           "before": _clip(session.title or ""),
                           "after": _clip(str(data.get("title")))}]
        out["source_revisions"] = {"session_title": _clip(session.title or "", 128)}
        return out
    if operation == "schedule.update":
        out["summary"] = f"把每日学习时间预算改为 {data.get('daily_minutes')} 分钟"
        out["changes"] = [{"field": "daily_minutes", "label": "每日时间预算",
                           "before": None,
                           "after": str(data.get("daily_minutes"))}]
        out["side_effects"] = ["只改时间预算，不自动重规划。"]
        return out
    if operation == "workspace.create":
        add_ids = [str(f) for f in (data.get("file_ids") or [])]
        names = _textbook_names(student_id, add_ids) if add_ids else {}
        if add_ids and set(add_ids) - set(names):
            # 与执行一致：无法访问的教材直接 404，不静默减少选入集合。
            raise ActionRejected(404, "entity_not_found",
                                 "file_ids 中存在无法访问的教材。")
        out["summary"] = f"创建辅导区「{data.get('name', '')}」"
        out["changes"] = [{"field": "name", "label": "辅导区名称",
                           "before": None, "after": _clip(str(data.get("name")))}]
        if add_ids:
            out["changes"].append({
                "field": "file_ids", "label": "选入教材",
                "before": None, "after": _names_label(names, add_ids)})
            out["side_effects"] = [
                "创建时同步建立专属资料夹并选入上述教材。"]
        else:
            out["side_effects"] = ["创建时同步建立专属资料夹（当前未选入教材）。"]
        return out
    if operation == "workspace.update_sources":
        ws = _owned_workspace(student_id, str(data.get("workspace_id")))
        current = [str(f) for f in (ws.selected_file_ids or [])]
        add = [str(f) for f in (data.get("add_file_ids") or [])]
        names = _textbook_names(
            student_id, list(dict.fromkeys(current + add)))
        if set(add) - set(names):
            raise ActionRejected(404, "entity_not_found",
                                 "add_file_ids 中存在无法访问的教材。")
        remove = {str(f) for f in (data.get("remove_file_ids") or [])}
        merged = [f for f in current if f not in remove] + \
                 [f for f in add if f not in current]
        out["summary"] = f"更新辅导区「{ws.name}」的教材来源"
        out["changes"] = [{
            "field": "selected_file_ids", "label": "教材来源（合并后）",
            "before": _names_label(names, current),
            "after": _names_label(names, merged)}]
        out["side_effects"] = [
            "服务端基于最新集合合并；他人并发新增的教材不会被覆盖。"]
        if remove:
            out["side_effects"].append("移除教材可能触发该辅导区评价范围变更。")
        out["source_revisions"] = {
            "workspace_updated_at": _clip(str(ws.updated_at), 128)}
        out["affected_entities"] = [{
            "kind": "workspace_chat", "workspace_id": str(ws.workspace_id)}]
        return out
    if operation == "chat.move_workspace":
        session = _owned_session(student_id, str(data.get("session_id")))
        ws = _owned_workspace(student_id, str(data.get("workspace_id")))
        from app.core.workspace import load_workspace
        before = "未分组"
        if session.workspace_id:
            prev = load_workspace(session.workspace_id)
            if prev is not None:
                before = prev.name
        out["summary"] = f"把对话移动到辅导区「{ws.name}」"
        out["changes"] = [{
            "field": "workspace", "label": "所属辅导区",
            "before": _clip(before), "after": _clip(ws.name)}]
        out["side_effects"] = [
            "移动后该对话进入新辅导区的公共记忆范围。"]
        out["source_revisions"] = {
            "session_workspace": _clip(session.workspace_id or "loose", 128)}
        out["affected_entities"] = [{
            "kind": "workspace_chat", "workspace_id": str(ws.workspace_id)}]
        return out
    if operation == "chat.archive":
        session = _owned_session(student_id, str(data.get("session_id")))
        out["summary"] = f"归档对话「{session.title or session.session_id}」"
        out["changes"] = [{
            "field": "session", "label": "对话",
            "before": _clip(session.title or str(session.session_id)),
            "after": "移入归档（可恢复）"}]
        out["side_effects"] = [
            "对话移入归档而非删除，可在归档页恢复。",
            "若该对话正在生成回复，生成会被中断。"]
        out["source_revisions"] = {"session_present": "1"}
        return out
    if operation == "library.create_folder":
        out["summary"] = f"创建资料文件夹「{data.get('name', '')}」"
        out["changes"] = [{"field": "name", "label": "文件夹名称",
                           "before": None, "after": _clip(str(data.get("name")))}]
        return out
    if operation == "library.rename_file":
        from app.core.library import load_library
        lib = load_library(student_id)
        f = lib.find_file(str(data.get("file_id")))
        if f is None:
            raise ActionRejected(404, "entity_not_found", "文件不存在。")
        out["summary"] = f"重命名资料文件「{f.get('filename', '')}」"
        out["changes"] = [{
            "field": "filename", "label": "文件名",
            "before": _clip(str(f.get("filename") or "")),
            "after": _clip(str(data.get("filename")))}]
        out["side_effects"] = ["检索切片与向量 ID 保持稳定，仅改显示名。"]
        out["source_revisions"] = {
            "file_updated_at": _clip(str(f.get("updated_at") or ""), 128)}
        return out
    if operation == "library.move_file":
        from app.core.library import load_library
        lib = load_library(student_id)
        f = lib.find_file(str(data.get("file_id")))
        if f is None:
            raise ActionRejected(404, "entity_not_found", "文件不存在。")
        target_id = str(data.get("folder_id") or "")
        target_name = "根目录"
        if target_id:
            folder = lib.find_folder(target_id)
            if folder is None:
                raise ActionRejected(404, "entity_not_found",
                                     "目标文件夹不存在。")
            target_name = str(folder.get("name") or target_id)
        current_name = "根目录"
        if f.get("folder_id"):
            cur = lib.find_folder(str(f.get("folder_id")))
            current_name = str((cur or {}).get("name") or f.get("folder_id"))
        out["summary"] = f"移动文件「{f.get('filename', '')}」到{target_name}"
        out["changes"] = [{
            "field": "folder", "label": "所在文件夹",
            "before": _clip(current_name), "after": _clip(target_name)}]
        out["side_effects"] = [
            "只移动文件本身，不删除原件；文件内容不变。"]
        out["source_revisions"] = {
            "file_updated_at": _clip(str(f.get("updated_at") or ""), 128)}
        out["affected_entities"] = [{
            "kind": "file", "file_id": str(f.get("id")),
            "folder_id": target_id or None}]
        return out
    if operation == "textbook.cancel":
        tb, _owner = _owned_textbook(student_id, str(data.get("textbook_id")),
                                     is_admin=is_admin)
        out["summary"] = f"取消教材「{tb.get('title') or data.get('textbook_id')}」的处理"
        out["changes"] = [{
            "field": "status", "label": "教材状态",
            "before": _clip(str(tb.get("status") or "")), "after": "停止处理"}]
        out["side_effects"] = [
            "合作式取消：停止解析/OCR/图谱构建，已提取文本与图谱保留。",
            "对空闲教材调用无副作用（幂等）。"]
        out["source_revisions"] = {
            "textbook_status": _clip(str(tb.get("status") or ""), 128)}
        out["affected_entities"] = [{
            "kind": "textbook", "textbook_id": str(tb.get("id"))}]
        return out
    if operation == "textbook.rebuild":
        tb, _owner = _owned_textbook(student_id, str(data.get("textbook_id")),
                                     is_admin=is_admin)
        mode = str(data.get("mode") or "rag_graph")
        mode_label = {"rag_graph": "重建索引与图谱（默认，不跑 OCR）",
                      "full_ocr": "完整重新 OCR（耗时最长）",
                      "graph_only": "只重建图谱（不动切片索引）"}[mode]
        out["summary"] = (f"刷新教材「{tb.get('title') or data.get('textbook_id')}」"
                          f"：{mode_label}")
        out["changes"] = [{
            "field": "mode", "label": "刷新模式", "before": None,
            "after": mode_label}]
        out["side_effects"] = [
            "消耗后台计算资源（OCR/图谱构建），可能需要数分钟。",
            "刷新期间教材状态显示为构建中；已有文本不会被清空。"]
        out["source_revisions"] = {
            "textbook_status": _clip(str(tb.get("status") or ""), 128)}
        out["affected_entities"] = [{
            "kind": "textbook", "textbook_id": str(tb.get("id"))}]
        return out
    if operation == "archive.restore":
        from app.core import trash as trash_store
        manifest = trash_store.get_item(student_id, str(data.get("item_id")))
        if manifest is None:
            raise ActionRejected(404, "entity_not_found", "归档不存在。")
        rtype = str(manifest.get("resource_type") or "")
        type_label = {
            "session": "对话", "library_file": "资料文件",
            "library_folder": "资料文件夹", "textbook": "教材",
            "textbook_volume": "教材卷", "classroom_lesson": "课程",
            "workspace": "辅导区", "knowledge_graph": "知识图谱",
        }.get(rtype, rtype)
        title = str(manifest.get("title") or "未命名")
        out["summary"] = f"从归档恢复「{title}」（{type_label}）"
        out["changes"] = [
            {"field": "title", "label": "恢复内容",
             "before": None, "after": _clip(title)},
            {"field": "resource_type", "label": "实体类型",
             "before": None, "after": type_label}]
        side: list[str] = ["恢复后原归档条目被消费；如需撤销可重新归档。"]
        meta = manifest.get("metadata") or {}
        if meta.get("workspace_id"):
            try:
                _owned_workspace(student_id, str(meta.get("workspace_id")))
            except ActionRejected:
                side.append("原辅导区已不存在，恢复后需要重新选择归属。")
        out["side_effects"] = side
        out["source_revisions"] = {
            "trash_deleted_at": _clip(str(manifest.get("deleted_at") or ""), 128)}
        out["affected_entities"] = [{
            "kind": "archive_item", "item_id": str(manifest.get("id")),
            "resource_type": rtype}]
        return out
    # -- B06：笔记 / 学习编排 ------------------------------------------------
    if operation == "note.append":
        from app.core import notes as notes_store
        meta = _owned_note(student_id, str(data.get("note_id")))
        revision = int(meta.get("revision") or 1)
        if revision != int(data.get("base_revision") or 0):
            raise ActionRejected(409, "revision_conflict",
                                 "笔记已被编辑，请打开最新内容后再追加。")
        out["summary"] = (f"向笔记「{meta.get('title', '')}」追加内容"
                          f"（基于版本 {revision}）")
        out["changes"] = [{
            "field": "append_markdown", "label": "追加的正文",
            "before": None,
            "after": _clip(str(data.get("append_markdown")))}]
        out["side_effects"] = ["追加生成新版本；原内容保留在版本历史。"]
        out["source_revisions"] = {"note_revision": str(revision)}
        out["affected_entities"] = [{
            "kind": "note", "note_id": str(data.get("note_id"))}]
        return out
    if operation == "note.replace":
        from app.core import notes as notes_store
        meta = _owned_note(student_id, str(data.get("note_id")))
        vault = notes_store.load_vault(student_id)
        revision = int(meta.get("revision") or 1)
        if revision != int(data.get("base_revision") or 0):
            raise ActionRejected(409, "revision_conflict",
                                 "笔记已被编辑，请查看最新差异。")
        changes = [{
            "field": "content", "label": "正文（改写，请审阅）",
            "before": _clip(vault.read_note(str(data.get("note_id")))),
            "after": _clip(str(data.get("content")))}]
        if str(data.get("title") or "").strip() \
                and str(data["title"]).strip() != str(meta.get("title") or ""):
            changes.insert(0, {
                "field": "title", "label": "标题",
                "before": _clip(str(meta.get("title") or "")),
                "after": _clip(str(data["title"]))})
        out["summary"] = f"改写笔记「{meta.get('title', '')}」"
        out["changes"] = changes
        out["side_effects"] = ["替换前内容保留在版本历史，可随时恢复。"]
        out["source_revisions"] = {"note_revision": str(revision)}
        out["affected_entities"] = [{
            "kind": "note", "note_id": str(data.get("note_id"))}]
        return out
    if operation == "note.move":
        from app.core import notes as notes_store
        vault = notes_store.load_vault(student_id)
        metas = []
        for nid in [str(n) for n in (data.get("note_ids") or [])]:
            meta = vault.find_note(nid)
            if meta is None:
                raise ActionRejected(404, "entity_not_found",
                                     f"笔记 {nid} 不存在。")
            metas.append(meta)
        target_id = str(data.get("folder_id") or "")
        target_name = "未分类"
        if target_id:
            folder = vault.find_folder(target_id)
            if folder is None:
                raise ActionRejected(404, "entity_not_found",
                                     "目标文件夹不存在。")
            target_name = str(folder.get("name") or target_id)
        out["summary"] = (f"移动 {len(metas)} 篇笔记到「{target_name}」"
                          if len(metas) > 1
                          else f"移动笔记「{metas[0].get('title', '')}」到"
                               f"「{target_name}」")
        out["changes"] = [{
            "field": "folder", "label": "目标文件夹",
            "before": None, "after": target_name}]
        if len(metas) > 1:
            out["changes"].append({
                "field": "notes", "label": "笔记清单",
                "before": None,
                "after": _clip("、".join(
                    str(m.get("title") or m.get("id")) for m in metas))})
        out["source_revisions"] = {
            "note_revisions": _clip(",".join(
                f"{m.get('id')}:{m.get('revision')}" for m in metas), 200)}
        out["affected_entities"] = [
            {"kind": "note", "note_id": str(m.get("id"))} for m in metas]
        return out
    if operation == "note.set_review":
        meta = _owned_note(student_id, str(data.get("note_id")))
        enabled = bool(data.get("enabled"))
        review = dict(meta.get("review") or {})
        out["summary"] = (f"开启笔记「{meta.get('title', '')}」的复习计划"
                          if enabled
                          else f"关闭笔记「{meta.get('title', '')}」的复习计划")
        out["changes"] = [{
            "field": "review_enabled", "label": "复习计划",
            "before": "已开启" if review.get("enabled") else "已关闭",
            "after": "开启" if enabled else "关闭"}]
        out["side_effects"] = (["按遗忘曲线安排复习提醒。"] if enabled
                               else ["不再安排新复习；已到期的复习卡移除。"])
        out["source_revisions"] = {
            "note_updated_at": _clip(str(meta.get("updated_at") or ""), 128)}
        out["affected_entities"] = [{
            "kind": "note", "note_id": str(data.get("note_id"))}]
        return out
    if operation == "note.restore_revision":
        from app.core import notes as notes_store
        vault = notes_store.load_vault(student_id)
        meta = _owned_note(student_id, str(data.get("note_id")))
        revision = int(meta.get("revision") or 1)
        if revision != int(data.get("expected_current_revision") or 0):
            raise ActionRejected(409, "revision_conflict",
                                 "笔记此后已被编辑，请查看最新版本。")
        want = int(data.get("revision") or 0)
        content = vault.read_revision(str(data.get("note_id")), want)
        if content is None:
            raise ActionRejected(404, "entity_not_found", "版本不存在。")
        out["summary"] = (f"把笔记「{meta.get('title', '')}」恢复到版本 {want}"
                          f"（当前版本 {revision}）")
        out["changes"] = [{
            "field": "content", "label": f"将恢复为版本 {want} 内容",
            "before": _clip(vault.read_note(str(data.get("note_id")))),
            "after": _clip(content)}]
        out["side_effects"] = ["恢复生成新当前版本；版本历史完整保留。"]
        out["source_revisions"] = {"note_revision": str(revision)}
        out["affected_entities"] = [{
            "kind": "note", "note_id": str(data.get("note_id"))}]
        return out
    if operation == "goal.create":
        out["summary"] = f"创建学习目标「{data.get('title', '')}」"
        out["changes"] = [{
            "field": "title", "label": "目标标题",
            "before": None, "after": _clip(str(data.get("title")))}]
        if data.get("deadline"):
            try:
                out["changes"].append({
                    "field": "deadline", "label": "目标期限",
                    "before": None,
                    "after": datetime.fromtimestamp(
                        float(data["deadline"])).strftime("%Y-%m-%d")})
            except (ValueError, OSError, OverflowError):
                pass
        if data.get("subjects"):
            out["changes"].append({
                "field": "subjects", "label": "学科",
                "before": None,
                "after": _clip("、".join(map(str, data["subjects"])))})
        out["side_effects"] = [
            "创建目标会立即触发重新规划：周计划按新目标重排。",
            "你创建的任务与已保存日程不会被覆盖。"]
        return out
    if operation == "goal.update":
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        svc = get_orchestration_service()
        state = svc._load(student_id)
        goal = next((g for g in state.goals
                     if g.id == str(data.get("goal_id"))), None)
        if goal is None:
            raise ActionRejected(404, "entity_not_found", "目标不存在。")
        field_label = {
            "title": ("title", "目标标题"),
            "description": ("description", "目标说明"),
            "goal_type": ("goal_type", "目标类型"),
            "deadline": ("deadline", "目标期限"),
            "workspace_id": ("workspace_id", "归属辅导区"),
        }
        changes = []
        for key, (attr, label) in field_label.items():
            if data.get(key) is not None:
                changes.append({
                    "field": key, "label": label,
                    "before": _clip(str(getattr(goal, attr, "") or "")),
                    "after": _clip(str(data[key]))})
        if data.get("subjects") is not None:
            changes.append({
                "field": "subjects", "label": "学科",
                "before": _clip("、".join(goal.subjects or [])),
                "after": _clip("、".join(map(str, data["subjects"])))})
        out["summary"] = f"更新学习目标「{goal.title}」"
        out["changes"] = changes or [{
            "field": "goal", "label": "目标", "before": None, "after": ""}]
        weeks = state.weekly_plan or []
        out["side_effects"] = [
            f"更新后重新规划：当前 {len(weeks)} 周计划将按新目标重排。",
            "user 来源的周/任务/子任务保持不变。"]
        out["source_revisions"] = {
            "goal_fingerprint": svc._goals_fingerprint(state)}
        out["affected_entities"] = [{
            "kind": "orchestration_goal", "goal_id": str(goal.id)}]
        return out
    if operation == "plan.regenerate":
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        svc = get_orchestration_service()
        state = svc._load(student_id)
        candidate = _latest_plan_candidate(state)
        if candidate is None:
            raise ActionRejected(409, "preview_stale",
                                 "计划候选缺失，请重新查看变更。")
        weeks = candidate.get("weeks") or []
        current_len = len(state.weekly_plan or [])
        out["summary"] = (f"按当前目标重新生成 {candidate.get('num_weeks', 4)}"
                          f" 周学习计划（候选已生成，确认后逐字应用）")
        out["changes"] = [{
            "field": "weekly_plan", "label": "周计划",
            "before": f"当前 {current_len} 周",
            "after": _clip("；".join(
                f"第{int(w.get('week_index', i))}周"
                f" {str(w.get('focus') or '')[:24]}"
                for i, w in enumerate(weeks)) or "（无可排内容）")}]
        out["side_effects"] = [
            "确认的是当前候选本身，提交时不再重新生成。",
            "已保存的日任务与 user 来源计划内容不被覆盖。"]
        out["source_revisions"] = {
            "plan_candidate": str(candidate.get("candidate_id") or ""),
            "weekly_plan_len": str(current_len)}
        out["candidate_id"] = str(candidate.get("candidate_id") or "")
        return out
    if operation == "task.update":
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        task = _owned_task(student_id, str(data.get("task_id")))
        label_map = [
            ("title", "任务标题", "title"),
            ("day", "日期", "day"),
            ("kind", "类型", "kind"),
            ("phase", "阶段", "phase"),
            ("estimate_minutes", "预计分钟", "estimate_minutes"),
            ("priority", "优先级", "priority"),
        ]
        changes = [{
            "field": key, "label": label,
            "before": _clip(str(getattr(task, attr, "") or "")),
            "after": _clip(str(data[key]))}
            for key, label, attr in label_map if data.get(key) is not None]
        out["summary"] = f"更新任务「{task.title}」"
        out["changes"] = changes
        out["side_effects"] = ["只写明确给出的字段，其余任务信息不变。"]
        out["source_revisions"] = {
            "task_snapshot": _clip(
                f"{task.id}:{task.title}:{task.day}:", 128)}
        out["affected_entities"] = [{
            "kind": "orchestration_task", "task_id": str(task.id)}]
        return out
    if operation == "subtask.create":
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        svc = get_orchestration_service()
        state = svc._load(student_id)
        week_index = int(data.get("week_index") or 0)
        week = next((w for w in state.weekly_plan
                     if w.week_index == week_index), None)
        task = next((t for w in state.weekly_plan if w.week_index == week_index
                     for t in w.tasks
                     if t.id == str(data.get("week_task_id"))), None)
        if week is None or task is None:
            raise ActionRejected(404, "entity_not_found",
                                 "周任务不存在。")
        out["summary"] = (f"为第 {week_index} 周任务「{task.title}」"
                          f"添加子任务「{data.get('title', '')}」")
        out["changes"] = [{
            "field": "subtask", "label": f"子任务（现有 {len(task.subtasks)} 项）",
            "before": None, "after": _clip(str(data.get("title")))}]
        out["side_effects"] = ["子任务进入该周任务；不改动其他周。"]
        out["source_revisions"] = {
            "subtask_count": str(len(task.subtasks))}
        out["affected_entities"] = [{
            "kind": "orchestration_task", "task_id": str(task.id)}]
        return out
    # -- B07：测评 / 评价 / 教学指导 -----------------------------------------
    if operation == "assessment.start":
        concepts = [str(k) for k in (data.get("concept_keys") or [])]
        scope_rev = str(data.get("expected_scope_revision") or "")
        if data.get("workspace_id"):
            _resolve_eval_scope(student_id, str(data.get("workspace_id")))
        out["summary"] = (f"开始测评：{len(concepts)} 个概念，"
                          f"共 {int(data.get('count') or 1)} 题")
        out["changes"] = [
            {"field": "concepts", "label": "测评范围",
             "before": None, "after": _clip("、".join(concepts))},
            {"field": "count", "label": "题数",
             "before": None, "after": str(int(data.get("count") or 1))}]
        out["side_effects"] = [
            "题目生成消耗后台资源；测评由你作答，助手不代答。",
            "作答与提示记录进入学习证据（FULL-09 提示记账）。"]
        out["source_revisions"] = {"scope_revision": _clip(scope_rev, 128)}
        return out
    if operation == "assessment.practice":
        state = _eval_journal_state(student_id)
        qid = str(data.get("question_id"))
        revs = state.tasks.get(qid) or {}
        rev = int(data.get("question_revision") or 0)
        if not revs or revs.get(rev) is None:
            raise ActionRejected(404, "entity_not_found", "题目不存在。")
        mode = str(data.get("mode") or "same")
        mode_label = "同题再练" if mode == "same" else "变式练习"
        out["summary"] = f"{mode_label}（来源题目 {qid} 版本 {rev}）"
        out["changes"] = [{
            "field": "mode", "label": "练习方式", "before": None,
            "after": mode_label}]
        out["side_effects"] = [
            "新题目实例由你作答；来源与版本记录保持可追溯。",
            "生成消耗题目生成资源。"]
        out["source_revisions"] = {
            "question_revision": str(rev)}
        out["affected_entities"] = [{"kind": "question", "question_id": qid}]
        return out
    if operation == "evaluation.request_review":
        state = _eval_journal_state(student_id)
        src = state.sources.get(str(data.get("source_id")))
        if src is None:
            raise ActionRejected(404, "entity_not_found", "证据不存在。")
        if int(src.receipt.source_revision) != int(
                data.get("expected_revision") or 0):
            raise ActionRejected(409, "revision_conflict",
                                 "证据版本已变化，请刷新后复核。")
        if str(data.get("interpretation_id")) not in (src.interpretations
                                                      or []):
            raise ActionRejected(404, "entity_not_found",
                                 "被争议的解释不存在，请刷新。")
        out["summary"] = "提交学习评价复核（附你的理由）"
        out["changes"] = [{
            "field": "reason", "label": "复核理由",
            "before": None, "after": _clip(str(data.get("reason")))}]
        out["side_effects"] = [
            "理由与被争议解释绑定；复核 job 完成后展示新的真实结果。",
            "同源已有进行中的复核时会返回既有复核，不重复受理。"]
        out["source_revisions"] = {
            "source_revision": str(src.receipt.source_revision)}
        return out
    if operation == "evaluation.retry":
        state = _eval_journal_state(student_id)
        rt = state.jobs.get(str(data.get("job_id")))
        if rt is None:
            raise ActionRejected(404, "entity_not_found", "作业不存在。")
        if str(rt.job.state.value) != "failed":
            raise ActionRejected(409, "target_changed",
                                 "只有失败的作业可重试。")
        out["summary"] = "重试失败的评价作业（新作业关联原作业）"
        out["changes"] = [{
            "field": "job", "label": "作业",
            "before": "failed", "after": "重新排队（parent 关联）"}]
        out["side_effects"] = ["已完成结果不被改写；新作业独立运行。"]
        return out
    if operation == "evaluation.synthesize":
        scope = _resolve_eval_scope(student_id, str(data.get("workspace_id")))
        expected = str(data.get("expected_scope_revision") or "")
        if expected and expected != scope.scope_revision:
            raise ActionRejected(409, "revision_conflict",
                                 "教材范围已变化，请刷新后重试。")
        out["summary"] = "重新生成辅导区综合评价"
        out["changes"] = [{
            "field": "workspace", "label": "辅导区",
            "before": None,
            "after": _clip(str(getattr(scope, "label", None)
                               or data.get("workspace_id")))}]
        out["side_effects"] = [
            "仅在显式请求时重新生成；已有排队的综合作业会复用，不重复排队。"]
        out["source_revisions"] = {
            "scope_revision": _clip(scope.scope_revision, 128)}
        return out
    if operation in ("teaching.approve", "teaching.apply"):
        from app.agents.evaluation import store as eval_store
        proposal = eval_store.load_proposal(
            student_id, str(data.get("proposal_id")))
        if proposal is None:
            raise ActionRejected(404, "entity_not_found", "教学提案不存在。")
        expected = str(data.get("expected_status") or "proposed")
        if str(proposal.status) != expected:
            raise ActionRejected(409, "target_changed",
                                 f"提案状态已是 {proposal.status}，请刷新。")
        is_apply = operation == "teaching.apply"
        out["summary"] = (f"{'应用' if is_apply else '批准'}教学提案"
                          f"「{str(proposal.title)[:24]}」")
        out["changes"] = [{
            "field": "status", "label": "提案状态",
            "before": str(proposal.status),
            "after": "applied" if is_apply else "approved"}]
        if is_apply:
            out["side_effects"] = [
                "指导文本部署到教学引擎；完成前不会显示已生效。",
                "部署失败保留部分结果，可再次应用补部署（幂等）。"]
        else:
            out["side_effects"] = ["批准只表示同意；应用是另一次独立确认。"]
        out["source_revisions"] = {"proposal_status": str(proposal.status)}
        return out
    if operation == "teaching.revoke":
        from app.agents.teaching_engine import guidance_store
        entry = next((e for e in guidance_store.load_all(student_id)
                      if e.id == str(data.get("guidance_id"))), None)
        if entry is None:
            raise ActionRejected(404, "entity_not_found", "教学指导不存在。")
        if not entry.active:
            raise ActionRejected(409, "target_changed",
                                 "该指导已撤销，请刷新。")
        out["summary"] = f"撤销教学指导「{str(entry.title)[:24]}」"
        out["changes"] = [{
            "field": "active", "label": "指导状态",
            "before": "生效中", "after": "已撤销"}]
        out["side_effects"] = [
            "撤销后教学引擎立即停止使用该指导；审计记录保留。",
            "后续教学回到无该指导的行为。"]
        return out
    # -- B09：画像 / 记忆 / 助手偏好 -----------------------------------------
    if operation == "memory.set_window":
        from app.agents.memory.prompt_memory import get_policy, get_user_window
        current = get_user_window(student_id)
        out["summary"] = (f"把跨会话记忆窗口从 {current} 轮改为 "
                          f"{int(data.get('window'))} 轮")
        out["changes"] = [{
            "field": "window", "label": "记忆窗口（轮）",
            "before": str(current),
            "after": str(int(data.get("window")))}]
        out["side_effects"] = [
            "只改偏好，不改动任何学习证据或已有记忆内容。"]
        out["source_revisions"] = {"window": str(current)}
        return out
    if operation == "profile.update":
        from app.identity import store as id_store
        user = id_store.get_by_id(student_id)
        if user is None:
            raise ActionRejected(404, "entity_not_found", "账号不存在。")
        profile = user.profile
        labels = (("name", "姓名", profile.name),
                  ("grade", "学段", profile.grade),
                  ("school", "学校", profile.school),
                  ("subjects", "学科", "、".join(profile.subjects or [])))
        changes = [{
            "field": key, "label": label, "before": _clip(str(current)),
            "after": _clip("、".join(map(str, data[key]))
                           if key == "subjects" else str(data[key]))}
            for key, label, current in labels
            if data.get(key) is not None]
        if not changes:
            raise ActionRejected(422, "invalid_target", "没有可更新字段。")
        out["summary"] = "更新账户资料"
        out["changes"] = changes
        out["side_effects"] = [
            "学段变化会同步到讲解与出题基线（沿用账户资料服务）。",
            "助手不维护第二套学术画像。"]
        out["source_revisions"] = {
            "profile_epoch": str(int(user.updated_at)) if hasattr(
                user, "updated_at") else "1"}
        return out
    if operation == "assistant.preferences":
        from app.core.assistant_store import load_preferences
        prefs = load_preferences(student_id)
        labels = {"tone": "助手语气", "response_length": "回答长度",
                  "default_scope": "默认查询范围",
                  "voice_policy": "语音合成策略",
                  "voice_id": "朗读音色", "auto_read": "自动朗读",
                  "send_after_recording": "录音后自动发送",
                  "voice_input_mode": "录入方式",
                  "playback_rate": "朗读倍速", "volume": "朗读音量",
                  "allow_local_fallback": "允许本地回退",
                  "proactive_enabled": "主动服务"}
        changes = [{
            "field": key, "label": label,
            "before": str(prefs.get(key) if prefs.get(key) is not None
                          else ""),
            "after": str(data[key])}
            for key, label in labels.items() if data.get(key) is not None]
        if not changes:
            raise ActionRejected(422, "invalid_target", "没有可更新偏好。")
        out["summary"] = "更新助手偏好"
        out["changes"] = changes
        out["side_effects"] = [
            "只影响助手回答风格与语音偏好，不改动主题/字号等界面设置。",
            "回执可撤销；撤销恢复原值。"]
        out["source_revisions"] = {
            "prefs_revision": str(prefs.get("revision") or 1)}
        return out
    # -- B10：课程高级动作 ----------------------------------------------------
    if operation == "lesson.generate":
        from app.core.workspace import load_workspace
        ws = load_workspace(str(data.get("workspace_id")))
        if ws is None or (ws.student_id or "") != student_id:
            raise ActionRejected(404, "entity_not_found", "辅导区不存在。")
        source_ids = [str(f) for f in (data.get("source_file_ids") or [])]
        sources_label = "未指定（默认当前辅导区教材）"
        if source_ids:
            names = _textbook_names(student_id, source_ids)
            if set(source_ids) - set(names):
                raise ActionRejected(404, "entity_not_found",
                                     "source_file_ids 中存在不可用教材。")
            sources_label = _names_label(names, source_ids)
        start_mode = str(data.get("start_mode") or "automatic")
        mode_label = ("直接生成全套课件" if start_mode == "automatic"
                      else "先生成大纲，确认后继续")
        out["summary"] = (f"生成课件「{data.get('topic', '')}」"
                          f"（{mode_label}）")
        out["changes"] = [
            {"field": "topic", "label": "课程主题", "before": None,
             "after": _clip(str(data.get("topic")))},
            {"field": "sources", "label": "来源教材", "before": None,
             "after": sources_label},
            {"field": "duration_minutes", "label": "时长（分钟）",
             "before": None,
             "after": str(int(data.get("duration_minutes") or 15))},
            {"field": "start_mode", "label": "生成方式", "before": None,
             "after": mode_label}]
        out["side_effects"] = [
            "生成消耗课堂任务预算；完成后在课程介绍页查看，不自动开课。",
            "页面排版与构图由生成管线在受控枚举内决定，助手不指定。",
            "同一动作重试复用同一生成任务（幂等），不重复扣额。"]
        out["source_revisions"] = {"workspace_updated_at": _clip(
            str(ws.updated_at), 128)}
        return out
    if operation in ("lesson.retry", "lesson.cancel"):
        snapshot = _classroom_job_snapshot(
            student_id, str(data.get("workspace_id")),
            str(data.get("lesson_id")), str(data.get("job_id")))
        if int(snapshot.get("state_revision") or 0) != int(
                data.get("expected_state_revision") or 0):
            raise ActionRejected(409, "preview_stale",
                                 "任务状态已变化，请刷新后重试。")
        state = str(snapshot.get("state") or "")
        is_retry = operation == "lesson.retry"
        if is_retry and state not in ("failed", "cancelled", "needs_input"):
            raise ActionRejected(409, "target_changed",
                                 f"状态 {state} 不支持重试。")
        if not is_retry and state in ("succeeded", "failed", "cancelled"):
            raise ActionRejected(409, "target_changed",
                                 f"任务已终态（{state}），无需取消。")
        out["summary"] = ("重试课件生成任务（保留已成功产物）" if is_retry
                          else "请求取消课件生成任务")
        out["changes"] = [{
            "field": "job", "label": "生成任务",
            "before": state,
            "after": ("重新排队" if is_retry else "请求取消（保留产物）")}]
        out["side_effects"] = [
            "复用现有领域任务，不新建生成任务。"
            if is_retry else "取消是协作式：进行中的阶段完成当前步后停止。"]
        out["source_revisions"] = {
            "state_revision": str(snapshot.get("state_revision") or "")}
        return out
    if operation == "lesson.export":
        from app.classroom import service as classroom_service
        try:
            classroom_service.load_published_revision(
                student_id, str(data.get("workspace_id")),
                str(data.get("lesson_id")), int(data.get("revision") or 0))
        except Exception as exc:
            raise ActionRejected(404, "entity_not_found", str(exc)[:120]
                                 or "课程版本不存在。")
        fmt = str(data.get("fmt") or "html_zip")
        fmt_label = {"html_zip": "HTML 离线包（含讲稿）",
                     "notes_md": "讲稿 Markdown"}[fmt]
        out["summary"] = f"导出课件（{fmt_label}，24 小时内有效）"
        out["changes"] = [{
            "field": "export", "label": "导出内容", "before": None,
            "after": fmt_label}]
        out["side_effects"] = [
            "导出成功后提供下载链接，不自动触发浏览器下载。"]
        return out
    return out


def approve(student_id: str, action_id: str, *, preview_id: str,
            parameter_hash_in: str, decision: str) -> dict[str, Any]:
    """POST /actions/{aid}/approve：登记审批许可（§21.4）。

    approve 只表示允许该具体动作；参数或业务版本变化使旧许可失效。
    """
    if not actions_enabled():
        raise ActionRejected(503, "capability_disabled",
                             "领域写动作当前未开放。")
    located = actions_svc.locate_action(student_id, action_id)
    if located is None:
        raise ActionRejected(404, "entity_not_found", "该动作不存在。")
    record, _cid, action = located
    preview = _preview_doc(action)
    if not preview or preview.get("preview_id") != preview_id:
        raise ActionRejected(409, "preview_stale", "预览已更新，请查看最新差异。")
    if preview.get("parameter_hash") != parameter_hash_in:
        raise ActionRejected(409, "preview_stale",
                             "参数已变化，预览不再有效。")
    approval = {
        "approval_id": "aap_" + secrets.token_hex(12),
        "preview_id": preview_id,
        "parameter_hash": parameter_hash_in,
        "source_revisions": dict(preview.get("source_revisions") or {}),
        "decision": decision,
        "expires_at": (datetime.now(tz=timezone.utc)
                       + timedelta(seconds=APPROVAL_TTL_SECONDS)).isoformat(),
    }
    action["approval"] = approval
    if decision == "reject":
        action["state"] = "cancelled"
    _save(record, student_id)
    return approval


def require_approval(student_id: str, action: dict[str, Any],
                     approval_id: str | None, *,
                     is_admin: bool = False) -> None:
    """execute 前校验许可：review_required 必须持有当前有效许可。

    §21.4：许可绑定 parameter_hash 与 source_revisions；业务版本变化
    （如目标已被编辑）同样使旧许可失效，需重新预览。
    """
    payload = action.get("payload") or {}
    operation = str(payload.get("operation") or "")
    data = payload.get("input") or {}
    if policy_for(operation, data) != "review_required":
        return
    approval = action.get("approval")
    if (not approval_id or not approval
            or approval.get("approval_id") != approval_id
            or approval.get("decision") != "approve"
            or approval.get("parameter_hash")
            != parameter_hash(operation, data)):
        raise ActionRejected(409, "preview_stale",
                             "该操作需要先确认当前预览。")
    expires = str(approval.get("expires_at") or "")
    try:
        if datetime.fromisoformat(expires) < datetime.now(tz=timezone.utc):
            raise ActionRejected(409, "preview_stale", "确认已过期，请重新预览。")
    except ValueError:
        raise ActionRejected(409, "preview_stale", "确认已过期，请重新预览。")
    # 业务版本复核：预览时的 source_revisions 与当前不一致 → 旧许可失效。
    if actions_enabled():
        current = _preview_content(
            student_id, operation, data,
            is_admin=is_admin)["source_revisions"]
        if dict(approval.get("source_revisions") or {}) != current:
            raise ActionRejected(409, "preview_stale",
                                 "目标状态已变化，请查看最新差异。")


# -- 事件循环桥接（execute 在 to_thread 工作线程运行） ------------------------

def _run_on_loop(main_loop, fn, *args, timeout: float = 60.0):
    """把必须运行在事件循环线程的领域编排提交到主循环并等待结果。

    main_loop 缺失（同步测试上下文）时在当前线程直接执行。
    """
    import asyncio
    if main_loop is None or not main_loop.is_running():
        return fn(*args)

    async def _call():
        return fn(*args)

    return asyncio.run_coroutine_threadsafe(_call(), main_loop).result(
        timeout=timeout)


def _refresh_workspace_memory(main_loop, ws_id: str, session_id: str) -> None:
    """移动会话后的公共记忆更新（尽力而为，失败不回滚移动结果）。"""
    try:
        from app.core.workspace_memory import init_workspace_memory_from_session
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # 本身在事件循环线程（如异步单测）：后台执行不阻塞。
            loop.create_task(
                init_workspace_memory_from_session(ws_id, session_id))
        elif main_loop is not None and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                init_workspace_memory_from_session(ws_id, session_id),
                main_loop).result(timeout=30.0)
        else:
            asyncio.run(init_workspace_memory_from_session(ws_id, session_id))
    except Exception:  # noqa: BLE001
        pass  # 与原路由一致的尽力而为语义


def _client_request_id(action: dict[str, Any], operation: str) -> str:
    """§21.6.1 稳定 client_request_id：同一动作重试复用同一业务实体。"""
    return f"assistant:{action.get('action_id')}:{operation}"


def execute_domain_write(student_id: str,
                         action: dict[str, Any], *,
                         is_admin: bool = False,
                         main_loop=None) -> dict[str, Any]:
    """领域写执行：复用原模块服务；返回 business_result（§21.6 幂等由
    client_request_id/action_id 保证）。action 为完整动作记录 dict。"""
    payload = action.get("payload") or {}
    # §21.6/B04 幂等：同一 action 重复执行（如网络重试后 failed→retry）
    # 复用首次已创建的业务实体，不重复创建。
    prior = action.get("business_result") or {}
    if prior.get("kind") not in (None, "none") and prior.get("entity_id"):
        return dict(prior)
    operation = str(payload.get("operation") or "")
    data = payload.get("input") or {}
    if not actions_enabled():
        raise ActionRejected(503, "capability_disabled",
                             "领域写动作当前未开放。")
    try:
        if operation == "task.create":
            from app.agents.learning_orchestration.manager import (
                get_orchestration_service)
            kwargs: dict[str, Any] = {}
            if data.get("estimate_minutes") is not None:
                kwargs["estimate_minutes"] = int(data["estimate_minutes"])
            task = get_orchestration_service().add_task(
                student_id, day=str(data.get("day") or ""),
                title=str(data.get("title")), concept_id="",
                concept_name="", kind="study", phase="",
                priority=3, milestone_id="", **kwargs)
            return {"kind": "task", "entity_id": task.id,
                    "related_ids": {}, "result_revision": str(task.day),
                    # §21.5：创建未被编辑时可整单撤销。
                    "undo": {"task_id": task.id, "day": str(task.day),
                             "title": str(task.title)}}
        if operation == "task.complete":
            from app.agents.learning_orchestration.manager import (
                get_orchestration_service)
            ok, _emitted = get_orchestration_service().complete_task(
                student_id, str(data.get("task_id")))
            if not ok:
                raise ActionRejected(409, "target_changed",
                                     "任务已完成或不存在。")
            return {"kind": "task", "entity_id": str(data.get("task_id")),
                    "related_ids": {}, "result_revision": "completed"}
        if operation == "note.create":
            from app.core import notes as notes_store
            vault = notes_store.load_vault(student_id)
            note = vault.create_note(
                title=str(data.get("title") or ""),
                content=str(data.get("content")),
                folder_id=str(data.get("folder_id") or ""),
                tags=list(data.get("tags") or []))
            notes_store.save_vault(vault)
            created_sha = hashlib.sha256(
                (str(data.get("title") or "") + "\x00"
                 + str(data.get("content"))).encode("utf-8")).hexdigest()
            return {"kind": "note", "entity_id": str(note.get("id")),
                    "related_ids": {}, "result_revision": "1",
                    # §21.5：标题+正文与创建时一致（未被编辑）才可撤销。
                    "undo": {"note_id": str(note.get("id")),
                             "content_sha": created_sha}}
        if operation == "chat.rename":
            from app.core.session import rename_session
            sid = str(data.get("session_id"))
            session = _owned_session(student_id, sid)
            previous_title = str(session.title or "")
            if not rename_session(sid, str(data.get("title"))):
                raise ActionRejected(404, "entity_not_found", "对话不存在。")
            return {"kind": "chat", "entity_id": sid,
                    "related_ids": {}, "result_revision": "renamed",
                    "undo": {"session_id": sid,
                             "previous_title": previous_title}}
        if operation == "schedule.update":
            from app.agents.learning_orchestration.manager import (
                get_orchestration_service)
            service = get_orchestration_service()
            previous = service._load(student_id).schedule.daily_minutes
            out = service.update_schedule(
                student_id, daily_minutes=int(data.get("daily_minutes")))
            if out is None:
                raise ActionRejected(503, "storage_unavailable",
                                     "日程更新失败。")
            return {"kind": "none", "entity_id": None, "related_ids": {},
                    "result_revision": str(data.get("daily_minutes")),
                    "undo": {"previous_minutes": int(previous)}}
        if operation == "workspace.create":
            return _exec_workspace_create(student_id, action, data)
        if operation == "workspace.update_sources":
            return _exec_workspace_update_sources(student_id, action, data)
        if operation == "chat.move_workspace":
            return _exec_chat_move_workspace(student_id, action, data,
                                             main_loop=main_loop)
        if operation == "chat.archive":
            return _exec_chat_archive(student_id, action, data)
        if operation == "library.create_folder":
            return _exec_library_create_folder(student_id, action, data)
        if operation == "library.rename_file":
            return _exec_library_rename_file(student_id, data)
        if operation == "library.move_file":
            return _exec_library_move_file(student_id, data)
        if operation == "textbook.cancel":
            return _exec_textbook_cancel(student_id, data, is_admin=is_admin)
        if operation == "textbook.rebuild":
            return _exec_textbook_rebuild(student_id, data,
                                          is_admin=is_admin,
                                          main_loop=main_loop)
        if operation == "archive.restore":
            return _exec_archive_restore(student_id, data)
        # -- B06：笔记 / 学习编排 ------------------------------------------------
        if operation == "note.append":
            return _exec_note_append(student_id, data)
        if operation == "note.replace":
            return _exec_note_replace(student_id, data)
        if operation == "note.move":
            return _exec_note_move(student_id, data)
        if operation == "note.set_review":
            return _exec_note_set_review(student_id, data)
        if operation == "note.restore_revision":
            return _exec_note_restore_revision(student_id, data)
        if operation == "goal.create":
            return _exec_goal_create(student_id, data, main_loop=main_loop)
        if operation == "goal.update":
            return _exec_goal_update(student_id, data, main_loop=main_loop)
        if operation == "plan.regenerate":
            return _exec_plan_regenerate(student_id, action, data)
        if operation == "task.update":
            return _exec_task_update(student_id, data)
        if operation == "subtask.create":
            return _exec_subtask_create(student_id, data)
        # -- B07：测评 / 评价 / 教学指导 ----------------------------------------
        if operation == "assessment.start":
            return _exec_assessment_start(student_id, data,
                                          main_loop=main_loop)
        if operation == "assessment.practice":
            return _exec_assessment_practice(student_id, data,
                                             main_loop=main_loop)
        if operation == "evaluation.request_review":
            return _exec_evaluation_request_review(student_id, data,
                                                   main_loop=main_loop)
        if operation == "evaluation.retry":
            return _exec_evaluation_retry(student_id, data,
                                          main_loop=main_loop)
        if operation == "evaluation.synthesize":
            return _exec_evaluation_synthesize(student_id, data,
                                               main_loop=main_loop)
        if operation == "teaching.approve":
            return _exec_teaching_approve(student_id, data)
        if operation == "teaching.apply":
            return _exec_teaching_apply(student_id, data)
        if operation == "teaching.revoke":
            return _exec_teaching_revoke(student_id, data)
        # -- B09：画像 / 记忆 / 助手偏好 -----------------------------------------
        if operation == "memory.set_window":
            return _exec_memory_set_window(student_id, data)
        if operation == "profile.update":
            return _exec_profile_update(student_id, data)
        if operation == "assistant.preferences":
            return _exec_assistant_preferences(student_id, data)
        # -- B10：课程高级动作 ----------------------------------------------------
        if operation == "lesson.generate":
            return _exec_lesson_generate(student_id, action, data)
        if operation == "lesson.retry":
            return _exec_lesson_job(student_id, data, retry=True)
        if operation == "lesson.cancel":
            return _exec_lesson_job(student_id, data, retry=False)
        if operation == "lesson.export":
            return _exec_lesson_export(student_id, action, data)
    except ActionRejected:
        raise
    except FileNotFoundError as exc:
        raise ActionRejected(404, "entity_not_found", str(exc)[:120] or "目标不存在。")
    except FileExistsError as exc:
        raise ActionRejected(409, "target_changed",
                             str(exc)[:120] or "同名实体已存在。")
    except PermissionError as exc:
        raise ActionRejected(403, "forbidden", str(exc)[:120] or "无权操作。")
    except ValueError as exc:
        raise ActionRejected(422, "invalid_target", str(exc)[:120] or "参数无效。")
    except Exception as exc:  # noqa: BLE001
        raise ActionRejected(503, "storage_unavailable",
                             f"领域写入失败：{str(exc)[:120]}")
    raise ActionRejected(503, "capability_disabled", "该操作尚未开放。")


# -- B05 执行分支 -----------------------------------------------------------

def _exec_workspace_create(student_id: str, action: dict[str, Any],
                           data: dict[str, Any]) -> dict[str, Any]:
    """POST /workspaces 语义；§21.6.1 幂等标记随创建同一次原子写落盘。"""
    from app.core.workspace import (Workspace, save_workspace,
                                    load_workspace, ensure_library_folder)
    request_id = _client_request_id(action, "workspace.create")
    from . import readers
    for summary in readers.owned_workspaces(student_id):
        if summary.get("assistant_request_id") == request_id:
            ws = load_workspace(str(summary.get("workspace_id")))
            return {"kind": "workspace", "entity_id": ws.workspace_id,
                    "related_ids": {}, "result_revision": str(ws.updated_at),
                    "idempotent_reuse": True,
                    "undo": {"workspace_id": ws.workspace_id,
                             "selected_file_ids": list(
                                 ws.selected_file_ids or [])}}
    ws = Workspace(name=str(data.get("name")).strip(),
                   student_id=student_id,
                   assistant_request_id=request_id)
    wid = save_workspace(ws)
    ws = load_workspace(wid)
    ensure_library_folder(ws)
    # P6-C3：来源只保留教材；无效 file_id 与预览一致直接 404（不静默减少）。
    add_ids = [str(f) for f in (data.get("file_ids") or [])]
    if add_ids and set(add_ids) - set(_textbook_names(student_id, add_ids)):
        raise ActionRejected(404, "entity_not_found",
                             "file_ids 中存在无法访问的教材。")
    ws.selected_folder_ids = []
    ws.selected_file_ids = list(dict.fromkeys(add_ids))
    save_workspace(ws)
    return {"kind": "workspace", "entity_id": wid,
            "related_ids": {}, "result_revision": str(ws.updated_at),
            # §21.5：无会话且选入集合未被改动时可撤销（归档整个辅导区）。
            "undo": {"workspace_id": wid,
                     "selected_file_ids": list(ws.selected_file_ids)}}


def _exec_workspace_update_sources(student_id: str, action: dict[str, Any],
                                   data: dict[str, Any]) -> dict[str, Any]:
    """PATCH /workspaces/{id} 语义：服务端基于最新集合合并（§21.3）。"""
    ws = _owned_workspace(student_id, str(data.get("workspace_id")))
    add_ids = [str(f) for f in (data.get("add_file_ids") or [])]
    remove = {str(f) for f in (data.get("remove_file_ids") or [])}
    valid_add = [f for f in add_ids if _textbook_names(student_id, [f])]
    if set(add_ids) - set(valid_add):
        # 归属校验：加入选集合的必须是本人或公用教材（404，不静默吞错）。
        raise ActionRejected(404, "entity_not_found",
                             "add_file_ids 中存在无法访问的教材。")
    merged = [f for f in (ws.selected_file_ids or []) if f not in remove]
    merged += [f for f in valid_add if f not in merged]
    # §21.5：记录本动作实际施加的增量；撤销只回滚自己的部分，不覆盖并发。
    added = [f for f in valid_add if f in merged]
    removed = [f for f in (ws.selected_file_ids or []) if f in remove]
    ws.selected_folder_ids = []
    ws.selected_file_ids = merged
    from app.core.workspace import save_workspace
    save_workspace(ws)
    if remove or valid_add:
        _notify_scope_change(student_id, str(ws.workspace_id))
    return {"kind": "workspace", "entity_id": str(ws.workspace_id),
            "related_ids": {}, "result_revision": str(ws.updated_at),
            "undo": {"workspace_id": str(ws.workspace_id),
                     "added": added, "removed": removed,
                     "result_updated_at": str(ws.updated_at)}}


def _notify_scope_change(student_id: str, ws_id: str) -> None:
    """R07 选卷变化接评价 lifecycle；失败不阻断（与路由一致）。"""
    try:
        from app.agents.student_model.evaluation import lifecycle
        lifecycle.on_scope_change(student_id, ws_id, change="selection_updated")
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "on_scope_change failed for %s", ws_id, exc_info=True)


def _exec_chat_move_workspace(student_id: str, action: dict[str, Any],
                              data: dict[str, Any], *, main_loop) -> dict[str, Any]:
    """POST /workspaces/{id}/sessions 语义。"""
    session = _owned_session(student_id, str(data.get("session_id")))
    ws = _owned_workspace(student_id, str(data.get("workspace_id")))
    previous_ws = str(session.workspace_id or "")
    from app.core.workspace import add_session_to_workspace
    from app.core.session import save_session
    if add_session_to_workspace(str(ws.workspace_id),
                                str(session.session_id)) is None:
        raise ActionRejected(404, "entity_not_found", "辅导区不存在。")
    session.workspace_id = str(ws.workspace_id)
    save_session(session)
    _refresh_workspace_memory(main_loop, str(ws.workspace_id),
                              str(session.session_id))
    return {"kind": "chat", "entity_id": str(session.session_id),
            "related_ids": {"workspace_id": str(ws.workspace_id)},
            "result_revision": str(ws.workspace_id),
            # §21.5：撤销把对话移回原辅导区（空串 = 未分组）。
            "undo": {"session_id": str(session.session_id),
                     "previous_workspace_id": previous_ws}}


def _exec_chat_archive(student_id: str, action: dict[str, Any],
                       data: dict[str, Any]) -> dict[str, Any]:
    """DELETE /chat/sessions/{id} 语义：归档而非删除（可恢复）。"""
    session = _owned_session(student_id, str(data.get("session_id")))
    from app.core.trash import archive_session
    try:
        item = archive_session(student_id, str(session.session_id))
    except FileNotFoundError:
        raise ActionRejected(404, "entity_not_found", "会话不存在。")
    return {"kind": "trash_item", "entity_id": str(item.get("id")),
            "related_ids": {"session_id": str(session.session_id)},
            "result_revision": str(item.get("deleted_at", "")),
            # §21.5：归档撤销调用真实 restore。
            "undo": {"trash_item": str(item.get("id"))}}


def _exec_library_create_folder(student_id: str, action: dict[str, Any],
                                data: dict[str, Any]) -> dict[str, Any]:
    """POST /library/folders 语义；幂等标记写入文件夹记录（同一次保存）。"""
    from app.core.library import load_library, save_library
    request_id = _client_request_id(action, "library.create_folder")
    lib = load_library(student_id)
    existing = next((f for f in lib.folders
                     if f.get("assistant_request_id") == request_id), None)
    if existing is not None:
        return {"kind": "library_folder", "entity_id": str(existing.get("id")),
                "related_ids": {}, "result_revision": "reused",
                "idempotent_reuse": True,
                "undo": {"folder_id": str(existing.get("id"))}}
    folder = lib.create_folder(str(data.get("name")))
    folder["assistant_request_id"] = request_id
    save_library(lib)
    return {"kind": "library_folder", "entity_id": str(folder.get("id")),
            "related_ids": {}, "result_revision": str(folder.get("updated_at")),
            # §21.5：文件夹仍为空时可撤销（删除不移动任何文件）。
            "undo": {"folder_id": str(folder.get("id"))}}


def _exec_library_rename_file(student_id: str,
                              data: dict[str, Any]) -> dict[str, Any]:
    """PATCH /library/files/{id} 语义。"""
    from app.core.library import load_library, save_library
    lib = load_library(student_id)
    f = lib.find_file(str(data.get("file_id")))
    if f is None:
        raise ActionRejected(404, "entity_not_found", "文件不存在。")
    if f.get("folder_id"):
        folder = lib.find_folder(str(f.get("folder_id")))
        if folder and folder.get("workspace_id"):
            raise ActionRejected(409, "invalid_target",
                                 "工作区专属文件请通过工作区设置管理。")
    previous_filename = str(f.get("filename") or "")
    if not lib.rename_file(str(data.get("file_id")),
                           str(data.get("filename"))):
        raise ActionRejected(422, "invalid_target", "文件名不能为空。")
    save_library(lib)
    return {"kind": "library_file", "entity_id": str(f.get("id")),
            "related_ids": {}, "result_revision": str(f.get("updated_at")),
            # §21.5：文件名仍是本动作结果时可改回旧名。
            "undo": {"file_id": str(f.get("id")),
                     "previous_filename": previous_filename}}


def _exec_library_move_file(student_id: str,
                            data: dict[str, Any]) -> dict[str, Any]:
    """POST /library/files/{id}/move 语义。"""
    from app.core.library import load_library, save_library, file_scope
    lib = load_library(student_id)
    f = lib.find_file(str(data.get("file_id")))
    if f is None:
        raise ActionRejected(404, "entity_not_found", "文件不存在。")
    target = str(data.get("folder_id") or "")
    if target and lib.find_folder(target) is None:
        raise ActionRejected(404, "entity_not_found", "目标文件夹不存在。")
    previous_folder = str(f.get("folder_id") or "")
    old_scope = file_scope(f)
    lib.move_file(str(data.get("file_id")), target)
    save_library(lib)
    new_scope = file_scope(f)
    if new_scope != old_scope:
        # 向量重挂载为 api 模块单一来源的尽力而为逻辑，这里复用不复制。
        try:
            from app.api.v1.library import _rescope_vectors
            _rescope_vectors(lib, str(data.get("file_id")), new_scope)
        except Exception:  # noqa: BLE001
            pass
    return {"kind": "library_file", "entity_id": str(f.get("id")),
            "related_ids": {"folder_id": target},
            "result_revision": str(f.get("updated_at")),
            # §21.5：文件仍在目标文件夹时移回原位置。
            "undo": {"file_id": str(f.get("id")),
                     "previous_folder_id": previous_folder}}


def _exec_textbook_cancel(student_id: str, data: dict[str, Any], *,
                          is_admin: bool) -> dict[str, Any]:
    """POST /textbooks/{id}/cancel 语义：合作式取消（复用路由核心）。"""
    _tb, owner_sid = _owned_textbook(
        student_id, str(data.get("textbook_id")), is_admin=is_admin)
    # 领域编排（合作式取消结算）单一来源位于 api 模块，复用而非复制。
    from app.api.v1.textbook import _cancel_parse_core
    final = _cancel_parse_core(owner_sid, str(data.get("textbook_id")))
    return {"kind": "textbook", "entity_id": str(data.get("textbook_id")),
            "related_ids": {}, "result_revision": str(final)}


def _exec_textbook_rebuild(student_id: str, data: dict[str, Any], *,
                           is_admin: bool, main_loop) -> dict[str, Any]:
    """POST /textbooks/{id}/rebuild_graph 语义（review_required）。"""
    _tb, owner_sid = _owned_textbook(
        student_id, str(data.get("textbook_id")), is_admin=is_admin)
    mode = str(data.get("mode") or "rag_graph")
    # _start_rebuild 经 _spawn_refresh 需在事件循环线程执行（构建队列），
    # execute 在 to_thread 工作线程 → 桥接到主循环。
    from app.api.v1.textbook import _start_rebuild
    result = _run_on_loop(main_loop, _start_rebuild,
                          owner_sid, str(data.get("textbook_id")), mode)
    return {"kind": "textbook_job", "entity_id": str(data.get("textbook_id")),
            "related_ids": {"mode": mode},
            "result_revision": str(result.get("status", "building"))}


def _exec_archive_restore(student_id: str,
                          data: dict[str, Any]) -> dict[str, Any]:
    """POST /trash/{id}/restore 语义：冲突 409，原位置缺失由用户选目标。"""
    from app.core import trash as trash_store
    manifest = trash_store.get_item(student_id, str(data.get("item_id")))
    if manifest is None:
        raise ActionRejected(404, "entity_not_found", "归档不存在。")
    try:
        trash_store.restore_item(
            student_id, str(data.get("item_id")),
            workspace_ids=[str(w) for w in (data.get("workspace_ids") or [])])
    except FileNotFoundError as exc:
        raise ActionRejected(404, "entity_not_found", str(exc)[:120])
    except FileExistsError as exc:
        raise ActionRejected(409, "target_changed",
                             f"恢复冲突：{str(exc)[:100]}")
    except ValueError as exc:
        raise ActionRejected(422, "invalid_target", str(exc)[:120])
    return {"kind": "restore", "entity_id": str(manifest.get("original_id")),
            "related_ids": {"trash_item": str(manifest.get("id")),
                            "resource_type": str(manifest.get("resource_type"))},
            "result_revision": "restored",
            # §21.5：恢复的撤销 = 重新归档该实体（生成新归档条目）。
            "undo": {"resource_type": str(manifest.get("resource_type")),
                     "original_id": str(manifest.get("original_id")),
                     "workspace_id": str(
                         (manifest.get("metadata") or {}).get("workspace_id")
                         or "")}}


# -- B06 执行分支 -----------------------------------------------------------

def _run_async_on_loop(main_loop, coro_fn, *args, timeout: float = 60.0):
    """把异步领域编排提交到主事件循环并等待（无循环时本线程运行）。"""
    import asyncio
    if main_loop is not None and main_loop.is_running():
        return asyncio.run_coroutine_threadsafe(
            coro_fn(*args), main_loop).result(timeout=timeout)
    return asyncio.run(coro_fn(*args))


def _exec_note_append(student_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """PUT /notes/{id} 共用保存服务语义：精确 revision 附加（§21.3）。"""
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    note_id = str(data.get("note_id"))
    if vault.find_note(note_id) is None:
        raise ActionRejected(404, "entity_not_found", "笔记不存在。")
    base = int(data.get("base_revision") or 0)
    previous = vault.read_note(note_id)
    addition = str(data.get("append_markdown")).strip()
    combined = (previous.rstrip() + "\n\n" + addition) if previous.strip() \
        else addition
    try:
        meta = vault.write_note(note_id, combined, author="user",
                                base_revision=base,
                                summary="追加内容（学习助手）")
    except notes_store.StaleRevisionError:
        raise ActionRejected(409, "revision_conflict",
                             "笔记已被编辑，请打开最新内容后再追加。")
    notes_store.save_vault(vault)
    return {"kind": "note", "entity_id": note_id, "related_ids": {},
            "result_revision": str(meta.get("revision")),
            # §21.5：撤销 = 恢复 base_revision 版本（历史里有精确原文）。
            "undo": {"note_id": note_id, "base_revision": base,
                     "result_revision": int(meta.get("revision") or 0)}}


def _exec_note_replace(student_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """note.replace：整体改写（review_required）；历史保留旧版本。"""
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    note_id = str(data.get("note_id"))
    if vault.find_note(note_id) is None:
        raise ActionRejected(404, "entity_not_found", "笔记不存在。")
    base = int(data.get("base_revision") or 0)
    new_title = str(data.get("title") or "").strip()
    try:
        meta = vault.write_note(note_id, str(data.get("content")),
                                author="user", base_revision=base,
                                summary=str(data.get("summary") or "")
                                or "改写笔记（学习助手，经预览确认）")
        if new_title and new_title != str(meta.get("title") or ""):
            vault.rename_note(note_id, new_title)
    except notes_store.StaleRevisionError:
        raise ActionRejected(409, "revision_conflict",
                             "笔记已被编辑，请查看最新差异。")
    notes_store.save_vault(vault)
    return {"kind": "note", "entity_id": note_id, "related_ids": {},
            "result_revision": str(meta.get("revision")),
            "undo": {"note_id": note_id, "base_revision": base,
                     "result_revision": int(meta.get("revision") or 0)}}


def _exec_note_move(student_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """POST /notes/bulk/move 语义（≤20 篇；文件夹缺失 404）。"""
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    folder_id = str(data.get("folder_id") or "")
    if folder_id and vault.find_folder(folder_id) is None:
        raise ActionRejected(404, "entity_not_found", "目标文件夹不存在。")
    moves: list[dict[str, str]] = []
    for nid in [str(n) for n in (data.get("note_ids") or [])]:
        meta = vault.find_note(nid)
        if meta is None:
            raise ActionRejected(404, "entity_not_found",
                                 f"笔记 {nid} 不存在。")
        moves.append({"note_id": nid,
                      "previous_folder_id": str(meta.get("folder_id") or "")})
        vault.move_note(nid, folder_id)
    notes_store.save_vault(vault)
    return {"kind": "note", "entity_id": moves[0]["note_id"] if moves else "",
            "related_ids": {}, "result_revision": folder_id or "unfiled",
            "undo": {"moves": moves}}


def _exec_note_set_review(student_id: str,
                          data: dict[str, Any]) -> dict[str, Any]:
    """PATCH /notes/{id} 的 review_enabled 分支；不生成复习记录。"""
    from app.core import notes as notes_store
    from app.api.v1.notes import _drop_review_card, _sync_review_card
    vault = notes_store.load_vault(student_id)
    note_id = str(data.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise ActionRejected(404, "entity_not_found", "笔记不存在。")
    review = dict(meta.get("review") or {})
    previous_enabled = bool(review.get("enabled"))
    enabled = bool(data.get("enabled"))
    review["enabled"] = enabled
    if enabled:
        _sync_review_card(student_id, meta)
    else:
        review["next_review_at"] = 0.0
        _drop_review_card(student_id, note_id)
    meta["review"] = review
    meta["updated_at"] = notes_store._now()
    notes_store.save_vault(vault)
    return {"kind": "note", "entity_id": note_id, "related_ids": {},
            "result_revision": "1" if enabled else "0",
            "undo": {"note_id": note_id,
                     "previous_enabled": previous_enabled}}


def _exec_note_restore_revision(student_id: str,
                                data: dict[str, Any]) -> dict[str, Any]:
    """POST /notes/{id}/revisions/{r}/restore 语义（review_required）。"""
    from app.core import notes as notes_store
    from app.api.v1.notes import _sync_review_card
    vault = notes_store.load_vault(student_id)
    note_id = str(data.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise ActionRejected(404, "entity_not_found", "笔记不存在。")
    current = int(meta.get("revision") or 1)
    if current != int(data.get("expected_current_revision") or 0):
        raise ActionRejected(409, "revision_conflict",
                             "笔记此后已被编辑，请查看最新版本。")
    try:
        meta = vault.restore_revision(note_id, int(data.get("revision") or 0))
    except FileNotFoundError:
        raise ActionRejected(404, "entity_not_found", "版本不存在。")
    _sync_review_card(student_id, meta)
    notes_store.save_vault(vault)
    return {"kind": "note", "entity_id": note_id, "related_ids": {},
            "result_revision": str(meta.get("revision")),
            "undo": {"note_id": note_id, "restored_from_current": current,
                     "result_revision": int(meta.get("revision") or 0)}}


def _exec_goal_create(student_id: str, data: dict[str, Any],
                      *, main_loop) -> dict[str, Any]:
    """POST /orchestration/goal 语义：创建 + 立即重规划（§21.3 说明义务）。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    svc = get_orchestration_service()
    try:
        goal = svc.add_goal(
            student_id, title=str(data.get("title")),
            description=str(data.get("description") or ""),
            goal_type=str(data.get("goal_type") or "ability"),
            subjects=list(data.get("subjects") or []),
            target_concept_ids=list(data.get("target_concept_ids") or []),
            workspace_id=str(data.get("workspace_id") or ""),
            deadline=float(data.get("deadline") or 0.0))
    except ValueError as exc:
        raise ActionRejected(422, "invalid_target", str(exc)[:120])
    ok, _reason = _run_async_on_loop(
        main_loop, lambda: svc.regenerate_plan(student_id))
    return {"kind": "goal", "entity_id": str(goal.id), "related_ids": {},
            "result_revision": "planned" if ok else "created"}


def _exec_goal_update(student_id: str, data: dict[str, Any],
                      *, main_loop) -> dict[str, Any]:
    """PATCH /orchestration/goal/{id} 语义：更新 + 重规划（user 内容保留）。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    svc = get_orchestration_service()
    ok = svc.update_goal(
        student_id, str(data.get("goal_id")),
        title=data.get("title"), description=data.get("description"),
        goal_type=data.get("goal_type"), subjects=data.get("subjects"),
        target_concept_ids=data.get("target_concept_ids"),
        workspace_id=data.get("workspace_id"), deadline=data.get("deadline"))
    if not ok:
        raise ActionRejected(404, "entity_not_found", "目标不存在。")
    _run_async_on_loop(main_loop, lambda: svc.regenerate_plan(student_id))
    return {"kind": "goal", "entity_id": str(data.get("goal_id")),
            "related_ids": {}, "result_revision": "planned"}


def _exec_plan_regenerate(student_id: str, action: dict[str, Any],
                          data: dict[str, Any]) -> dict[str, Any]:
    """§21.6.3 阶段二：逐字应用预览绑定的候选；无二次生成。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    preview = action.get("preview") or {}
    candidate_id = str(preview.get("candidate_id") or "")
    if not candidate_id:
        raise ActionRejected(409, "preview_stale",
                             "缺少计划候选，请重新查看变更。")
    ok, reason = get_orchestration_service().commit_plan_candidate(
        student_id, candidate_id)
    if not ok:
        raise ActionRejected(
            409, "preview_stale",
            {"candidate_expired": "计划候选已过期，请重新查看变更。",
             "goals_changed": "目标已变化，请重新查看变更。"}.get(
                reason, "计划候选失效，请重新预览。"))
    return {"kind": "none", "entity_id": None,
            "related_ids": {"candidate_id": candidate_id},
            "result_revision": candidate_id}


def _exec_task_update(student_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """PATCH /orchestration/task/{id} 语义：只写明确给出的字段。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    task = _owned_task(student_id, str(data.get("task_id")))
    fields = ("title", "day", "kind", "phase", "estimate_minutes",
              "priority", "milestone_id")
    kwargs = {k: data[k] for k in fields if data.get(k) is not None}
    previous = {"title": str(task.title), "day": str(task.day),
                "kind": str(getattr(task.kind, "value", task.kind)),
                "estimate_minutes": int(task.estimate_minutes),
                "priority": int(task.priority)}
    try:
        ok = get_orchestration_service().update_task(
            student_id, str(data.get("task_id")), **kwargs)
    except ValueError as exc:
        raise ActionRejected(422, "invalid_target", str(exc)[:120])
    if not ok:
        raise ActionRejected(404, "entity_not_found", "任务不存在。")
    return {"kind": "task", "entity_id": str(task.id), "related_ids": {},
            "result_revision": str(kwargs.get("day") or task.day),
            "undo": {"task_id": str(task.id), "previous": previous,
                     "applied": {k: str(v) for k, v in kwargs.items()}}}


def _exec_subtask_create(student_id: str,
                         data: dict[str, Any]) -> dict[str, Any]:
    """POST /week/{w}/task/{t}/subtask 语义（source=user，随周保留）。"""
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    try:
        sub = get_orchestration_service().add_subtask(
            student_id, int(data.get("week_index") or 0),
            str(data.get("week_task_id")), title=str(data.get("title")),
            estimate_minutes=int(data.get("estimate_minutes") or 15),
            source="user")
    except ValueError as exc:
        raise ActionRejected(422, "invalid_target", str(exc)[:120])
    if sub is None:
        raise ActionRejected(404, "entity_not_found", "周任务不存在。")
    return {"kind": "task", "entity_id": str(sub.id), "related_ids": {},
            "result_revision": str(data.get("week_task_id")),
            "undo": {"week_index": int(data.get("week_index") or 0),
                     "week_task_id": str(data.get("week_task_id")),
                     "subtask_id": str(sub.id),
                     "title": str(sub.title)}}


# -- B07 执行分支（复用领域路由核心；异步编排经主循环桥接） -------------------

def _exec_assessment_start(student_id: str, data: dict[str, Any],
                           *, main_loop) -> dict[str, Any]:
    """POST /assessment/start 语义：复用路由核心（review_required）。"""
    import app.api.v1.assessment as assessment_api
    req = assessment_api.CatStartRequest(
        workspace_id=str(data.get("workspace_id") or ""),
        concept_keys=[str(k) for k in (data.get("concept_keys") or [])],
        count=int(data.get("count") or 1),
        q_type=str(data.get("q_type") or ""),
        grade=str(data.get("grade") or "本科"),
        subject=str(data.get("subject") or ""),
        expected_scope_revision=str(
            data.get("expected_scope_revision") or ""))
    from fastapi import HTTPException
    try:
        out = _run_async_on_loop(
            main_loop, lambda: assessment_api.start_cat(req, student_id))
    except HTTPException as exc:
        raise _http_to_rejected(exc)
    aid = str(out.get("assessment_id") or out.get("instance_id") or "")
    return {"kind": "assessment", "entity_id": aid, "related_ids": {},
            "result_revision": "started"}


def _exec_assessment_practice(student_id: str, data: dict[str, Any],
                              *, main_loop) -> dict[str, Any]:
    """POST /assessment/questions/{qid}/practice 语义（review_required）。"""
    import app.api.v1.assessment as assessment_api
    req = assessment_api.PracticeRequest(
        question_revision=int(data.get("question_revision") or 1),
        mode=str(data.get("mode") or "same"),
        expected_scope_revision=str(
            data.get("expected_scope_revision") or ""))
    from fastapi import HTTPException
    try:
        out = _run_async_on_loop(
            main_loop,
            lambda: assessment_api.question_practice(
                str(data.get("question_id")), req, student_id))
    except HTTPException as exc:
        raise _http_to_rejected(exc)
    question_out = out.get("question")
    revision_out = ""
    if isinstance(question_out, dict):
        revision_out = str(question_out.get("question_revision") or "")
    else:
        revision_out = str(getattr(question_out, "question_revision", "")
                           or "")
    return {"kind": "assessment",
            "entity_id": str(data.get("question_id")),
            "related_ids": {"mode": str(data.get("mode"))},
            "result_revision": revision_out or "created"}


def _exec_evaluation_request_review(student_id: str, data: dict[str, Any],
                                    *, main_loop) -> dict[str, Any]:
    """POST /learner-evaluation/evidence/{id}/reviews 语义（R06）。"""
    import app.api.v1.learner_evaluation as le_api
    req = le_api.ReviewCreateRequest(
        interpretation_id=str(data.get("interpretation_id")),
        reason=str(data.get("reason")),
        issue_kind=str(data.get("issue_kind") or "other"),
        expected_revision=int(data.get("expected_revision") or 1))
    from fastapi import HTTPException
    try:
        out = _run_async_on_loop(
            main_loop,
            lambda: le_api.create_review(
                str(data.get("source_id")), req, student_id))
    except HTTPException as exc:
        raise _http_to_rejected(exc)
    return {"kind": "evaluation_job",
            "entity_id": str(out.get("review_id") or ""),
            "related_ids": {"job_id": str(out.get("job_id") or ""),
                            "duplicate": str(bool(out.get("duplicate")))},
            "result_revision": "requested"}


def _exec_evaluation_retry(student_id: str, data: dict[str, Any],
                           *, main_loop) -> dict[str, Any]:
    """POST /learner-evaluation/jobs/{id}/retry 语义：仅 failed。"""
    import app.api.v1.learner_evaluation as le_api
    from fastapi import HTTPException
    try:
        out = _run_async_on_loop(
            main_loop,
            lambda: le_api.retry_job(
                str(data.get("job_id")),
                le_api.JobRetryRequest(), student_id))
    except HTTPException as exc:
        raise _http_to_rejected(exc)
    return {"kind": "evaluation_job",
            "entity_id": str(out.get("job_id") or ""),
            "related_ids": {"parent_job_id": str(
                out.get("parent_job_id") or "")},
            "result_revision": "queued"}


def _exec_evaluation_synthesize(student_id: str, data: dict[str, Any],
                                *, main_loop) -> dict[str, Any]:
    """POST /learner-evaluation/workspaces/{id}/synthesis 语义。"""
    import app.api.v1.learner_evaluation as le_api
    req = le_api.SynthesisRequest(
        expected_scope_revision=str(
            data.get("expected_scope_revision") or ""))
    from fastapi import HTTPException
    try:
        out = _run_async_on_loop(
            main_loop,
            lambda: le_api.request_synthesis(
                str(data.get("workspace_id")), req, student_id))
    except HTTPException as exc:
        raise _http_to_rejected(exc)
    return {"kind": "evaluation_job",
            "entity_id": str(out.get("job_id") or ""),
            "related_ids": {"duplicate": str(bool(out.get("duplicate")))},
            "result_revision": "queued"}


def _exec_teaching_approve(student_id: str,
                           data: dict[str, Any]) -> dict[str, Any]:
    """PATCH /evaluation/proposals/{id} status=approved（不自动应用）。"""
    from app.agents.evaluation import store as eval_store
    proposal = eval_store.load_proposal(
        student_id, str(data.get("proposal_id")))
    if proposal is None:
        raise ActionRejected(404, "entity_not_found", "教学提案不存在。")
    if str(proposal.status) != str(data.get("expected_status") or "proposed"):
        raise ActionRejected(409, "target_changed",
                             f"提案状态已是 {proposal.status}，请刷新。")
    if not eval_store.update_proposal_status(
            student_id, str(data.get("proposal_id")), "approved"):
        raise ActionRejected(409, "target_changed", "批准失败，请刷新重试。")
    return {"kind": "teaching_proposal",
            "entity_id": str(data.get("proposal_id")),
            "related_ids": {}, "result_revision": "approved"}


def _exec_teaching_apply(student_id: str,
                         data: dict[str, Any]) -> dict[str, Any]:
    """PATCH ... status=applied：部署并核对指导实际生效（§21.6.5）。"""
    from app.agents.evaluation import store as eval_store
    from app.agents.teaching_engine import guidance_store
    proposal = eval_store.load_proposal(
        student_id, str(data.get("proposal_id")))
    if proposal is None:
        raise ActionRejected(404, "entity_not_found", "教学提案不存在。")
    if str(proposal.status) != str(data.get("expected_status") or "approved"):
        raise ActionRejected(409, "target_changed",
                             f"提案状态是 {proposal.status}；需先批准再应用。")
    if not eval_store.update_proposal_status(
            student_id, str(data.get("proposal_id")), "applied"):
        raise ActionRejected(409, "target_changed", "应用失败，请刷新重试。")
    if not proposal.guidance:
        # 无指导文本的旧格式提案：状态置 applied 即完成（同原路由语义）。
        return {"kind": "teaching_proposal",
                "entity_id": str(data.get("proposal_id")),
                "related_ids": {}, "result_revision": "applied"}
    deployed = guidance_store.apply_guidance(
        student_id, guidance_store.GuidanceEntry(
            source_proposal=proposal.id, title=proposal.title,
            applicability=proposal.applicability,
            guidance=proposal.guidance,
            cautions=list(proposal.cautions),
            confidence=proposal.confidence))
    # FULL-14：核对指导真实存在且 active，不以状态字符串冒充生效。
    active = any(
        e.source_proposal == str(data.get("proposal_id")) and e.active
        for e in guidance_store.load_all(student_id))
    if deployed and active:
        return {"kind": "teaching_proposal",
                "entity_id": str(data.get("proposal_id")),
                "related_ids": {}, "result_revision": "applied_active"}
    # 部分成功：状态已 applied 但指导未生效 → needs_attention 语义，
    # 允许幂等补部署（重复 apply_guidance 写同一 source_proposal）。
    raise ActionRejected(503, "storage_unavailable",
                         "提案已标记应用，但指导部署未生效；可重试补部署。",
                         retryable=True)


def _exec_teaching_revoke(student_id: str,
                          data: dict[str, Any]) -> dict[str, Any]:
    """DELETE /evaluation/guidance/{id} 语义：即时停用，审计保留。"""
    from app.agents.teaching_engine import guidance_store
    entry = next((e for e in guidance_store.load_all(student_id)
                  if e.id == str(data.get("guidance_id"))), None)
    if entry is None:
        raise ActionRejected(404, "entity_not_found", "教学指导不存在。")
    if not entry.active:
        raise ActionRejected(409, "target_changed",
                             "该指导已撤销，请刷新。")
    if not guidance_store.revoke_guidance(
            student_id, str(data.get("guidance_id"))):
        raise ActionRejected(409, "target_changed", "撤销失败，请重试。")
    return {"kind": "teaching_proposal",
            "entity_id": str(data.get("guidance_id")),
            "related_ids": {}, "result_revision": "revoked"}


# -- B09 执行分支 -----------------------------------------------------------

def _exec_memory_set_window(student_id: str,
                            data: dict[str, Any]) -> dict[str, Any]:
    """PUT /memory/prompt-profile/window 语义（域内 clamp 5..max）。"""
    from app.agents.memory.prompt_memory import get_user_window, set_user_window
    previous = int(get_user_window(student_id))
    value = set_user_window(student_id, int(data.get("window")))
    return {"kind": "none", "entity_id": None, "related_ids": {},
            "result_revision": str(value),
            "undo": {"previous_window": previous}}


def _exec_profile_update(student_id: str,
                         data: dict[str, Any]) -> dict[str, Any]:
    """PUT /user/profile 白名单分支；学段同步沿用原服务。"""
    from app.identity import store as id_store
    from app.identity.store import update_profile_fields
    user = id_store.get_by_id(student_id)
    if user is None:
        raise ActionRejected(404, "entity_not_found", "账号不存在。")
    previous = {
        "name": str(user.profile.name or ""),
        "grade": str(user.profile.grade or ""),
        "school": str(user.profile.school or ""),
        "subjects": [str(s) for s in (user.profile.subjects or [])],
    }
    fields = {k: data[k] for k in ("name", "grade", "school", "subjects")
              if data.get(k) is not None}
    try:
        update_profile_fields(student_id, fields)
    except ValueError as exc:
        raise ActionRejected(404, "entity_not_found", str(exc)[:120])
    if "grade" in fields:
        # 与原路由一致：学段同步进 StudentModel 讲解基线（尽力而为）。
        try:
            from app.api.v1.user import _sync_grade_to_student_model
            refreshed = id_store.get_by_id(student_id)
            if refreshed is not None:
                _sync_grade_to_student_model(refreshed)
        except Exception:  # noqa: BLE001
            pass
    return {"kind": "profile", "entity_id": student_id, "related_ids": {},
            "result_revision": "updated",
            "undo": {"previous": {k: str(v) if not isinstance(v, list)
                                  else v for k, v in previous.items()},
                     "applied": {k: str(v) for k, v in fields.items()}}}


def _exec_assistant_preferences(student_id: str,
                                data: dict[str, Any]) -> dict[str, Any]:
    """PUT /assistant/preferences 白名单合并（§22.4/§24.6）。"""
    from app.core.assistant_store import (AssistantStoreError,
                                          load_preferences, save_preferences)
    previous = load_preferences(student_id)
    updates = {k: data[k] for k in (
        "tone", "response_length", "default_scope", "voice_policy",
        "voice_id", "auto_read", "send_after_recording",
        "voice_input_mode", "playback_rate", "volume",
        "allow_local_fallback", "proactive_enabled")
        if data.get(k) is not None}
    try:
        merged = save_preferences(student_id, updates)
    except AssistantStoreError as exc:
        if exc.code == "conflict":
            raise ActionRejected(409, "target_changed", str(exc))
        raise ActionRejected(503, "storage_unavailable", str(exc))
    return {"kind": "none", "entity_id": None, "related_ids": {},
            "result_revision": str(merged.get("revision") or ""),
            "undo": {"previous": {k: str(v) for k, v in previous.items()
                                  if k != "revision"},
                     "applied": {k: str(v) for k, v in updates.items()}}}


# -- B10 执行分支（复用现有课堂领域 job；构图不由助手参数化） -----------------

def _classroom_job_snapshot(student_id: str, workspace_id: str,
                            lesson_id: str, job_id: str) -> dict[str, Any]:
    from app.classroom import service as classroom_service
    from app.classroom.errors import ClassroomError
    try:
        return classroom_service.job_snapshot(
            student_id, workspace_id, lesson_id, job_id)
    except ClassroomError as exc:
        status = int(getattr(exc, "http_status", 0) or 0)
        code = str(getattr(exc, "code", "") or "classroom_error")
        if code in ("source_not_found", "classroom_disabled"):
            raise ActionRejected(
                404 if code == "source_not_found" else 503,
                "entity_not_found" if code == "source_not_found"
                else "capability_disabled", str(exc)[:120])
        raise ActionRejected(status or 409, "target_changed", str(exc)[:120])


def _exec_lesson_generate(student_id: str, action: dict[str, Any],
                          data: dict[str, Any]) -> dict[str, Any]:
    """POST /workspaces/{id}/classroom/lessons 语义（§21.3）。

    幂等键由 action_id 派生（A13 同一模式）：同动作重试复用同一生成
    任务，绝不重复开任务或重复扣额。
    """
    from app.classroom import service as classroom_service
    from app.classroom.errors import ClassroomError
    from app.schemas import classroom as sc
    brief = sc.LessonBrief(
        topic=str(data.get("topic")),
        goals=[str(g) for g in (data.get("goals") or [])],
        duration_minutes=int(data.get("duration_minutes") or 15),
        grade=str(data.get("grade") or ""),
        language=str(data.get("language") or "zh"),
        source_selection=sc.SourceSelection(files=[
            sc.SourceFileSelection(file_id=str(fid))
            for fid in (data.get("source_file_ids") or [])]),
    )
    request = sc.CreateLessonRequest(
        brief=brief, start_mode=str(data.get("start_mode") or "automatic"))
    idempotency_key = _client_request_id(action, "lesson.generate")
    try:
        out = classroom_service.create_lesson(
            student_id, str(data.get("workspace_id")), request,
            idempotency_key=idempotency_key)
    except ClassroomError as exc:
        status = int(getattr(exc, "http_status", 0) or 0)
        code = str(getattr(exc, "code", "") or "classroom_error")
        if code == "classroom_disabled":
            raise ActionRejected(503, "capability_disabled", str(exc)[:120])
        if code == "idempotency_conflict":
            raise ActionRejected(409, "target_changed", str(exc)[:120])
        if code == "source_not_found":
            raise ActionRejected(404, "entity_not_found", str(exc)[:120])
        raise ActionRejected(status or 422, "invalid_target", str(exc)[:120])
    return {"kind": "classroom_job", "entity_id": str(out.get("job_id")),
            "related_ids": {"lesson_id": str(out.get("lesson_id"))},
            "result_revision": str(out.get("target_revision") or "")}


def _exec_lesson_job(student_id: str, data: dict[str, Any],
                     *, retry: bool) -> dict[str, Any]:
    """现有课堂 job retry/cancel 服务（保留产物/协作式取消）。"""
    from app.classroom import service as classroom_service
    from app.classroom.errors import ClassroomError
    try:
        if retry:
            out = classroom_service.retry_job(
                student_id, str(data.get("workspace_id")),
                str(data.get("lesson_id")), str(data.get("job_id")),
                int(data.get("expected_state_revision") or 0))
        else:
            out = classroom_service.cancel_job(
                student_id, str(data.get("workspace_id")),
                str(data.get("lesson_id")), str(data.get("job_id")),
                int(data.get("expected_state_revision") or 0))
    except ClassroomError as exc:
        code = str(getattr(exc, "code", "") or "classroom_error")
        status = int(getattr(exc, "http_status", 0) or 0)
        if code in ("source_not_found", "classroom_disabled"):
            raise ActionRejected(
                404 if code == "source_not_found" else 503,
                "entity_not_found" if code == "source_not_found"
                else "capability_disabled", str(exc)[:120])
        if code in ("state_conflict", "content_invalid"):
            raise ActionRejected(409, "target_changed", str(exc)[:120])
        raise ActionRejected(status or 409, "target_changed", str(exc)[:120])
    return {"kind": "classroom_job", "entity_id": str(data.get("job_id")),
            "related_ids": {"lesson_id": str(data.get("lesson_id"))},
            "result_revision": str(out.get("state_revision") or "")}


def _exec_lesson_export(student_id: str, action: dict[str, Any],
                        data: dict[str, Any]) -> dict[str, Any]:
    """现有课堂 export 服务；成功给下载链接，不自动触发下载。"""
    from app.classroom import service as classroom_service
    from app.classroom.errors import ClassroomError
    try:
        out = classroom_service.create_export(
            student_id, str(data.get("workspace_id")),
            str(data.get("lesson_id")), int(data.get("revision") or 0),
            str(data.get("fmt") or "html_zip"),
            idempotency_key=_client_request_id(action, "lesson.export"))
    except ClassroomError as exc:
        code = str(getattr(exc, "code", "") or "classroom_error")
        if code in ("source_not_found",):
            raise ActionRejected(404, "entity_not_found", str(exc)[:120])
        if code == "classroom_disabled":
            raise ActionRejected(503, "capability_disabled", str(exc)[:120])
        raise ActionRejected(422, "invalid_target", str(exc)[:120])
    return {"kind": "classroom_export", "entity_id": str(out.get("export_id")),
            "related_ids": {"lesson_id": str(data.get("lesson_id")),
                            "content_url": str(out.get("content_url") or "")},
            "result_revision": str(out.get("revision") or "")}
