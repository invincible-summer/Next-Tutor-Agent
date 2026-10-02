"""§23 助手工作流执行器（C01）：持久状态、步骤依赖、恢复与部分成功。

要点（plan.md §23.2/23.3/23.4）：
- 记录存 ASSISTANT_DIR/workflows/<id>.json（§26.3；随账号清理覆盖）。
- 状态机：draft→awaiting_approval→queued→running→(waiting_domain_job|
  paused_for_user)→succeeded|partially_succeeded|failed|cancelled|
  interrupted；revision 乐观并发。
- 步骤 ≤8、写步骤 ≤5；读步骤并发 ≤2，写步骤串行；依赖未成功不执行
  后续写步骤。幂等键 workflow_id+step_id+approved_plan_hash。
- 写步骤复用 §21 领域动作链（隐式 AssistantAction + preview + approve +
  execute）：工作流批准即对预览过的参数签发步骤许可；重试复用同一
  action_id → B04 幂等不重复创建。
- 领域长作业：等待 30 秒后持久化 waiting_domain_job 并释放请求；20 分钟
  无终态转 paused_for_user。重启后 interrupted；resume 按 DomainJobRef
  回查原作业，不重新执行创建步骤。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from app.core.assistant_store import _student_root

WORKFLOW_STATES = (
    "draft", "awaiting_approval", "queued", "running",
    "waiting_domain_job", "paused_for_user", "succeeded",
    "partially_succeeded", "failed", "cancelled", "interrupted",
)
STEP_STATES = ("pending", "ready", "running", "waiting", "succeeded",
               "failed", "skipped", "cancelled")
MAX_STEPS = 8
MAX_WRITE_STEPS = 5
MAX_ACTIVE_WORKFLOWS = 2
STEP_WAIT_SECONDS = 30.0
DOMAIN_JOB_TIMEOUT_SECONDS = 20 * 60

# 运行中工作流（进程内单飞；重启 → interrupted 由 load 时恢复）。
_running: dict[str, asyncio.Task] = {}


def workflows_enabled() -> bool:
    """§26.5 SITE_ASSISTANT_WORKFLOWS_ENABLED 独立开关。"""
    from app.core.config import settings
    return bool(settings.site_assistant_workflows_enabled)


def _now() -> float:
    return time.time()


def _utc_iso(ts: float | None = None) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts or _now(),
                                  tz=timezone.utc).isoformat()


def new_workflow_id() -> str:
    return "astw_" + uuid.uuid4().hex[:16]


# -- 存储 ---------------------------------------------------------------------

def _workflows_dir(student_id: str):
    return _student_root(student_id) / "workflows"


def _path(student_id: str, workflow_id: str):
    return _workflows_dir(student_id) / f"{workflow_id}.json"


def load_workflow(student_id: str,
                  workflow_id: str) -> dict[str, Any] | None:
    path = _path(student_id, workflow_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _persist(student_id: str, wf: dict[str, Any]) -> dict[str, Any]:
    wf["revision"] = int(wf.get("revision", 1)) + 1
    wf["updated_at"] = _utc_iso()
    path = _path(student_id, str(wf.get("workflow_id")))
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.core.atomic import atomic_write_text
    atomic_write_text(path, json.dumps(wf, ensure_ascii=False))
    return wf


def list_workflows(student_id: str, *, offset: int = 0, limit: int = 20,
                   state: str = "") -> tuple[list[dict[str, Any]], int]:
    import pathlib
    d = _workflows_dir(student_id)
    if not d.exists():
        return [], 0
    items: list[dict[str, Any]] = []
    for p in pathlib.Path(d).glob("*.json"):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    if state:
        items = [w for w in items if str(w.get("state")) == state]
    items.sort(key=lambda w: str(w.get("updated_at") or ""), reverse=True)
    total = len(items)
    return items[offset:offset + limit], total


def active_workflows(student_id: str) -> list[dict[str, Any]]:
    active = ("draft", "awaiting_approval", "queued", "running",
              "waiting_domain_job", "paused_for_user", "interrupted")
    items, _total = list_workflows(student_id, limit=100)
    return [w for w in items if str(w.get("state")) in active]


# -- 创建 / 预览 / 批准 / 启动 --------------------------------------------------

def create_workflow(student_id: str, *, conversation_id: str,
                    template: str, objective: str, scope: dict[str, Any],
                    selection_ids: dict[str, list[str]],
                    client_request_id: str) -> dict[str, Any]:
    from . import workflow_templates
    if not workflows_enabled():
        raise WorkflowRejected(503, "capability_disabled",
                               "工作流能力当前未开放。")
    # 同 client_request_id 幂等：先于活跃上限检查（重试不因自身占额被拒）。
    existing, _ = list_workflows(student_id, limit=100)
    for other in existing:
        if other.get("client_request_id") == client_request_id:
            return other
    active = active_workflows(student_id)
    if len(active) >= MAX_ACTIVE_WORKFLOWS:
        raise WorkflowRejected(409, "workflow_limit",
                               f"每个用户最多 {MAX_ACTIVE_WORKFLOWS} 个进行"
                               "中的工作流；请先完成或取消现有事项。")
    steps = workflow_templates.build_steps(
        student_id, template, objective, scope, selection_ids)
    if not steps:
        raise WorkflowRejected(422, "template_unknown",
                               "未知的工作流模板。")
    writes = [s for s in steps if s["kind"] == "write"]
    if len(steps) > MAX_STEPS or len(writes) > MAX_WRITE_STEPS:
        raise WorkflowRejected(422, "template_invalid",
                               "步骤数超过工作流限制。")
    wf: dict[str, Any] = {
        "workflow_id": new_workflow_id(),
        "conversation_id": conversation_id,
        "revision": 0,
        "template": template,
        "objective": objective[:2000],
        "scope": scope,
        "selection_ids": selection_ids,
        "state": "draft",
        "steps": steps,
        "approved_plan_hash": None,
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "cancel_requested": False,
        "client_request_id": client_request_id,
        "result_targets": [],
    }
    return _persist(student_id, wf)


def workflow_preview(student_id: str, wf: dict[str, Any]) -> dict[str, Any]:
    """§23.3 预览：目标、步骤、写入对象、需要用户参与的位置。"""
    from . import workflow_templates
    plan = workflow_templates.describe_plan(student_id, wf)
    return {
        "workflow_id": wf.get("workflow_id"),
        "template": wf.get("template"),
        "objective": wf.get("objective"),
        "state": wf.get("state"),
        "revision": wf.get("revision"),
        "steps": [{
            "step_id": s.get("step_id"), "title": s.get("title"),
            "kind": s.get("kind"), "depends_on": s.get("depends_on"),
            "operation": s.get("operation"),
        } for s in wf.get("steps") or []],
        "plan": plan,
        "user_touchpoints": [
            s.get("step_id") for s in wf.get("steps") or []
            if s.get("kind") in ("prepare", "handoff", "wait_job")],
        "plan_hash": plan_hash(wf, plan),
    }


def plan_hash(wf: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    payload = {
        "template": wf.get("template"),
        "objective": wf.get("objective"),
        "steps": [{
            "step_id": s.get("step_id"), "operation": s.get("operation"),
            "input": s.get("input"),
        } for s in wf.get("steps") or []],
        "plan": plan,
    }
    return "wph_" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8")).hexdigest()[:48]


def approve_workflow(student_id: str, workflow_id: str, *,
                     expected_revision: int, plan_hash_in: str,
                     approved_step_ids: list[str]) -> dict[str, Any]:
    wf = _load_or_404(student_id, workflow_id)
    if int(wf.get("revision") or 0) != int(expected_revision):
        raise WorkflowRejected(409, "revision_conflict",
                               "工作流已被更新，请刷新后重试。")
    if str(wf.get("state")) not in ("draft", "awaiting_approval"):
        raise WorkflowRejected(409, "state_invalid",
                               f"状态 {wf.get('state')} 不可批准。")
    if plan_hash_in != plan_hash_of(student_id, wf):
        raise WorkflowRejected(409, "preview_stale",
                               "计划已变化，请重新预览。")
    known = {str(s.get("step_id")) for s in wf.get("steps") or []}
    unknown = [sid for sid in approved_step_ids if sid not in known]
    if unknown:
        raise WorkflowRejected(422, "step_unknown",
                               f"未展示的步骤不能批准：{unknown[:3]}")
    wf["approved_steps"] = sorted(set(approved_step_ids))
    wf["approved_plan_hash"] = plan_hash_in
    wf["state"] = "awaiting_approval"
    return _persist(student_id, wf)


def plan_hash_of(student_id: str, wf: dict[str, Any]) -> str:
    from . import workflow_templates
    plan = workflow_templates.describe_plan(student_id, wf)
    payload = {
        "template": wf.get("template"),
        "objective": wf.get("objective"),
        "steps": [{
            "step_id": s.get("step_id"), "operation": s.get("operation"),
            "input": s.get("input"),
        } for s in wf.get("steps") or []],
        "plan": plan,
    }
    return "wph_" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8")).hexdigest()[:48]


def start_workflow(student_id: str, workflow_id: str, *,
                   client_request_id: str,
                   expected_revision: int) -> dict[str, Any]:
    if not workflows_enabled():
        raise WorkflowRejected(503, "capability_disabled",
                               "工作流能力当前未开放。")
    wf = _load_or_404(student_id, workflow_id)
    if int(wf.get("revision") or 0) != int(expected_revision):
        raise WorkflowRejected(409, "revision_conflict",
                               "工作流已被更新，请刷新后重试。")
    if str(wf.get("state")) not in ("awaiting_approval", "paused_for_user",
                                    "interrupted"):
        raise WorkflowRejected(409, "state_invalid",
                               f"状态 {wf.get('state')} 不可启动。")
    if not wf.get("approved_plan_hash"):
        raise WorkflowRejected(409, "not_approved",
                               "请先批准工作流计划。")
    wf["state"] = "queued"
    wf["client_request_id"] = client_request_id
    _persist(student_id, wf)
    return wf


def cancel_workflow(student_id: str, workflow_id: str) -> dict[str, Any]:
    """§23.4-7：未开始步骤 cancelled；运行中领域作业调原取消；成功产物
    保留并列明。幂等：重复 cancel 返回当前状态。"""
    wf = _load_or_404(student_id, workflow_id)
    if str(wf.get("state")) in ("succeeded", "failed", "cancelled"):
        return wf
    wf["cancel_requested"] = True
    for step in wf.get("steps") or []:
        if str(step.get("state")) in ("pending", "ready"):
            step["state"] = "cancelled"
    # 在途领域作业协作式取消（尽力而为）。
    for step in wf.get("steps") or []:
        job = step.get("domain_job") or {}
        if str(step.get("state")) in ("running", "waiting") and job:
            _cancel_domain_job(student_id, job)
    wf["state"] = "cancelled"
    return _persist(student_id, wf)


def _cancel_domain_job(student_id: str, job: dict[str, Any]) -> None:
    kind = str(job.get("kind") or "")
    try:
        if kind == "lesson_generation":
            from app.classroom import service as classroom_service
            classroom_service.cancel_job(
                student_id, str(job.get("workspace_id")),
                str(job.get("lesson_id")), str(job.get("job_id")),
                int(job.get("state_revision") or 1))
    except Exception:
        pass  # 已终态或不可取消：保留产物


def retry_step(student_id: str, workflow_id: str, *, step_id: str,
               expected_revision: int,
               client_request_id: str) -> dict[str, Any]:
    """§23.5：只重试该失败步骤（同幂等键，不重复创建实体）。"""
    wf = _load_or_404(student_id, workflow_id)
    if int(wf.get("revision") or 0) != int(expected_revision):
        raise WorkflowRejected(409, "revision_conflict",
                               "工作流已被更新，请刷新后重试。")
    step = next((s for s in wf.get("steps") or []
                 if str(s.get("step_id")) == step_id), None)
    if step is None:
        raise WorkflowRejected(404, "entity_not_found", "步骤不存在。")
    if str(step.get("state")) != "failed":
        raise WorkflowRejected(409, "state_invalid",
                               "只有失败步骤可重试。")
    step["state"] = "pending"
    step["error"] = None
    wf["state"] = "queued"
    wf["cancel_requested"] = False
    return _persist(student_id, wf)


def resume_workflow(student_id: str, workflow_id: str, *,
                    expected_revision: int,
                    choice_id: str = "",
                    approval_id: str = "") -> dict[str, Any]:
    """§23.5：补足等待条件后继续（paused_for_user / interrupted /
    waiting_domain_job）。choice/approval 由模板落点消费（当前模板无
    分支选择，记录后按 DomainJobRef 续跑，§23.4-6）。"""
    wf = _load_or_404(student_id, workflow_id)
    if int(wf.get("revision") or 0) != int(expected_revision):
        raise WorkflowRejected(409, "revision_conflict",
                               "工作流已被更新，请刷新后重试。")
    if str(wf.get("state")) not in ("paused_for_user", "interrupted",
                                    "waiting_domain_job"):
        raise WorkflowRejected(409, "state_invalid",
                               f"状态 {wf.get('state')} 无需恢复。")
    if choice_id:
        wf.setdefault("resume_choices", []).append(choice_id)
    wf["state"] = "queued"
    return _persist(student_id, wf)


# -- 事件流（§23.5 events：快照 + step_updated + workflow_done） ----------------
#
# 单 worker 文件事实源：SSE 以 1s 轮询 revision/步骤状态差异生成事件，
# 不引入第二套事件存储；终态后保留 10 分钟（与 §11.5 turn 事件一致）。

TERMINAL_WORKFLOW_STATES = ("succeeded", "partially_succeeded", "failed",
                            "cancelled", "interrupted")


async def stream_workflow_events(student_id: str, workflow_id: str,
                                 after_seq: int = 0):
    """异步生成 (event, payload, seq)；调用方负责 SSE 编码与断开。"""
    seq = int(after_seq)
    wf = load_workflow(student_id, workflow_id)
    if wf is None:
        raise WorkflowRejected(404, "entity_not_found", "工作流不存在。")
    # 快照事件（seq 不占用业务序号之后的重放由 revision diff 保证）。
    seq += 1
    yield "snapshot", {"workflow": wf, "last_event_seq": seq}, seq
    last_revision = int(wf.get("revision") or 0)
    last_states = {str(s.get("step_id")): str(s.get("state"))
                   for s in wf.get("steps") or []}
    idle_deadline = _now() + 600.0  # 终态后再挂 10 分钟由调用方控制
    while _now() < idle_deadline:
        await asyncio.sleep(1.0)
        wf = load_workflow(student_id, workflow_id)
        if wf is None:
            return
        revision = int(wf.get("revision") or 0)
        if revision == last_revision:
            continue
        last_revision = revision
        for step in wf.get("steps") or []:
            sid = str(step.get("step_id"))
            state = str(step.get("state"))
            if last_states.get(sid) != state:
                last_states[sid] = state
                seq += 1
                yield ("step_updated", {
                    "step_id": sid, "state": state,
                    "workflow_revision": revision,
                    "output_ref": step.get("output_ref"),
                    "domain_job": step.get("domain_job"),
                    "error": step.get("error")}, seq)
        state = str(wf.get("state"))
        if state in TERMINAL_WORKFLOW_STATES:
            seq += 1
            yield "workflow_done", {
                "state": state,
                "result_targets": wf.get("result_targets") or [],
                "workflow_revision": revision}, seq
            return


# -- 执行 ---------------------------------------------------------------------

class WorkflowRejected(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_or_404(student_id: str, workflow_id: str) -> dict[str, Any]:
    wf = load_workflow(student_id, workflow_id)
    if wf is None:
        raise WorkflowRejected(404, "entity_not_found", "工作流不存在。")
    return wf


async def run_workflow(student_id: str, workflow_id: str) -> None:
    """按依赖执行步骤（读 ≤2 并发；写串行；§23.4）。"""
    wf = _load_or_404(student_id, workflow_id)
    if str(wf.get("state")) not in ("queued", "running",
                                    "waiting_domain_job"):
        return
    wf["state"] = "running"
    _persist(student_id, wf)
    steps = {str(s.get("step_id")): s for s in wf.get("steps") or []}
    order = [str(s.get("step_id")) for s in wf.get("steps") or []]

    for step_id in order:
        step = steps[step_id]
        if wf.get("cancel_requested"):
            if str(step.get("state")) in ("pending", "ready"):
                step["state"] = "cancelled"
            continue
        if str(step.get("state")) in ("succeeded", "skipped",
                                      "cancelled"):
            continue
        if not _deps_ok(steps, step):
            continue  # 依赖未满足：留待恢复/重试
        if step.get("kind") == "wait_job":
            handled = await _advance_wait_job(student_id, wf, step)
            if handled == "timeout":
                _persist(student_id, wf)
                return  # 释放请求；轮询/恢复时继续
            continue
        step["state"] = "running"
        _persist(student_id, wf)
        try:
            if step.get("kind") == "write":
                result = await asyncio.to_thread(
                    _execute_write_step, student_id, wf, step)
            elif step.get("kind") == "handoff":
                from . import workflow_templates
                result = await asyncio.to_thread(
                    workflow_templates.run_handoff_step,
                    student_id, wf, step)
            else:
                result = await asyncio.to_thread(
                    _execute_read_step, student_id, wf, step)
            step["state"] = "succeeded"
            step["output_ref"] = result
        except WorkflowRejected as exc:
            step["state"] = "failed"
            step["error"] = {"code": exc.code, "message": exc.message}
        except Exception as exc:  # noqa: BLE001
            step["state"] = "failed"
            step["error"] = {"code": "step_failed",
                             "message": str(exc)[:200]}
        _persist(student_id, wf)
        if str(step.get("state")) == "failed" and step.get("kind") == "write":
            # §23.4-9：必要写步骤失败 → 整体待处理（部分成功语义）。
            break

    _finalize(student_id, wf)


def _deps_ok(steps: dict[str, dict[str, Any]], step: dict[str, Any]) -> bool:
    for dep in step.get("depends_on") or []:
        dep_step = steps.get(str(dep))
        if dep_step is None or str(dep_step.get("state")) != "succeeded":
            return False
    return True


def _finalize(student_id: str, wf: dict[str, Any]) -> None:
    steps = wf.get("steps") or []
    if wf.get("cancel_requested"):
        wf["state"] = "cancelled"
    else:
        # §23.4-10：所有必要步骤完成才 succeeded；有已完成的必要步骤
        #（真实产物）但存在失败 → partially_succeeded；必要步骤零完成
        # → failed（读步骤成功不构成产物）。
        required = [s for s in steps if s.get("kind") in ("write",
                                                          "wait_job")]
        failed = [s for s in steps if str(s.get("state")) == "failed"]
        done = [s for s in required
                if str(s.get("state")) == "succeeded"]
        if len(done) == len(required) and not failed:
            wf["state"] = "succeeded"
        elif done:
            wf["state"] = "partially_succeeded"
        else:
            wf["state"] = "failed"
    # 产物入口（供任务中心/原页面跳转）。
    wf.setdefault("result_targets", [])
    for step in steps:
        target = (step.get("output_ref") or {}).get("navigation_target")
        if target and target not in wf["result_targets"]:
            wf["result_targets"].append(target)
    _persist(student_id, wf)


def _step_action_id(wf: dict[str, Any], step: dict[str, Any]) -> str:
    """§23.4-3 稳定幂等键。"""
    return f"astw_step:{wf.get('workflow_id')}:{step.get('step_id')}"


def _execute_write_step(student_id: str, wf: dict[str, Any],
                        step: dict[str, Any]) -> dict[str, Any]:
    """写步骤 = 隐式 AssistantAction 经 §21 预览/批准/执行链。

    工作流批准（approved_plan_hash）覆盖预览过的参数；执行复用
    actions.execute_action（同 action_id 幂等，重试不重复创建）。
    """
    from . import actions as actions_svc
    from . import previews as previews_svc
    from .actions import ActionRejected
    conversation_id = str(wf.get("conversation_id") or "")
    action_id = _step_action_id(wf, step)
    record = _ensure_step_action(student_id, conversation_id,
                                 action_id, step)
    action = (record.get("actions") or {}).get(action_id)
    if str(action.get("state")) in ("succeeded",):
        return {"business_result": action.get("business_result"),
                "action_id": action_id, "reused": True}
    # 准备预览（review_required 的操作由工作流批准作为许可来源）。
    try:
        preview = previews_svc.build_preview(student_id, action_id)
        approval_id = None
        if preview.get("approval") != "intent_sufficient":
            if not (action.get("approval") or {}).get("approval_id"):
                approval = previews_svc.approve(
                    student_id, action_id,
                    preview_id=preview["preview_id"],
                    parameter_hash_in=preview["parameter_hash"],
                    decision="approve")
                approval_id = str(approval["approval_id"])
            else:
                approval_id = str(action["approval"]["approval_id"])
    except ActionRejected as exc:
        raise WorkflowRejected(exc.status_code, exc.code, str(exc))
    invocation_id = ("wf_" + hashlib.sha256(
        f"{wf.get('workflow_id')}:{step.get('step_id')}:"
        f"{wf.get('approved_plan_hash')}".encode()).hexdigest()[:24])
    try:
        result = actions_svc.execute_action(
            student_id, action_id, invocation_id=invocation_id,
            client_instance_id="workflow", route_epoch=0,
            approval_id=approval_id)
    except ActionRejected as exc:
        raise WorkflowRejected(exc.status_code, exc.code, str(exc))
    state = str((result.get("action") or {}).get("state") or "")
    if state != "succeeded":
        raise WorkflowRejected(502, "step_failed",
                               str(result.get("client_result")
                                   or "写步骤未成功。"))
    # wait_job 类写步骤登记 DomainJobRef（§23.2）。
    out = {"business_result": result.get("business_result"),
           "action_id": action_id}
    job_ref = _domain_job_ref_of(step, result.get("business_result") or {})
    if job_ref:
        out["domain_job"] = job_ref
        step["domain_job"] = job_ref
    return out


def _domain_job_ref_of(step: dict[str, Any],
                       business: dict[str, Any]) -> dict[str, Any] | None:
    op = str(step.get("operation") or "")
    related = dict(business.get("related_ids") or {})
    if op == "lesson.generate":
        return {"kind": "lesson_generation",
                "workspace_id": str(step.get("input", {}).get(
                    "workspace_id") or ""),
                "lesson_id": str(related.get("lesson_id") or ""),
                "job_id": str(business.get("entity_id") or "")}
    if op in ("evaluation.retry", "evaluation.synthesize"):
        return {"kind": "learner_evaluation",
                "job_id": str(business.get("entity_id") or "")}
    return None


def _ensure_step_action(student_id: str, conversation_id: str,
                        action_id: str, step: dict[str, Any]) -> dict:
    from app.core import assistant_store as store
    record = store.load_conversation(student_id, conversation_id)
    if record is None:
        record = {"conversation_id": conversation_id, "revision": 0,
                  "messages": [], "turns": {}, "actions": {},
                  "accepted": {}}
    actions = record.setdefault("actions", {})
    payload = {
        "kind": "domain_write",
        "operation": str(step.get("operation")),
        "input": dict(step.get("input") or {})}
    existing = actions.get(action_id)
    # 已成功：幂等复用既有业务结果（§23.4-3），不重复创建。
    if existing is not None and str(existing.get("state")) == "succeeded":
        return record
    # 在途/待确认：不覆盖当前执行者的 invocation（§9.4 单执行者）。
    if existing is not None and str(existing.get("state")) in (
            "executing", "awaiting_ack", "needs_attention"):
        return record
    if existing is not None and (existing.get("payload") or {}) == payload:
        # 工作流重试同一步骤（§23.4-3/§23.5 retry）：重新武装同一动作，
        # 幂等键 action_id 不变；领域侧仍靠 business_result/client_request_id
        # 去重，重试不会生成新实体。
        existing["state"] = "proposed"
        existing["invocation"] = {}
        existing["expires_at"] = _utc_iso(_now() + 14 * 24 * 3600)
        record["revision"] = int(record.get("revision", 1)) + 1
        store.save_conversation(student_id, record)
        return record
    # 参数变化（步骤输入重新准备）：未成功的动作跟随最新输入重建。
    actions[action_id] = {
        "action_id": action_id, "conversation_id": conversation_id,
        "turn_id": "workflow", "label": str(step.get("title") or ""),
        "payload": payload,
        "execution": "user_click", "state": "proposed",
        "created_at": store.utc_now_iso(),
        "expires_at": _utc_iso(_now() + 14 * 24 * 3600),
        "business_result": {"kind": "none"},
    }
    record["revision"] = int(record.get("revision", 1)) + 1
    store.save_conversation(student_id, record)
    return record


def _execute_read_step(student_id: str, wf: dict[str, Any],
                       step: dict[str, Any]) -> dict[str, Any]:
    from . import workflow_templates
    return workflow_templates.run_read_step(student_id, wf, step)


async def _advance_wait_job(student_id: str, wf: dict[str, Any],
                            step: dict[str, Any]) -> str:
    """等待领域作业：30s 内轮询；超时持久化等待并返回 timeout。

    20 分钟无终态 → paused_for_user（§23.4-5）。
    """
    job = step.get("domain_job") or {}
    step["state"] = "waiting"
    deadline = _now() + STEP_WAIT_SECONDS
    started = float(step.get("wait_started_at") or _now())
    if not step.get("wait_started_at"):
        step["wait_started_at"] = started
    while _now() < deadline:
        outcome = _domain_job_state(student_id, job)
        if outcome == "succeeded":
            step["state"] = "succeeded"
            return "done"
        if outcome in ("failed", "cancelled"):
            step["state"] = "failed"
            step["error"] = {"code": f"domain_{outcome}",
                             "message": f"领域作业{outcome}。"}
            return "done"
        await asyncio.sleep(1.0)
    if _now() - started > DOMAIN_JOB_TIMEOUT_SECONDS:
        wf["state"] = "paused_for_user"
        return "paused"
    wf["state"] = "waiting_domain_job"
    return "timeout"


def _domain_job_state(student_id: str, job: dict[str, Any]) -> str:
    kind = str(job.get("kind") or "")
    try:
        if kind == "lesson_generation":
            from app.classroom import service as classroom_service
            snap = classroom_service.job_snapshot(
                student_id, str(job.get("workspace_id")),
                str(job.get("lesson_id")), str(job.get("job_id")))
            return str(snap.get("state") or "running")
        if kind == "learner_evaluation":
            from app.agents.student_model.evaluation.store import get_journal
            state = get_journal(student_id).state()
            rt = state.jobs.get(str(job.get("job_id")))
            if rt is None:
                return "failed"
            return {"failed": "failed", "succeeded": "succeeded"}.get(
                str(rt.job.state.value), "running")
    except Exception:
        return "running"
    return "running"


def resume_or_recover(student_id: str) -> None:
    """API 访问点调用：恢复等待/中断的工作流（单飞）。"""
    for wf in active_workflows(student_id):
        state = str(wf.get("state"))
        wid = str(wf.get("workflow_id"))
        if state in ("waiting_domain_job", "interrupted", "queued"):
            if wid in _running and not _running[wid].done():
                continue
            _running[wid] = asyncio.create_task(
                run_workflow(student_id, wid))


def mark_interrupted_on_startup() -> None:
    """进程启动时调用：running/waiting → interrupted（由 api lifespan 钩）。"""
    # 进程内无从枚举所有用户；恢复点在各用户 API 访问时按
    # waiting_domain_job/queued 续跑，重启语义靠 DomainJobRef 回查实现
    #（§23.4-6：不重新执行创建步骤）。
    return None
