"""受控动作 execute/ack 状态机（A10）。

状态转换：
    proposed --execute受理--> executing --命令就绪--> awaiting_ack
        |                         |                       |
        |过期/取消                |失败                   |ack/15s 超时
        v                         v                       v
    expired / cancelled     failed / needs_attention   succeeded / failed

- execute 只接收 invocation_id / client_instance_id / route_epoch，
  不重新接收可被篡改的动作参数；
- 同一 action 只允许一个在途 invocation：重复同 invocation 幂等返回，
  不同 invocation 409/action_in_progress，已终结返回原结果；
- execute 先落盘 executing 再做业务准备；ack_token 绑定
  用户+动作+client_instance_id，不进入模型上下文；
- awaiting_ack 超过 15s 未 ack → needs_attention（读时惰性推进，
  单 worker 进程内无定时器依赖）；
- navigate/open_workspace_form 无业务写；prepare/resume/launch/handoff
  在 A13 接入领域幂等链路后才注册。
"""
from __future__ import annotations

import logging
import secrets
import threading
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core import assistant_store as store
from app.core.assistant_store import AssistantStoreError, utc_now_iso

ACK_WINDOW_SECONDS = 15.0          # §9.4：15s 未 ack → needs_attention
TERMINAL_STATES = ("succeeded", "cancelled", "expired")
_RETRYABLE_ACK_CODES = {"save_failed", "page_not_ready"}

_log = logging.getLogger(__name__)

# 每动作一把进程内锁：WeakValueDictionary 登记，调用方 with 块持强引用，
# 用完自动回收——此前按 action_id 累积的 dict 只增不减，长寿命进程下是
# 缓慢内存泄漏。并发安全论证同 core/atomic.py 的 file_lock。
_locks: "weakref.WeakValueDictionary[str, threading.Lock]" = (
    weakref.WeakValueDictionary())
_locks_guard = threading.Lock()


class ActionRejected(Exception):
    def __init__(self, status_code: int, code: str, message: str,
                 *, retryable: bool = False,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.extra = extra or {}


def _action_lock(action_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(action_id)
        if lock is None:
            lock = threading.Lock()
            _locks[action_id] = lock
        return lock


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_expired(action: dict[str, Any]) -> bool:
    expires = _parse_iso(action.get("expires_at"))
    return expires is not None and _now() >= expires


def _ack_due(action: dict[str, Any]) -> bool:
    """awaiting_ack 超过 15s → 应转为 needs_attention。"""
    if action.get("state") != "awaiting_ack":
        return False
    invocation = action.get("invocation") or {}
    delivered = _parse_iso(invocation.get("delivered_at"))
    return delivered is not None and (
        _now() - delivered).total_seconds() > ACK_WINDOW_SECONDS


def refresh_action_state(action: dict[str, Any]) -> bool:
    """读时惰性状态推进（过期 / ack 超时）。返回是否发生变化。

    只改内存副本；持久化由调用方决定（GET 路径会保存，纯投影不保存）。
    """
    changed = False
    if (action.get("state") in ("proposed",)
            and _is_expired(action)):
        action["state"] = "expired"
        changed = True
    if _ack_due(action):
        action["state"] = "needs_attention"
        changed = True
    return changed


def public_action(action: dict[str, Any]) -> dict[str, Any]:
    """§9.1 AssistantAction 的对外形状：剥离运行期内部字段。"""
    refreshed = dict(action)
    refresh_action_state(refreshed)
    return {
        "action_id": refreshed.get("action_id"),
        "conversation_id": refreshed.get("conversation_id"),
        "turn_id": refreshed.get("turn_id"),
        "label": refreshed.get("label"),
        "payload": refreshed.get("payload"),
        "execution": refreshed.get("execution"),
        "state": refreshed.get("state"),
        "undo_result": refreshed.get("undo_result"),
        "business_result": refreshed.get("business_result")
        or {"kind": "none"},
        "created_at": refreshed.get("created_at"),
        "expires_at": refreshed.get("expires_at"),
    }


def locate_action(
    student_id: str, action_id: str,
) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """返回 (会话记录, conversation_id, action 记录)。"""
    items, _total = store.list_conversations(student_id, limit=100)
    for summary in items:
        cid = summary.get("conversation_id")
        record = store.load_conversation(student_id, cid)
        if record is None:
            continue
        action = (record.get("actions") or {}).get(action_id)
        if action is not None:
            return record, str(cid), action
    return None


def _save(record: dict[str, Any], student_id: str) -> None:
    record["revision"] = int(record.get("revision", 1)) + 1
    store.save_conversation(student_id, record)


def _response(record: dict[str, Any], action: dict[str, Any],
              *, include_secret: bool,
              client_instance_id: str | None = None) -> dict[str, Any]:
    invocation = action.get("invocation") or {}
    same_executor = (client_instance_id is not None
                     and invocation.get("client_instance_id")
                     == client_instance_id)
    # 命令只对当前执行者在 awaiting_ack / needs_attention 期间下发；
    # executing 表示异步准备未完（202/command=null），终态只回结果。
    deliverable = bool(same_executor and action.get("state") in (
        "awaiting_ack", "needs_attention"))
    body: dict[str, Any] = {
        "action": public_action(action),
        "conversation_revision": record.get("revision", 1),
        "business_result": action.get("business_result")
        or {"kind": "none"},
        "client_result": action.get("client_result"),
        "command": invocation.get("command") if deliverable else None,
        "command_id": invocation.get("command_id") if deliverable else None,
        "ack_token": invocation.get("ack_token") if include_secret
        and deliverable else None,
        "command_expires_at": (invocation.get("command_expires_at")
                               if deliverable else None),
        "ready_to_deliver": deliverable,
        "retryable": bool(action.get("retryable")),
    }
    return body


def execute_action(
    student_id: str, action_id: str, *,
    invocation_id: str, client_instance_id: str, route_epoch: int,
    approval_id: str | None = None,
    is_admin: bool = False,
    main_loop=None,
) -> dict[str, Any]:
    """§9.3 execute：受理校验 → 先落盘 executing → 准备命令 → awaiting_ack。

    §21.4：review_required 的 domain_write 操作必须携带当前有效 approval_id。
    is_admin/main_loop 供领域写分支使用（公用教材校验 / 事件循环桥接）。
    """
    with _action_lock(action_id):
        located = locate_action(student_id, action_id)
        if located is None:
            raise ActionRejected(404, "entity_not_found", "该动作不存在。")
        record, _cid, action = located

        state = action.get("state", "proposed")
        invocation = action.get("invocation") or {}

        if state == "proposed" and _is_expired(action):
            action["state"] = "expired"
            _save(record, student_id)
            raise ActionRejected(410, "action_expired",
                                 "动作已过期，请重新发起。", retryable=False)

        if state in ("executing", "awaiting_ack", "needs_attention"):
            same = (invocation.get("invocation_id") == invocation_id
                    and invocation.get("client_instance_id")
                    == client_instance_id)
            if same:
                # 同 invocation 重复提交：返回同一命令，不再次运行准备。
                return _response(record, action, include_secret=True,
                                 client_instance_id=client_instance_id)
            raise ActionRejected(
                409, "action_in_progress",
                "该动作正由另一执行者处理。", retryable=False,
                extra={"current_state": state})

        if state in TERMINAL_STATES or state in ("failed",):
            # 已终结：返回原结果（§9.4），不重新执行。
            return _response(record, action, include_secret=False,
                             client_instance_id=client_instance_id)

        if state != "proposed":
            raise ActionRejected(409, "action_state_invalid",
                                 "动作状态不允许执行。")

        # -- 先落盘 executing（§9.4） ---------------------------------------
        action["state"] = "executing"
        action["invocation"] = {
            "invocation_id": invocation_id,
            "client_instance_id": client_instance_id,
            "route_epoch": int(route_epoch),
            "started_at": utc_now_iso(),
        }
        _save(record, student_id)

        # -- 业务准备 + 页面命令 --------------------------------------------
        payload = action.get("payload") or {}
        kind = str(payload.get("kind") or "")
        if kind == "domain_write":
            # §21：审批校验在执行前；许可缺失/过期回滚为 proposed 可重试。
            from . import previews
            try:
                previews.require_approval(student_id, action, approval_id,
                                          is_admin=is_admin)
            except ActionRejected:
                action["state"] = "proposed"
                action["invocation"] = {}
                _save(record, student_id)
                raise
        try:
            prepared = _prepare_execution(student_id, kind, payload, action,
                                          is_admin=is_admin,
                                          main_loop=main_loop)
        except ActionRejected as exc:
            # §19.3：业务准备/领域写失败 → failed；错误码与 retryable
            # 落盘（工作流与动作卡据此重试），不留悬挂 executing。
            action["state"] = "failed"
            action["retryable"] = bool(exc.retryable)
            action["client_result"] = {"status": "failed",
                                       "code": exc.code}
            _save(record, student_id)
            raise
        except Exception as exc:  # noqa: BLE001
            _log.warning("assistant action prepare failed: %s", exc,
                         exc_info=True)
            prepared = {"command": None, "business_result": None,
                        "failed": True, "message": str(exc)[:200]}
        if prepared is None or prepared.get("command") is None:
            if prepared and prepared.get("terminal") == "succeeded":
                # domain_write：业务写入成功，无页面命令，直接终态。
                action["business_result"] = prepared.get("business_result") \
                    or {"kind": "none"}
                action["state"] = "succeeded"
                action["invocation"]["completed_at"] = utc_now_iso()
                _save(record, student_id)
                return _response(record, action, include_secret=False,
                                 client_instance_id=client_instance_id)
            action["state"] = "failed"
            action["retryable"] = False
            action["client_result"] = {
                "status": "failed",
                "code": "capability_disabled",
            }
            _save(record, student_id)
            return _response(record, action, include_secret=False,
                             client_instance_id=client_instance_id)

        command_id = store.mint_command_id()
        action["business_result"] = prepared.get("business_result") \
            or {"kind": "none"}
        action["state"] = "awaiting_ack"
        action["invocation"].update({
            "command": prepared["command"],
            "command_id": command_id,
            "ack_token": secrets.token_hex(16),
            "command_expires_at": action.get("expires_at"),
            "delivered_at": utc_now_iso(),
        })
        _save(record, student_id)
        return _response(record, action, include_secret=True,
                         client_instance_id=client_instance_id)


def _owned_workspace_ids(student_id: str) -> set[str]:
    from . import readers
    return {w["workspace_id"] for w in readers.owned_workspaces(student_id)}


def _classroom_allowed(student_id: str) -> tuple[bool, str]:
    try:
        from app.classroom import capabilities as classroom_caps
        allowed, reason = classroom_caps.user_allowed(student_id)
        return allowed, reason or ""
    except Exception:
        return False, "课堂能力状态未知"


def _prepare_execution(
    student_id: str, kind: str, payload: dict[str, Any],
    action: dict[str, Any],
    *, is_admin: bool = False, main_loop=None,
) -> dict[str, Any] | None:
    """按 §9.1 payload kind 准备命令与业务结果（A10 导航族 + A13 交接族）。

    返回 {"command", "business_result"}；不在白名单/校验失败返回 None；
    目标已变化（课程版本不可用、任务已完成等）抛 ActionRejected(409,
    target_changed)。
    """
    if kind == "navigate":
        target = payload.get("target")
        if isinstance(target, dict) and target.get("kind"):
            return {"command": {"kind": "navigate", "target": target}}
        return None
    if kind == "open_workspace_form":
        workspace_id = str(payload.get("workspace_id") or "new")
        if workspace_id == "new":
            return {"command": {"kind": "workspace_form",
                                "workspace_id": "new"}}
        if workspace_id in _owned_workspace_ids(student_id):
            return {"command": {"kind": "workspace_form",
                                "workspace_id": workspace_id}}
        return None

    if kind == "prepare_lesson":
        return _prepare_lesson(student_id, payload, action)
    if kind == "resume_lesson":
        return _prepare_resume_lesson(student_id, payload, action)
    if kind == "launch_task":
        return _prepare_launch_task(student_id, payload)
    if kind in ("handoff_chat", "handoff_note"):
        return _prepare_handoff(student_id, kind, payload)
    if kind == "open_classroom_question":
        return _prepare_classroom_question(student_id, payload, action)
    if kind == "start_workflow":
        return _prepare_start_workflow(student_id, payload, action)
    if kind == "manage_subscription":
        return _prepare_manage_subscription(student_id, payload, action)
    if kind == "domain_write":
        # §21：领域写入在服务端完成，无页面命令；成功即终态。
        from . import previews
        result = previews.execute_domain_write(
            student_id, action, is_admin=is_admin, main_loop=main_loop)
        return {"command": None, "business_result": result,
                "terminal": "succeeded"}
    return None


def _prepare_start_workflow(student_id: str, payload: dict[str, Any],
                            action: dict[str, Any]) -> dict[str, Any] | None:
    """§21.1/§23.5 发起工作流：execute 只创建 draft（不写业务）；后续
    批准与启动在办理事项视图完成（§23.3 多步授权）。幂等：同 action
    重试复用首次创建的 workflow_id。"""
    prior = action.get("business_result") or {}
    if prior.get("kind") == "workflow" and prior.get("entity_id"):
        return {"command": None, "business_result": dict(prior),
                "terminal": "succeeded"}
    objective = str(payload.get("objective") or "").strip()
    if len(objective) < 4:
        raise ActionRejected(422, "invalid_target", "缺少工作流目标。")
    from . import workflows as wf_svc
    from .previews import ActionRejected
    try:
        wf = wf_svc.create_workflow(
            student_id,
            conversation_id=str(action.get("conversation_id") or ""),
            template=str(payload.get("template")),
            objective=objective[:2000],
            scope=dict(payload.get("scope") or {}),
            selection_ids=dict(payload.get("selection_ids") or {}),
            client_request_id=f"wf_{action.get('action_id')}")
    except wf_svc.WorkflowRejected as exc:
        raise ActionRejected(int(exc.status), str(exc.code),
                             str(exc.message)) from exc
    return {"command": None,
            "business_result": {"kind": "workflow",
                                "entity_id": str(wf.get("workflow_id"))},
            "terminal": "succeeded"}


def _prepare_manage_subscription(student_id: str, payload: dict[str, Any],
                                 action: dict[str, Any]) -> dict[str, Any]:
    """§25.1/§25.5 manage_subscription：execute 建订阅（幂等 client_
    request_id=action_id）；退订/改时间走设置页助手分类（原页面能力）。"""
    prior = action.get("business_result") or {}
    if prior.get("kind") == "subscription" and prior.get("entity_id"):
        return {"command": None, "business_result": dict(prior),
                "terminal": "succeeded"}
    from . import notifications as notify
    from .previews import ActionRejected
    if not notify.scheduler_enabled():
        raise ActionRejected(503, "capability_disabled",
                             "主动服务调度当前未开放。")
    try:
        sub = notify.create_subscription(
            student_id,
            kind=str((payload.get("input") or {}).get("kind")),
            timezone_name=str((payload.get("input") or {})
                              .get("timezone") or "UTC"),
            local_time=str((payload.get("input") or {}).get("local_time")
                           or ""),
            client_request_id=f"sub_{action.get('action_id')}")
    except notify.SubscriptionRejected as exc:
        raise ActionRejected(int(exc.status), str(exc.code),
                             str(exc.message)) from exc
    return {"command": None,
            "business_result": {"kind": "subscription",
                                "entity_id": str(
                                    sub.get("subscription_id"))},
            "terminal": "succeeded"}


def _prepare_lesson(student_id: str, payload: dict[str, Any],
                    action: dict[str, Any]) -> dict[str, Any] | None:
    """备课草稿（§9.5/§19.6）：只预填不自动生成。"""
    allowed, reason = _classroom_allowed(student_id)
    if not allowed:
        return None
    workspace_id = str(payload.get("workspace_id") or "")
    if workspace_id not in _owned_workspace_ids(student_id):
        return None
    draft_body = dict(payload.get("draft") or {})
    topic = str(draft_body.get("topic") or "").strip()[:500]
    if not topic:
        return None
    draft = store.create_draft(student_id, {
        "conversation_id": action.get("conversation_id"),
        "action_id": action.get("action_id"),
        "prefill": {
            "kind": "lesson", "workspace_id": workspace_id, "topic": topic,
            "objectives": (str(draft_body.get("objectives") or "")
                           [:2000] or None),
            "duration_minutes": draft_body.get("duration_minutes"),
        },
        "source_ids": [],
    })
    return {
        "command": {"kind": "lesson_form", "workspace_id": workspace_id,
                    "draft_id": draft["draft_id"]},
        "business_result": {"kind": "draft", "entity_id": draft["draft_id"],
                            "related_ids": {"workspace_id": workspace_id}},
    }


def _prepare_resume_lesson(student_id: str, payload: dict[str, Any],
                           action: dict[str, Any]) -> dict[str, Any]:
    """§19.7 课堂恢复：冻结 revision；run 复核归属；幂等键来自 action。"""
    allowed, _reason = _classroom_allowed(student_id)
    if not allowed:
        return None
    workspace_id = str(payload.get("workspace_id") or "")
    lesson_id = str(payload.get("lesson_id") or "")
    revision = int(payload.get("lesson_revision") or 0)
    run_id = payload.get("run_id")
    if workspace_id not in _owned_workspace_ids(student_id):
        return None
    from app.core import classroom_store
    lesson = classroom_store.load_lesson(student_id, workspace_id, lesson_id)
    if lesson is None or lesson.lifecycle not in ("active", "archived"):
        raise ActionRejected(409, "target_changed",
                             "课程已不存在或不可用。")
    if revision < 1:
        revision = lesson.latest_ready_revision
    if revision < 1:
        return None
    if run_id:
        run = classroom_store.load_run(student_id, workspace_id, lesson_id,
                                       str(run_id))
        if (run is None or run.lesson_revision != revision
                or run.status.value in ("ended",)):
            # §19.7-2：run 已结束/被删/版本不符 → 409，不悄悄 restart。
            raise ActionRejected(409, "target_changed",
                                 "课堂进度已变化，请重新选择课程。")

    from app.classroom import runs as classroom_runs
    from app.schemas import classroom as sc
    request = sc.CreateRunRequest(
        lesson_revision=revision,
        mode=sc.RunStartMode.resume_or_create)
    result = classroom_runs.create_run(
        student_id, workspace_id, lesson_id, request,
        idempotency_key=f"assistant:{action.get('action_id')}")
    return {
        "command": {
            "kind": "navigate",
            "target": {"kind": "classroom_run",
                       "workspace_id": workspace_id, "lesson_id": lesson_id,
                       "run_id": result["run_id"]},
        },
        "business_result": {
            "kind": "classroom_run", "entity_id": result["run_id"],
            "related_ids": {"workspace_id": workspace_id,
                            "lesson_id": lesson_id},
        },
    }


def _prepare_launch_task(student_id: str,
                         payload: dict[str, Any]) -> dict[str, Any]:
    """§19.7 学习任务：复用既有绑定幂等 launch；已完成 → 409。"""
    task_id = str(payload.get("task_id") or "")
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    launch = get_orchestration_service().launch_task(student_id, task_id)
    if launch is None:
        raise ActionRejected(
            409, "target_changed",
            "任务已完成或不存在，请在任务中心查看。",
            extra={"view": "/orchestration"})
    return {
        "command": {
            "kind": "navigate",
            "target": {"kind": "chat_session",
                       "session_id": launch.get("session_id") or ""},
        },
        "business_result": {
            "kind": "task_launch", "entity_id": launch.get("episode_id") or "",
            "related_ids": {"session_id": launch.get("session_id") or ""},
        },
    }


def _prepare_handoff(student_id: str, kind: str,
                     payload: dict[str, Any]) -> dict[str, Any] | None:
    """聊天/笔记交接：只预填（§9.5），不自动 send、不直接写正式笔记。"""
    draft_id = str(payload.get("draft_id") or "")
    draft = store.load_draft(student_id, draft_id)
    if draft is None:
        return None
    if draft.get("expired"):
        raise ActionRejected(410, "draft_expired", "交接草稿已过期。")
    if draft.get("consumed"):
        raise ActionRejected(409, "draft_already_consumed",
                             "交接草稿已被使用。")
    prefill = draft.get("prefill") or {}
    if str(prefill.get("kind") or "") != ("chat" if kind == "handoff_chat"
                                          else "note"):
        return None
    if kind == "handoff_chat":
        session_id = prefill.get("session_id")
        workspace_id = prefill.get("workspace_id")
        if workspace_id and workspace_id not in _owned_workspace_ids(student_id):
            return None
        return {
            "command": {"kind": "chat_draft", "draft_id": draft_id},
            "business_result": {"kind": "draft", "entity_id": draft_id,
                                "related_ids": {
                                    "session_id": session_id or ""}},
        }
    return {
        "command": {"kind": "note_draft", "draft_id": draft_id},
        "business_result": {"kind": "draft", "entity_id": draft_id},
    }


def _prepare_classroom_question(student_id: str, payload: dict[str, Any],
                                action: dict[str, Any]) -> dict[str, Any] | None:
    """课堂插问草稿（§9.5）：只预填问题，不自动提交。"""
    allowed, _reason = _classroom_allowed(student_id)
    if not allowed:
        return None
    workspace_id = str(payload.get("workspace_id") or "")
    if workspace_id not in _owned_workspace_ids(student_id):
        return None
    from app.core import classroom_store
    run = classroom_store.load_run(
        student_id, workspace_id, str(payload.get("lesson_id") or ""),
        str(payload.get("run_id") or ""))
    if run is None:
        raise ActionRejected(409, "target_changed", "课堂进度不存在。")
    question = str(payload.get("question") or "").strip()[:4000]
    if not question:
        return None
    draft = store.create_draft(student_id, {
        "conversation_id": action.get("conversation_id"),
        "action_id": action.get("action_id"),
        "prefill": {
            "kind": "classroom_question", "workspace_id": workspace_id,
            "lesson_id": payload.get("lesson_id"),
            "run_id": payload.get("run_id"), "question": question,
        },
        "source_ids": [],
    })
    return {
        "command": {"kind": "classroom_question",
                    "workspace_id": workspace_id,
                    "lesson_id": str(payload.get("lesson_id") or ""),
                    "run_id": str(payload.get("run_id") or ""),
                    "draft_id": draft["draft_id"]},
        "business_result": {"kind": "draft", "entity_id": draft["draft_id"],
                            "related_ids": {
                                "workspace_id": workspace_id,
                                "lesson_id": str(payload.get("lesson_id")
                                                 or "")}},
    }


def ack_action(
    student_id: str, action_id: str, *,
    command_id: str, ack_token: str, result: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    """§9.4 ack：回显 succeeded / failed / cancelled；重复相同 ack 幂等。"""
    if result not in ("succeeded", "failed", "cancelled"):
        raise ActionRejected(422, "invalid_target",
                             "result 只能是 succeeded/failed/cancelled。")
    with _action_lock(action_id):
        located = locate_action(student_id, action_id)
        if located is None:
            raise ActionRejected(404, "entity_not_found", "该动作不存在。")
        record, _cid, action = located
        state = action.get("state", "proposed")
        invocation = action.get("invocation") or {}

        if state in ("awaiting_ack", "needs_attention"):
            if (invocation.get("command_id") != command_id
                    or not secrets.compare_digest(
                        str(invocation.get("ack_token") or ""), ack_token)):
                raise ActionRejected(409, "ack_invalid",
                                     "确认凭证不匹配。")
            if _is_expired(action):
                action["state"] = "expired"
                _save(record, student_id)
                raise ActionRejected(410, "action_expired",
                                     "动作已过期。")
        elif state == "succeeded":
            # 幂等：重复相同 ack 返回当前状态。
            if invocation.get("command_id") != command_id:
                raise ActionRejected(409, "ack_invalid",
                                     "确认凭证不匹配。")
            return _response(record, action, include_secret=False)
        elif state == "proposed":
            raise ActionRejected(409, "action_state_invalid",
                                 "动作尚未执行，不能确认。")
        else:  # executing / failed / cancelled / expired
            raise ActionRejected(
                409, "action_state_invalid",
                f"动作状态 {state} 不允许确认。",
                extra={"current_state": state})

        if result == "succeeded":
            action["state"] = "succeeded"
            action["client_result"] = {"status": "succeeded"}
            action["retryable"] = False
        elif result == "cancelled":
            action["state"] = "cancelled"
            action["client_result"] = {"status": "cancelled"}
            action["retryable"] = False
        else:
            code = error_code or "page_not_ready"
            action["state"] = "failed"
            action["client_result"] = {"status": "failed", "code": code}
            action["retryable"] = code in _RETRYABLE_ACK_CODES
        action.setdefault("invocation", {})["acked_at"] = utc_now_iso()
        _save(record, student_id)
        return _response(record, action, include_secret=False)


def get_action(student_id: str, action_id: str,
               client_instance_id: str | None = None) -> dict[str, Any]:
    located = locate_action(student_id, action_id)
    if located is None:
        raise ActionRejected(404, "entity_not_found", "该动作不存在。")
    record, _cid, action = located
    changed = refresh_action_state(action)
    if changed:
        try:
            _save(record, student_id)
        except AssistantStoreError:
            pass  # 投影推进失败不阻塞读取
    return _response(record, action, include_secret=False,
                     client_instance_id=client_instance_id)


def cancel_proposed_for_turn(record: dict[str, Any], turn_id: str) -> bool:
    """turn 取消时联动（runtime 已内联同语义；此函数供后续复用）。"""
    changed = False
    for action in (record.get("actions") or {}).values():
        if (action.get("turn_id") == turn_id
                and action.get("state") == "proposed"):
            action["state"] = "cancelled"
            changed = True
    return changed
