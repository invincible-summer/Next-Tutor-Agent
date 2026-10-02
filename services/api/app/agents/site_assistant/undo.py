"""§21.5 可逆动作撤销：只恢复该动作自己造成的变更。

执行成功时 business_result.undo 记录前值快照（previews 各执行分支）；
撤销窗口默认 10 分钟（UNDO_TTL）。撤销调用真实领域补偿（restore/反向
写），不从缓存回写；目标已被并发修改或超出窗口时返回 409/target_changed，
由用户回原模块按其能力处理，不承诺无条件覆盖。

幂等：同一 client_request_id 重复提交返回同一撤销结果；撤销完成后
action.state 保持 succeeded（§19.3 终态），记录 undo_result 供 UI 呈现
「已撤销」，不伪装成从未执行。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .actions import ActionRejected, _save, locate_action, public_action

UNDO_TTL_SECONDS = 600.0

# 提供真实补偿实现的 operation；REVERSIBLE 为 False 的操作不在表中。
UNDO_OPERATIONS = frozenset({
    "task.create", "note.create", "chat.rename", "schedule.update",
    "workspace.create", "workspace.update_sources", "chat.move_workspace",
    "chat.archive", "library.create_folder", "library.rename_file",
    "library.move_file", "archive.restore",
    # B06：goal/plan 写操作引发重规划，不在助手内整体撤销。
    "note.append", "note.replace", "note.move", "note.set_review",
    "note.restore_revision", "task.update", "subtask.create",
    # B07：测评/评价/教学指导按原模块生命周期处理（§21.5）。
    # B09：偏好/画像写保存前值，可反向恢复。
    "memory.set_window", "profile.update", "assistant.preferences",
})


def _stale(msg: str) -> ActionRejected:
    return ActionRejected(409, "target_changed", msg)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def undo_action(student_id: str, action_id: str, *,
                client_request_id: str,
                expected_result_revision: str,
                is_admin: bool = False) -> dict[str, Any]:
    """POST /assistant/actions/{aid}/undo（§21.4）。

    client_request_id 供客户端重试去重；expected_result_revision 与执行
    回执的 result_revision 必须一致，防止拿旧回执覆盖新状态。
    """
    located = locate_action(student_id, action_id)
    if located is None:
        raise ActionRejected(404, "entity_not_found", "该动作不存在。")
    record, _cid, action = located
    if action.get("state") != "succeeded":
        raise ActionRejected(409, "action_state_invalid",
                             "只有已完成的动作可以撤销。")
    payload = action.get("payload") or {}
    if payload.get("kind") != "domain_write":
        raise ActionRejected(422, "invalid_target",
                             "该动作不提供撤销。")
    operation = str(payload.get("operation") or "")
    if operation not in UNDO_OPERATIONS:
        raise ActionRejected(422, "invalid_target",
                             "该操作不可撤销，请在原模块处理。")
    # 幂等重放：同 client_request_id 返回已记录的撤销结果。
    prior_undo = action.get("undo_result") or {}
    if prior_undo.get("client_request_id") == client_request_id:
        return {"status": "succeeded", "undone": True,
                "reason": str(prior_undo.get("message") or ""),
                "action": public_action(action)}
    if prior_undo:
        raise _stale("该动作已撤销，不能重复撤销。")

    result = action.get("business_result") or {}
    snapshot = result.get("undo") or {}
    if not snapshot:
        raise _stale("该动作缺少撤销快照，请在原模块处理。")
    if str(result.get("result_revision") or "") != str(
            expected_result_revision):
        raise _stale("执行回执版本不一致，请刷新后重试。")

    # 撤销窗口：自执行完成时刻起 UNDO_TTL（§21.5 默认 10 分钟）。
    completed = str((action.get("invocation") or {}).get("completed_at")
                    or "")
    try:
        done_at = datetime.fromisoformat(completed)
        if done_at.tzinfo is None:
            done_at = done_at.replace(tzinfo=timezone.utc)
    except ValueError:
        done_at = None
    if done_at is None or _now() - done_at > timedelta(
            seconds=UNDO_TTL_SECONDS):
        raise ActionRejected(410, "action_expired",
                             "撤销窗口已过，请在原模块按其能力处理。",
                             retryable=False)

    data = payload.get("input") or {}
    handler = _HANDLERS.get(operation)
    assert handler is not None  # UNDO_OPERATIONS 与 _HANDLERS 同步维护
    message = handler(student_id, data, snapshot, is_admin=is_admin)

    undo_record = {
        "client_request_id": client_request_id,
        "undone_at": _now().isoformat(),
        "message": message,
    }
    action["undo_result"] = undo_record
    _save(record, student_id)
    return {"status": "succeeded", "undone": True, "reason": message,
            "action": public_action(action)}


# -- 各 operation 的补偿实现 -----------------------------------------------
# 每个处理器校验「目标仍处于本动作刚执行完的状态」，否则 409 让用户回原
# 模块；补偿成功返回面向用户的一句话说明。

def _undo_task_create(student_id: str, data: dict[str, Any],
                      snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    from app.agents.learning_orchestration.schema import DailyTaskStatus
    service = get_orchestration_service()
    task_id = str(snap.get("task_id"))
    current = next(
        (t for t in service._load(student_id).daily_tasks
         if t.id == task_id), None)
    if current is None or current.status == DailyTaskStatus.COMPLETED:
        raise _stale("任务已被完成或移除，请在学习编排页处理。")
    if str(current.title) != str(snap.get("title")) \
            or str(current.day) != str(snap.get("day")):
        raise _stale("任务创建后被编辑，请在学习编排页处理。")
    if not service.delete_task(student_id, task_id):
        raise _stale("任务删除失败，请在学习编排页处理。")
    return "已删除该任务。"


def _undo_note_create(student_id: str, data: dict[str, Any],
                      snap: dict[str, Any], *, is_admin: bool) -> str:
    import hashlib
    from app.core import notes as notes_store
    from app.core import trash
    vault = notes_store.load_vault(student_id)
    note_id = str(snap.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise _stale("笔记已不存在。")
    current_sha = hashlib.sha256(
        (str(meta.get("title") or "") + "\x00"
         + vault.read_note(note_id)).encode("utf-8")).hexdigest()
    if current_sha != str(snap.get("content_sha")):
        raise _stale("笔记创建后被编辑，请在笔记仓库处理。")
    trash.archive_note(student_id, note_id)
    return "已归档该笔记（可在归档中心恢复）。"


def _undo_chat_rename(student_id: str, data: dict[str, Any],
                      snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core.session import rename_session
    from .previews import _owned_session
    session = _owned_session(student_id, str(snap.get("session_id")))
    if str(session.title or "") != str(data.get("title")):
        raise _stale("对话标题此后被修改，请在原对话处理。")
    rename_session(str(snap.get("session_id")),
                   str(snap.get("previous_title")))
    return "已恢复原对话标题。"


def _undo_schedule_update(student_id: str, data: dict[str, Any],
                          snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    service = get_orchestration_service()
    current = service._load(student_id).schedule.daily_minutes
    if int(current) != int(data.get("daily_minutes")):
        raise _stale("时间预算此后被修改，请在学习编排页处理。")
    if service.update_schedule(
            student_id, daily_minutes=int(snap.get("previous_minutes"))) \
            is None:
        raise ActionRejected(503, "storage_unavailable", "恢复失败，请重试。")
    return f"已恢复每日时间预算 {snap.get('previous_minutes')} 分钟。"


def _undo_workspace_create(student_id: str, data: dict[str, Any],
                           snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core.workspace import load_workspace
    from app.core import trash
    from .previews import _owned_workspace
    ws = _owned_workspace(student_id, str(snap.get("workspace_id")))
    if (ws.session_ids or []) or (ws.selected_file_ids or []) != list(
            snap.get("selected_file_ids") or []):
        raise _stale("辅导区创建后已有会话或教材变更，请在工作区设置处理。")
    trash.archive_workspace(student_id, str(ws.workspace_id))
    return "已归档该辅导区（可在归档中心恢复）。"


def _undo_workspace_update_sources(student_id: str, data: dict[str, Any],
                                   snap: dict[str, Any],
                                   *, is_admin: bool) -> str:
    from app.core.workspace import save_workspace
    from .previews import _owned_workspace, _notify_scope_change
    ws = _owned_workspace(student_id, str(snap.get("workspace_id")))
    # §21.5 版本门：执行后的 updated_at 未变才有资格反向；任何并发写
    # （页面或他人）都使其失效，不无条件覆盖。
    if str(ws.updated_at) != str(snap.get("result_updated_at")):
        raise _stale("教材来源此后已被修改，请在工作区设置处理。")
    added = [str(f) for f in (snap.get("added") or [])]
    removed = [str(f) for f in (snap.get("removed") or [])]
    current = [str(f) for f in (ws.selected_file_ids or [])]
    merged = [f for f in current if f not in set(added)]
    merged += [f for f in removed if f not in merged]
    ws.selected_file_ids = merged
    save_workspace(ws)
    _notify_scope_change(student_id, str(ws.workspace_id))
    return "已恢复辅导区教材来源（只回滚本次变更）。"


def _undo_chat_move_workspace(student_id: str, data: dict[str, Any],
                              snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core.session import save_session
    from app.core.workspace import (add_session_to_workspace,
                                    remove_session_from_workspace)
    from .previews import _owned_session
    session = _owned_session(student_id, str(snap.get("session_id")))
    target = str(data.get("workspace_id"))
    if str(session.workspace_id or "") != target:
        raise _stale("对话此后已被移动，请在工作区处理。")
    previous = str(snap.get("previous_workspace_id") or "")
    if previous:
        # add_session_to_workspace 自动先从当前辅导区移出（单一归属）。
        if add_session_to_workspace(previous, str(session.session_id)) is None:
            raise _stale("原辅导区已不存在，请在工作区选择归属。")
        session.workspace_id = previous
    else:
        remove_session_from_workspace(target, str(session.session_id))
        session.workspace_id = ""
    save_session(session)
    return "已把对话移回原位置。"


def _undo_chat_archive(student_id: str, data: dict[str, Any],
                       snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core import trash
    item_id = str(snap.get("trash_item"))
    if trash.get_item(student_id, item_id) is None:
        raise _stale("归档条目已消费或不存在，请在归档中心处理。")
    try:
        trash.restore_item(student_id, item_id)
    except FileExistsError as exc:
        raise _stale(f"恢复冲突：{str(exc)[:80]}")
    return "已恢复该对话（原归档条目被消费）。"


def _undo_library_create_folder(student_id: str, data: dict[str, Any],
                                snap: dict[str, Any],
                                *, is_admin: bool) -> str:
    from app.core.library import load_library, save_library
    lib = load_library(student_id)
    folder_id = str(snap.get("folder_id"))
    folder = lib.find_folder(folder_id)
    if folder is None:
        raise _stale("文件夹已不存在。")
    if any(f.get("folder_id") == folder_id for f in lib.files) \
            or any(f.get("parent_id") == folder_id for f in lib.folders):
        raise _stale("文件夹已包含文件或子文件夹，请在资料中心处理。")
    lib.delete_folder(folder_id)
    save_library(lib)
    return "已删除该文件夹。"


def _undo_library_rename_file(student_id: str, data: dict[str, Any],
                              snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core.library import load_library, save_library
    lib = load_library(student_id)
    f = lib.find_file(str(snap.get("file_id")))
    if f is None:
        raise _stale("文件已不存在。")
    if str(f.get("filename") or "") != str(data.get("filename")):
        raise _stale("文件名此后被修改，请在资料中心处理。")
    lib.rename_file(str(snap.get("file_id")),
                    str(snap.get("previous_filename")))
    save_library(lib)
    return "已恢复原文件名。"


def _undo_library_move_file(student_id: str, data: dict[str, Any],
                            snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core.library import load_library, save_library, file_scope
    lib = load_library(student_id)
    f = lib.find_file(str(snap.get("file_id")))
    if f is None:
        raise _stale("文件已不存在。")
    target = str(data.get("folder_id") or "")
    if str(f.get("folder_id") or "") != target:
        raise _stale("文件此后已被移动，请在资料中心处理。")
    previous = str(snap.get("previous_folder_id") or "")
    if previous and lib.find_folder(previous) is None:
        raise _stale("原文件夹已不存在，请在资料中心选择位置。")
    old_scope = file_scope(f)
    lib.move_file(str(snap.get("file_id")), previous)
    save_library(lib)
    if file_scope(f) != old_scope:
        try:
            from app.api.v1.library import _rescope_vectors
            _rescope_vectors(lib, str(snap.get("file_id")), file_scope(f))
        except Exception:  # noqa: BLE001
            pass
    return "已把文件移回原位置。"


def _undo_archive_restore(student_id: str, data: dict[str, Any],
                          snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core import trash
    rtype = str(snap.get("resource_type"))
    original_id = str(snap.get("original_id"))
    try:
        if rtype == "session":
            trash.archive_session(student_id, original_id)
        elif rtype == "library_file":
            trash.archive_library_file(student_id, original_id)
        elif rtype == "library_folder":
            trash.archive_library_folder(student_id, original_id)
        elif rtype == "note":
            trash.archive_note(student_id, original_id)
        elif rtype == "textbook":
            trash.archive_textbook(student_id, original_id)
        elif rtype == "workspace":
            trash.archive_workspace(student_id, original_id)
        elif rtype == "classroom_lesson":
            trash.archive_classroom_lesson(
                student_id, str(snap.get("workspace_id")), original_id)
        else:
            raise _stale("该类型暂不支持在此撤销，请在归档中心处理。")
    except FileNotFoundError:
        raise _stale("恢复的实体已不存在，无法重新归档。")
    return "已重新归档该实体（生成新归档条目）。"


# -- B06 补偿：笔记 / 学习编排 ----------------------------------------------

def _undo_note_append_or_replace(student_id: str, data: dict[str, Any],
                                 snap: dict[str, Any], *,
                                 is_admin: bool) -> str:
    """撤销追加/改写：笔记未被再编辑时恢复 base_revision 版本。"""
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    note_id = str(snap.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise _stale("笔记已不存在，请在笔记仓库处理。")
    if int(meta.get("revision") or 1) != int(snap.get("result_revision") or 0):
        raise _stale("笔记此后又被编辑，请在笔记仓库按版本历史处理。")
    try:
        vault.restore_revision(note_id, int(snap.get("base_revision") or 0))
    except FileNotFoundError:
        raise _stale("原版本快照不存在，请在笔记仓库处理。")
    notes_store.save_vault(vault)
    return "已恢复追加/改写前的笔记版本（生成新当前版本）。"


def _undo_note_move(student_id: str, data: dict[str, Any],
                    snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    target = str(data.get("folder_id") or "")
    moved: list[str] = []
    for move in snap.get("moves") or []:
        note_id = str(move.get("note_id"))
        previous = str(move.get("previous_folder_id") or "")
        meta = vault.find_note(note_id)
        if meta is None or str(meta.get("folder_id") or "") != target:
            # 只回退仍处本动作结果的笔记；被用户再移动的留在原地。
            continue
        if previous and vault.find_folder(previous) is None:
            continue
        vault.move_note(note_id, previous)
        moved.append(note_id)
    notes_store.save_vault(vault)
    if not moved:
        raise _stale("笔记此后已被移动或原文件夹缺失，请在笔记仓库处理。")
    return f"已把 {len(moved)} 篇笔记移回原文件夹。"


def _undo_note_set_review(student_id: str, data: dict[str, Any],
                          snap: dict[str, Any], *, is_admin: bool) -> str:
    previous_enabled = bool(snap.get("previous_enabled"))
    from app.core import notes as notes_store
    from app.api.v1.notes import _drop_review_card, _sync_review_card
    vault = notes_store.load_vault(student_id)
    note_id = str(snap.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise _stale("笔记已不存在。")
    review = dict(meta.get("review") or {})
    if bool(review.get("enabled")) != bool(data.get("enabled")):
        raise _stale("复习开关此后被修改，请在笔记仓库处理。")
    review["enabled"] = previous_enabled
    if previous_enabled:
        _sync_review_card(student_id, meta)
    else:
        review["next_review_at"] = 0.0
        _drop_review_card(student_id, note_id)
    meta["review"] = review
    meta["updated_at"] = notes_store._now()
    notes_store.save_vault(vault)
    return "已恢复原复习计划状态。"


def _undo_note_restore_revision(student_id: str, data: dict[str, Any],
                                snap: dict[str, Any], *,
                                is_admin: bool) -> str:
    from app.core import notes as notes_store
    vault = notes_store.load_vault(student_id)
    note_id = str(snap.get("note_id"))
    meta = vault.find_note(note_id)
    if meta is None:
        raise _stale("笔记已不存在。")
    if int(meta.get("revision") or 1) != int(snap.get("result_revision") or 0):
        raise _stale("笔记此后又被编辑，请在笔记仓库处理。")
    try:
        vault.restore_revision(note_id, int(snap.get("restored_from_current")
                                            or 0))
    except FileNotFoundError:
        raise _stale("恢复前版本不存在，请在笔记仓库处理。")
    notes_store.save_vault(vault)
    return "已恢复到恢复操作前的当前版本。"


def _undo_task_update(student_id: str, data: dict[str, Any],
                      snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    svc = get_orchestration_service()
    task_id = str(snap.get("task_id"))
    current = next((t for t in svc._load(student_id).daily_tasks
                    if t.id == task_id), None)
    if current is None:
        raise _stale("任务已不存在。")
    applied = snap.get("applied") or {}
    # 只在本动作结果仍完整时反向；用户再编辑则指引原页面。
    for key, attr in (("title", "title"), ("day", "day")):
        if key in applied and str(getattr(current, attr, "")) != \
                str(applied.get(key, "")):
            raise _stale("任务此后已被修改，请在学习编排页处理。")
    previous = dict(snap.get("previous") or {})
    kwargs: dict[str, Any] = {}
    if "title" in previous:
        kwargs["title"] = str(previous["title"])
    if "day" in previous:
        kwargs["day"] = str(previous["day"])
    if "estimate_minutes" in previous:
        kwargs["estimate_minutes"] = int(previous["estimate_minutes"])
    if "priority" in previous:
        kwargs["priority"] = int(previous["priority"])
    if not svc.update_task(student_id, task_id, **kwargs):
        raise _stale("恢复失败，请在学习编排页处理。")
    return "已恢复任务原字段。"


def _undo_subtask_create(student_id: str, data: dict[str, Any],
                         snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    svc = get_orchestration_service()
    week_index = int(snap.get("week_index") or 0)
    task_id = str(snap.get("week_task_id"))
    subtask_id = str(snap.get("subtask_id"))
    state = svc._load(student_id)
    sub = next((s for w in state.weekly_plan
                if w.week_index == week_index for t in w.tasks
                if t.id == task_id for s in t.subtasks
                if s.id == subtask_id), None)
    if sub is None:
        raise _stale("子任务已不存在。")
    if str(sub.title) != str(snap.get("title")) or bool(sub.done):
        raise _stale("子任务此后被修改或已完成，请在学习编排页处理。")
    if not svc.delete_subtask(student_id, week_index, task_id, subtask_id):
        raise _stale("删除失败，请在学习编排页处理。")
    return "已删除该子任务。"


# -- B09 补偿：偏好 / 画像 ---------------------------------------------------

def _undo_memory_set_window(student_id: str, data: dict[str, Any],
                            snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.agents.memory.prompt_memory import (
        get_user_window, set_user_window)
    if int(get_user_window(student_id)) != int(
            data.get("window") or 0):
        raise _stale("记忆窗口此后被修改，请在记忆中心处理。")
    set_user_window(student_id, int(snap.get("previous_window") or 15))
    return f"已恢复记忆窗口为 {snap.get('previous_window')} 轮。"


def _undo_profile_update(student_id: str, data: dict[str, Any],
                         snap: dict[str, Any], *, is_admin: bool) -> str:
    from app.identity import store as id_store
    from app.identity.store import update_profile_fields
    user = id_store.get_by_id(student_id)
    if user is None:
        raise _stale("账号已不存在。")
    applied = snap.get("applied") or {}
    # 只回滚本动作写的字段且仍是本动作结果时。
    fields: dict[str, Any] = {}
    previous = dict(snap.get("previous") or {})
    if "name" in applied and user.profile.name != applied.get("name"):
        raise _stale("姓名此后被修改，请在账户资料页处理。")
    if "grade" in applied and user.profile.grade != applied.get("grade"):
        raise _stale("学段此后被修改，请在账户资料页处理。")
    for key in ("name", "grade", "school", "subjects"):
        if key in applied and key in previous:
            fields[key] = previous[key]
    if not fields:
        return "账户资料无需要恢复的字段。"
    try:
        update_profile_fields(student_id, fields)
    except ValueError:
        raise _stale("恢复失败，请在账户资料页处理。")
    if "grade" in fields:
        try:
            from app.api.v1.user import _sync_grade_to_student_model
            refreshed = id_store.get_by_id(student_id)
            if refreshed is not None:
                _sync_grade_to_student_model(refreshed)
        except Exception:  # noqa: BLE001
            pass
    return "已恢复画像原字段。"


def _undo_assistant_preferences(student_id: str, data: dict[str, Any],
                                snap: dict[str, Any],
                                *, is_admin: bool) -> str:
    from app.core.assistant_store import load_preferences, save_preferences
    current = load_preferences(student_id)
    applied = dict(snap.get("applied") or {})
    previous = dict(snap.get("previous") or {})
    if not applied:
        return "偏好无需恢复。"
    # 只有仍是本动作结果的键才反向；用户此后改过的键不动。
    restore: dict[str, Any] = {}
    for key, applied_value in applied.items():
        current_value = current.get(key)
        if current_value is None or str(current_value) == str(applied_value):
            if key in previous:
                raw = previous[key]
                if key in ("auto_read", "send_after_recording",
                           "allow_local_fallback", "proactive_enabled"):
                    restore[key] = raw == "True"
                elif key in ("playback_rate", "volume"):
                    restore[key] = float(raw)
                elif key == "voice_id" and raw == "None":
                    restore[key] = None
                else:
                    restore[key] = raw
    if not restore:
        raise _stale("偏好此后被修改，请在助手设置中处理。")
    save_preferences(student_id, restore)
    return "已恢复助手偏好原值。"


_HANDLERS = {
    "task.create": _undo_task_create,
    "note.create": _undo_note_create,
    "chat.rename": _undo_chat_rename,
    "schedule.update": _undo_schedule_update,
    "workspace.create": _undo_workspace_create,
    "workspace.update_sources": _undo_workspace_update_sources,
    "chat.move_workspace": _undo_chat_move_workspace,
    "chat.archive": _undo_chat_archive,
    "library.create_folder": _undo_library_create_folder,
    "library.rename_file": _undo_library_rename_file,
    "library.move_file": _undo_library_move_file,
    "archive.restore": _undo_archive_restore,
    # B06
    "note.append": _undo_note_append_or_replace,
    "note.replace": _undo_note_append_or_replace,
    "note.move": _undo_note_move,
    "note.set_review": _undo_note_set_review,
    "note.restore_revision": _undo_note_restore_revision,
    "task.update": _undo_task_update,
    "subtask.create": _undo_subtask_create,
    # B09
    "memory.set_window": _undo_memory_set_window,
    "profile.update": _undo_profile_update,
    "assistant.preferences": _undo_assistant_preferences,
}
