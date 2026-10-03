"""动作执行策略（A10）。

execution 只由这里的规则表依据「本轮用户意图 + 已校验工具结果」决定；
文档、教材、模型文本或历史引用不能授予执行权。首版白名单动作
navigate / open_workspace_form；prepare_lesson / resume_lesson /
launch_task / handoff_* / open_classroom_question 在 A13 接入领域链路
后才允许被提出。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.site_assistant.store import mint_action_id, utc_now_iso
from app.schemas.assistant import AssistantRouteId

from . import catalog
from .intent import ParsedIntent

ACTION_TTL_SECONDS = 600  # §9.1：首版 action 默认 10 分钟有效

# 已实现 execute 链路的 payload kind（§9.1；A10 导航族 + A13 交接族 +
# §21 领域写族）——与 capabilities 单一来源。
IMPLEMENTED_ACTION_KINDS = (
    "navigate",
    "open_workspace_form",
    "prepare_lesson",
    "resume_lesson",
    "launch_task",
    "handoff_chat",
    "handoff_note",
    "open_classroom_question",
    "domain_write",
    # §21.1/§23：工作流发起（C01；能力开关见 capabilities.workflows_enabled）
    "start_workflow",
    # §21.1/§25.1：订阅管理（C04；需 SITE_ASSISTANT_PROACTIVE_ENABLED）
    "manage_subscription",
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_action(*, conversation_id: str, turn_id: str, label: str,
                payload: dict[str, Any], execution: str,
                page_epoch: int) -> dict[str, Any]:
    created = _now()
    return {
        "action_id": mint_action_id(),
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "label": label[:200],
        "payload": payload,
        "execution": execution,
        "state": "proposed",
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(seconds=ACTION_TTL_SECONDS)).isoformat(),
        "route_epoch": int(page_epoch),
        # 运行期内部字段（不进入 public_action / schema）。
        "business_result": {"kind": "none"},
        "client_result": None,
        "invocation": None,
    }


def decide_actions(
    parsed: ParsedIntent,
    results: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    conversation_id: str,
    turn_id: str,
    lang: str = "zh",
    page_epoch: int = 0,
    page_context: dict[str, Any] | None = None,
    timezone_name: str = "UTC",
) -> list[dict[str, Any]]:
    """§9.2 规则表：唯一目标 + 明确动词 → automatic；其余 user_click。

    多候选 / 参数缺失 / 仅询问 → 不生成动作（choices 卡负责选择）。
    §21 领域写提案（B03 首批操作）由 prepare_action 分支确定性解析：
    intent_sufficient 操作可 automatic；review_required 一律 user_click，
    execute 必须携带审批许可。
    """
    actions: list[dict[str, Any]] = []
    zh = lang == "zh"

    if parsed.kind == "navigate":
        unique = _unique_navigate_target(parsed, results)
        if unique is not None:
            target, hint = unique
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=_navigate_label(target, zh, hint),
                payload={"kind": "navigate", "target": target},
                execution="automatic", page_epoch=page_epoch))

    if parsed.kind == "search":
        unique = _unique_navigate_target(parsed, results)
        if unique is not None:
            # 只是询问「我的…在哪」：给入口但不自动跳页（§9.2-3）。
            target, hint = unique
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=_navigate_label(target, zh, hint),
                payload={"kind": "navigate", "target": target},
                execution="user_click", page_epoch=page_epoch))

    # -- A13 课程与任务交接（§9.2/§19.6/§19.7） ---------------------------
    scope_workspace = None
    if str((meta.get("scope") or {}).get("mode")) == "workspace":
        ids = (meta.get("scope") or {}).get("workspace_ids") or []
        scope_workspace = ids[0] if ids else None

    if parsed.kind == "prepare_action":
        # §23.1/C03 跨模块流程优先：明确的多步目标整链交给工作流（先
        # 预览后批准，user_click），单步写提案随后。
        flow = _propose_start_workflow(str(parsed.text or ""), zh=zh,
                                       scope_workspace=scope_workspace)
        write = None
        subscription = None
        if flow is None:
            subscription = _propose_manage_subscription(
                str(parsed.text or ""), zh=zh)
        if flow is None and subscription is None:
            # §21 领域写提案优先：参数完整才生成，缺项回落到普通回答。
            write = _propose_domain_write(
                parsed, results, zh=zh, page_context=page_context,
                timezone_name=timezone_name,
                student_id=str(meta.get("student_id") or ""))
        if flow is not None:
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=flow["label"],
                payload={"kind": "start_workflow",
                         "template": flow["template"],
                         "objective": flow["objective"],
                         "scope": flow.get("scope") or {},
                         "selection_ids": flow.get("selection_ids") or {}},
                execution="user_click", page_epoch=page_epoch))
        elif subscription is not None:
            # §25.1 自然语言订阅：默认建议时间仅预填，确认即建。
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=subscription["label"],
                payload={"kind": "manage_subscription",
                         "operation": subscription["operation"],
                         "input": subscription["input"]},
                execution="user_click", page_epoch=page_epoch))
        elif write is not None:
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=write["label"],
                payload={"kind": "domain_write",
                         "operation": write["operation"],
                         "input": write["input"]},
                execution=write["execution"], page_epoch=page_epoch))
        elif zh and _propose_local_preference(str(parsed.text or "")):
            # §22.4 本地 UI 偏好：客户端执行（useUIStore），不经服务端写。
            local = _propose_local_preference(str(parsed.text or ""))
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=local["label"],
                payload={"kind": "set_local_preference",
                         "key": local["key"], "value": local["value"]},
                execution="user_click", page_epoch=page_epoch))
        else:
            course = next((r for r in results
                           if r.get("tool") == "find_course"), None)
            resume = ((course or {}).get("data") or {}).get("resume")
            if resume and resume.get("lesson_id"):
                # 「继续上次课程」+ 唯一未完成 run → automatic（§19.7-3）。
                run = resume.get("run") or {}
                actions.append(_new_action(
                    conversation_id=conversation_id, turn_id=turn_id,
                    label=(f"继续「{str(resume.get('title', ''))[:30]}」"
                           if zh else f"Resume \"{resume.get('title', '')}\""),
                    payload={"kind": "resume_lesson",
                             "workspace_id": resume.get("workspace_id"),
                             "lesson_id": resume.get("lesson_id"),
                             "lesson_revision": int(
                                 run.get("lesson_revision") or 0) or None,
                             "run_id": run.get("run_id")},
                    execution="automatic", page_epoch=page_epoch))
            elif scope_workspace:
                # 备课草稿：预填主题与明确时长，不自动生成（§9.5/§19.6）。
                topic = _lesson_topic(parsed.text)
                if topic:
                    draft: dict[str, Any] = {"topic": topic}
                    duration = _lesson_duration(parsed.text)
                    if duration is not None:
                        draft["duration_minutes"] = duration
                    actions.append(_new_action(
                        conversation_id=conversation_id, turn_id=turn_id,
                        label="打开备课表单（预填主题）" if zh
                        else "Open lesson form (prefilled)",
                        payload={"kind": "prepare_lesson",
                                 "workspace_id": scope_workspace,
                                 "draft": draft},
                        execution="user_click", page_epoch=page_epoch))

    if parsed.kind == "planning_advice":
        # 「开始/去做这个任务」且只有一个未完成任务 → launch（§19.7）。
        wants_launch = any(token in parsed.text for token in (
            "开始", "启动", "去做", "做这个", "现在做", "start"))
        tasks = next((r for r in results
                      if r.get("tool") == "get_saved_tasks"), None)
        open_tasks = ((tasks or {}).get("data") or {}).get("open") or []
        today = ((tasks or {}).get("data") or {}).get("today") or []
        seen_ids: set[str] = set()
        pool = []
        for task in list(today) + list(open_tasks):
            tid = str(task.get("id") or "")
            if not tid or tid in seen_ids:
                continue
            if (task.get("status") or "pending") == "completed":
                continue
            seen_ids.add(tid)
            pool.append(task)
        if wants_launch and len(pool) == 1 and pool[0].get("id"):
            task = pool[0]
            task_title = str(task.get("title") or task.get("id"))[:24]
            actions.append(_new_action(
                conversation_id=conversation_id, turn_id=turn_id,
                label=(f"开始任务「{task_title}」" if zh else "Start task"),
                payload={"kind": "launch_task", "task_id": task["id"]},
                execution="user_click", page_epoch=page_epoch))

    return actions[:1]


# -- §21 领域写提案（B03 首批 + B05 聊天/资料；B06–B10 在此追加） ----------

_TASK_CREATE_RE = re.compile(
    r"(?:帮我|请|麻烦|我想|我要)?(?:创建|添加|新建|记|加)"
    r"[^，,。；;！？?]{0,16}?任务(?:[，,：:\s]+(.+))?")
_TASK_COMPLETE_RE = re.compile(
    r"(?:我)?(?:已经?|刚)?(?:完成|做完|搞定了?|标记完成)(?:了|好)?"
    r"(?:这|这个|今天|今天的|当日)?任务(?:[，,：:\s]+(.+))?")
_CHAT_RENAME_RE = re.compile(
    r"(?:把|给)?(?:这个|当前)?(?:对话|会话)(?:重新)?"
    r"(?:命名|改名|改名为|改叫|重命名为)(?:为)?[，,：:\s]*(.+)")
_SCHEDULE_RE = re.compile(
    r"(?:把)?每(?:天|日)(?:的)?(?:学习)?(?:时间|时长|预算|时间预算)?"
    r"(?:改成?|设为?|调整为?|调到|改为|调整到)\s*(\d{1,3})\s*分钟")
_NOTE_CREATE_RE = re.compile(
    r"(?:帮我|请|麻烦|我想|我要)?(?:记|写|保存|整理成?)"
    r"(?:一?[个条份])?笔记[，,：:\s]*(.+)")

# B05：聊天/资料/归档（§21.3）
_WORKSPACE_CREATE_RE = re.compile(
    r"(?:帮我|请|麻烦|我想|我要)?(?:创建|新建|添加|建)"
    r"(?:一?个)?(?:学习|辅导)?(?:工作区|学习区|专区)"
    r"(?:[，,：:\s]*(?:名为|叫做|叫|名称是?|名字是?))?"
    r"[「\"“]?\s*([^「」\"”，,。；;！？?]{1,60})\s*[」\"”]?")
_FOLDER_CREATE_RE = re.compile(
    r"(?:帮我|请|麻烦|我想|我要)?(?:创建|新建|建)"
    r"(?:一?个)?(?:资料)?文件夹"
    r"(?:[，,：:\s]*(?:名为|叫做|叫|名称是?|名字是?))?"
    r"[「\"“]?\s*([^「」\"”，,。；;！？?]{1,60})\s*[」\"”]?")
_CHAT_ARCHIVE_RE = re.compile(
    r"(?:归档|删除|删掉|移入归档|收进归档|收进回收站)"
    r"(?:掉|了)?(?:这个|当前|此|这)(?:对话|会话|聊天记录)")
_CHAT_MOVE_RE = re.compile(
    r"(?:把)?(?:这个|当前|此)(?:对话|会话)"
    r"(?:移动|移|挪|转移|移动到|移到|挪到|转移到)"
    r"(?:到|进|入|去)?(?:名为)?(?:工作区|学习区|专区)?"
    r"[「\"“]?\s*([^「」\"”，,。；;！？?]{1,60})\s*[」\"”]?")

# B06：笔记 / 学习编排（§21.3）。笔记操作要求页面正在该笔记上（页面
# 实体提供 note_id），任务改期/改名经 get_saved_tasks 结果唯一定位。
_NOTE_APPEND_RE = re.compile(
    r"(?:在|给)?(?:这篇|当前|此)(?:篇)?笔记(?:的)?"
    r"(?:末尾|后面|最后|结尾)?(?:追加|添加|加上|补充)[：:，,\s]*(.+)", re.S)
_NOTE_REVIEW_RE = re.compile(
    r"(开启|打开|启用|关闭|取消|停止)(?:这篇|当前|此)?笔记的?"
    r"(?:间隔)?复习(?:计划)?")
_GOAL_CREATE_RE = re.compile(
    r"(?:帮我|请|我想|我要)?(?:创建|新建|添加|设定|制定|建立)"
    r"(?:一?个)?(?:学习)?目标"
    r"(?:[，,：:\s]*(?:名为|叫做|叫|名称是?|名字是?))?"
    r"[「\"“]?\s*([^「」\"”，,。；;！？?]{1,60})\s*[」\"”]?")
_PLAN_REGEN_RE = re.compile(
    r"(?:重新生成|重新规划|重排|重新安排)(?:我的)?(?:学习|复习)?计划")
_TASK_DAY_MOVE_RE = re.compile(
    r"(?:把)?(?:这个|当前)?任务[「\"“]?([^「」\"」，,。]{1,60})[」\"”]?"
    r"(?:改到|移到|挪到|移至|改至|调整到|换到|改在|安排到)"
    r"\s*(今天|明天|后天|\d{4}-\d{2}-\d{2})")
_TASK_RENAME_RE = re.compile(
    r"(?:把)?(?:这个|当前)?任务[「\"“]?([^「」\"」，,。]{1,60})[」\"”]?"
    r"(?:改名为?|重命名为?|改名成|改成)[：:，,\s]*"
    r"[「\"“]?([^「」\"”，,。；;！？?]{1,60})[」\"”]?")


# B09：偏好（§22.4）。回答长度是服务端助手偏好；记忆窗口走域服务；
# 主题/字号/语言是本地 UI 偏好（set_local_preference，客户端执行）。
_PREF_LENGTH_BRIEF_RE = re.compile(
    r"(?:以后|回答|回复|答案|说得?)[^，,。；;？?]{0,6}"
    r"(?:简短|简洁)(?:一点|些)?")
_PREF_LENGTH_DETAILED_RE = re.compile(
    r"(?:以后|回答|回复|答案|说得?)[^，,。；;？?]{0,6}"
    r"(?:更?详细)(?:一点|些)?")
_MEMORY_WINDOW_RE = re.compile(
    r"(?:记忆|跨会话记忆)(?:窗口)?(?:改成?|调到?|设为?|设置为?|改为?)"
    r"\s*(\d{1,3})\s*(?:轮|条)")
_THEME_DARK_RE = re.compile(
    r"(?:切换|改成|换成|打开|启用|用)深色(?:模式|主题)")
_THEME_LIGHT_RE = re.compile(
    r"(?:切换|改成|换成|恢复|用)浅色(?:模式|主题)")
_FONT_UP_RE = re.compile(
    r"(?:字号|字体)(?:调大|放大|大)(?:一点|些)?|放大(?:字号|字体)")
_LANG_EN_RE = re.compile(
    r"(?:界面|网站|页面)?(?:语言|界面)(?:切换|改成|换成|设为)?(?:英文|英语)|"
    r"切换(?:成|到)英文")

# 执行策略以 previews.policy_for 为单一来源（含按输入的动态升级，
# 如 workspace.update_sources 移除需预览）；此处不再维护副本。


def _local_date_iso(offset_days: int, timezone_name: str) -> str:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return (datetime.now(tz).date()
            + timedelta(days=offset_days)).isoformat()


def _propose_domain_write(
    parsed: ParsedIntent,
    results: list[dict[str, Any]],
    *, zh: bool, page_context: dict[str, Any] | None,
    timezone_name: str = "UTC",
    student_id: str = "",
) -> dict[str, Any] | None:
    """从明确的写请求文字确定性解析 operation+input（§21.3）。

    参数不完整一律返回 None（不猜测，回落普通回答）。input 字段必须与
    schemas 中对应 Input 模型严格一致（extra=forbid）。student_id 供
    需要读取业务版本的提案（note.append 的 base_revision、
    plan.regenerate 的 plan revision）使用；为空时这些提案不出。
    """
    from app.core.config import settings
    if not settings.site_assistant_actions_enabled:
        return None  # §26.5 ACTIONS 开关关闭：不提出领域写动作
    text = str(parsed.text or "").strip()

    proposal = _propose_task_create(text, timezone_name)
    if proposal is None:
        proposal = _propose_task_complete(text, results)
    if proposal is None:
        proposal = _propose_chat_rename(text, page_context)
    if proposal is None:
        proposal = _propose_schedule_update(text)
    if proposal is None:
        proposal = _propose_note_create(text)
    if proposal is None:
        proposal = _propose_workspace_create(text)
    if proposal is None:
        proposal = _propose_library_create_folder(text)
    if proposal is None:
        proposal = _propose_chat_archive(text, page_context)
    if proposal is None:
        proposal = _propose_chat_move_workspace(text, results, page_context)
    # -- B06 ---------------------------------------------------------------
    if proposal is None:
        proposal = _propose_note_append(text, page_context, student_id)
    if proposal is None:
        proposal = _propose_note_set_review(text, page_context, student_id)
    if proposal is None:
        proposal = _propose_goal_create(text)
    if proposal is None:
        proposal = _propose_plan_regenerate(text, student_id)
    if proposal is None:
        proposal = _propose_task_update(text, results, timezone_name)
    # -- B09（§22.4 偏好）------------------------------------------------
    if proposal is None:
        proposal = _propose_pref_length(text)
    if proposal is None:
        proposal = _propose_memory_window(text)
    if proposal is None:
        return None
    # 执行策略单一来源：previews.policy_for（含按输入动态升级）。
    from .previews import policy_for
    execution = ("automatic"
                 if policy_for(proposal["operation"],
                               proposal["input"]) == "intent_sufficient"
                 else "user_click")
    return {**proposal, "execution": execution}


def _page_note_id(page_context: dict[str, Any] | None) -> str:
    entity = (page_context or {}).get("entity") or {}
    if str(entity.get("kind") or "") == "note":
        return str(entity.get("id") or "")
    return ""


def _propose_note_append(text: str,
                         page_context: dict[str, Any] | None,
                         student_id: str) -> dict | None:
    m = _NOTE_APPEND_RE.search(text)
    if not m or not student_id:
        return None
    note_id = _page_note_id(page_context)
    if not note_id:
        return None  # 不在笔记页：不猜目标笔记
    addition = (m.group(1) or "").strip()
    if not addition or len(addition) > 8000:
        return None
    from app import notes as notes_store
    meta = notes_store.load_vault(student_id).find_note(note_id)
    if meta is None:
        return None
    return {"operation": "note.append",
            "input": {"note_id": note_id,
                      "base_revision": int(meta.get("revision") or 1),
                      "append_markdown": addition},
            "label": f"向笔记「{str(meta.get('title') or '')[:20]}」追加内容"}


def _propose_note_set_review(text: str,
                             page_context: dict[str, Any] | None,
                             student_id: str) -> dict | None:
    m = _NOTE_REVIEW_RE.search(text)
    if not m or not student_id:
        return None
    note_id = _page_note_id(page_context)
    if not note_id:
        return None
    from app import notes as notes_store
    meta = notes_store.load_vault(student_id).find_note(note_id)
    if meta is None:
        return None
    enabled = m.group(1) in ("开启", "打开", "启用")
    return {"operation": "note.set_review",
            "input": {"note_id": note_id, "enabled": enabled},
            "label": (f"开启笔记「{str(meta.get('title') or '')[:20]}」的复习计划"
                      if enabled else
                      f"关闭笔记「{str(meta.get('title') or '')[:20]}」的复习计划")}


def _propose_goal_create(text: str) -> dict | None:
    m = _GOAL_CREATE_RE.search(text)
    if not m:
        return None
    title = _clean_name(m.group(1))
    if len(title) < 2 or len(title) > 60:
        return None
    return {"operation": "goal.create", "input": {"title": title},
            "label": f"创建学习目标「{title[:24]}」（将重新规划，先预览）"}


def _propose_plan_regenerate(text: str, student_id: str) -> dict | None:
    if not _PLAN_REGEN_RE.search(text) or not student_id:
        return None
    from app.agents.learning_orchestration.manager import (
        get_orchestration_service)
    state = get_orchestration_service()._load(student_id)
    if not state.has_goals:
        return None  # 无目标：重规划无意义，回落解释性回答
    revision = (f"{len(state.weekly_plan or [])}:"
                f"{int(state.last_plan_attempt or 0)}")
    return {"operation": "plan.regenerate",
            "input": {"expected_plan_revision": revision},
            "label": "重新生成学习计划（先看新计划候选）"}


def _saved_task_pool(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = next((r for r in results
                  if r.get("tool") == "get_saved_tasks"), None)
    data = ((tasks or {}).get("data") or {})
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in list(data.get("today") or []) + list(data.get("open") or []):
        tid = str(task.get("id") or "")
        if tid and tid not in seen:
            seen.add(tid)
            pool.append(task)
    return pool


def _match_saved_task(pool: list[dict[str, Any]], needle: str):
    """按标题精确/子串唯一匹配任务；多义或零命中返回 None。"""
    def _norm(t: str) -> str:
        return "".join(str(t).lower().split())
    key = _norm(needle)
    if not key:
        return None
    exact = [t for t in pool if _norm(t.get("title") or "") == key]
    matched = exact or [t for t in pool
                        if key in _norm(t.get("title") or "")]
    if len(matched) != 1:
        return None
    return matched[0]


def _propose_task_update(text: str, results: list[dict[str, Any]],
                         timezone_name: str) -> dict | None:
    pool = _saved_task_pool(results)
    if not pool:
        return None
    m = _TASK_DAY_MOVE_RE.search(text)
    if m:
        task = _match_saved_task(pool, _clean_name(m.group(1)))
        if task is None:
            return None
        word = m.group(2)
        offsets = {"今天": 0, "明天": 1, "后天": 2}
        day = (word if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word)
               else _local_date_iso(offsets[word], timezone_name))
        return {"operation": "task.update",
                "input": {"task_id": str(task.get("id")),
                          "day": day},
                "label": (f"把任务「{str(task.get('title') or '')[:20]}」"
                          f"改到 {day}")}
    m = _TASK_RENAME_RE.search(text)
    if m:
        task = _match_saved_task(pool, _clean_name(m.group(1)))
        new_title = _clean_name(m.group(2))
        if task is None or len(new_title) < 2:
            return None
        return {"operation": "task.update",
                "input": {"task_id": str(task.get("id")),
                          "title": new_title},
                "label": (f"把任务「{str(task.get('title') or '')[:20]}」"
                          f"改名为「{new_title[:20]}」")}
    return None


def _propose_task_create(text: str, timezone_name: str) -> dict | None:
    m = _TASK_CREATE_RE.search(text)
    if not m:
        return None
    rest = (m.group(1) or "").strip()
    if not rest:
        return None
    # 时长限定词从全文提取；出现在标题里的部分移除
    estimate = None
    em = re.search(r"(?:预计|用时|大概|大约)?\s*(\d{1,3})\s*分钟", text)
    if em:
        value = int(em.group(1))
        if 1 <= value <= 480:
            estimate = value
            rest = rest.replace(text[em.start():em.end()], "").strip()
    # 日期限定词（今天/明天/显式日期）从全文提取
    day = ""
    if re.search(r"今天|当日", text):
        day = _local_date_iso(0, timezone_name)
        rest = re.sub(r"今天|当日", "", rest).strip()
    elif "明天" in text:
        day = _local_date_iso(1, timezone_name)
        rest = rest.replace("明天", "").strip()
    else:
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if dm:
            day = dm.group(1)
            rest = rest.replace(day, "").strip()
    # 清理连接词与残留标点
    title = re.sub(r"^[：:，,、到的\s]+|[，,。！!？?；;\s]+$", "", rest)
    title = re.sub(r"\s{2,}", " ", title)
    if len(title) < 2 or len(title) > 200:
        return None
    data: dict[str, Any] = {"title": title, "day": day}
    if estimate is not None:
        data["estimate_minutes"] = estimate
    return {"operation": "task.create", "input": data,
            "label": f"创建学习任务「{title[:24]}」"}


def _propose_task_complete(text: str, results: list[dict[str, Any]]) -> dict | None:
    m = _TASK_COMPLETE_RE.search(text)
    if not m:
        return None
    tasks = next((r for r in results
                  if r.get("tool") == "get_saved_tasks"), None)
    data = ((tasks or {}).get("data") or {})
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in list(data.get("today") or []) + list(data.get("open") or []):
        tid = str(task.get("id") or "")
        if not tid or tid in seen:
            continue
        if (task.get("status") or "pending") == "completed":
            continue
        seen.add(tid)
        pool.append(task)
    rest = (m.group(1) or "").strip()
    target = None
    if rest:
        # 标题包含匹配；多命中不给动作（choices 负责）
        matched = [t for t in pool
                   if rest in str(t.get("title") or "")]
        if len(matched) == 1:
            target = matched[0]
    elif len(pool) == 1:
        target = pool[0]
    if target is None:
        return None
    status = str(target.get("status") or "pending")
    if status not in ("pending", "in_progress", "overdue"):
        return None
    title = str(target.get("title") or target.get("id"))[:24]
    return {"operation": "task.complete",
            "input": {"task_id": str(target.get("id")),
                      "expected_status": status},
            "label": f"标记任务完成「{title}」"}


def _propose_chat_rename(text: str,
                         page_context: dict[str, Any] | None) -> dict | None:
    m = _CHAT_RENAME_RE.search(text)
    if not m:
        return None
    ctx = page_context or {}
    entity = ctx.get("entity") or {}
    if str(entity.get("kind") or "") != "chat":
        return None
    sid = str(entity.get("id") or "")
    title = m.group(1).strip().strip("「」\"'“”")
    if not sid or len(title) < 1 or len(title) > 60:
        return None
    return {"operation": "chat.rename",
            "input": {"session_id": sid, "title": title},
            "label": f"把对话重命名为「{title[:20]}」"}


def _propose_schedule_update(text: str) -> dict | None:
    m = _SCHEDULE_RE.search(text)
    if not m:
        return None
    minutes = int(m.group(1))
    if not 5 <= minutes <= 480:
        return None
    return {"operation": "schedule.update",
            "input": {"daily_minutes": minutes},
            "label": f"把每日学习时间预算改为 {minutes} 分钟"}


def _propose_note_create(text: str) -> dict | None:
    m = _NOTE_CREATE_RE.search(text)
    if not m:
        return None
    content = m.group(1).strip()
    if len(content) < 2 or len(content) > 12000:
        return None
    tm = re.search(r"标题[为是：:\s]+\s*([^\s，,。]{1,120})", content)
    if tm:
        title = tm.group(1)
    else:
        first_line = content.splitlines()[0].strip()
        title = (first_line or content)[:20]
    return {"operation": "note.create",
            "input": {"title": title, "content": content},
            "label": f"创建笔记「{title[:20]}」（需预览确认）"}


def _clean_name(raw: str) -> str:
    name = re.sub(r"^[：:，,、到的\s]+|[，,。！!？?；;\s]+$", "",
                  (raw or "").strip())
    return re.sub(r"\s{2,}", " ", name)


def _propose_workspace_create(text: str) -> dict | None:
    m = _WORKSPACE_CREATE_RE.search(text)
    if not m:
        return None
    name = _clean_name(m.group(1))
    if len(name) < 2 or len(name) > 60:
        return None
    return {"operation": "workspace.create",
            "input": {"name": name},
            "label": f"创建辅导区「{name[:24]}」"}


def _propose_library_create_folder(text: str) -> dict | None:
    m = _FOLDER_CREATE_RE.search(text)
    if not m:
        return None
    name = _clean_name(m.group(1))
    if len(name) < 2 or len(name) > 60:
        return None
    return {"operation": "library.create_folder",
            "input": {"name": name},
            "label": f"创建资料文件夹「{name[:24]}」"}


def _page_chat_id(page_context: dict[str, Any] | None) -> str:
    ctx = page_context or {}
    entity = ctx.get("entity") or {}
    if str(entity.get("kind") or "") == "chat":
        return str(entity.get("id") or "")
    return ""


def _propose_chat_archive(text: str,
                          page_context: dict[str, Any] | None) -> dict | None:
    if not _CHAT_ARCHIVE_RE.search(text):
        return None
    sid = _page_chat_id(page_context)
    if not sid:
        return None
    return {"operation": "chat.archive",
            "input": {"session_id": sid},
            "label": "归档当前对话（可在归档页恢复）"}


def _propose_chat_move_workspace(
        text: str, results: list[dict[str, Any]],
        page_context: dict[str, Any] | None) -> dict | None:
    m = _CHAT_MOVE_RE.search(text)
    if not m:
        return None
    sid = _page_chat_id(page_context)
    if not sid:
        return None
    name = _clean_name(m.group(1))
    if len(name) < 2:
        return None
    # 工作区名从 resolve_destination 结果解析；精确名优先，其次子串。
    # 唯一命中才给动作（多候选由 choices 卡负责，零命中不猜测）。
    dest = next((r for r in results
                 if r.get("tool") == "resolve_destination"), None)
    candidates = ((dest or {}).get("data") or {}).get("candidates") or []
    workspaces = [c for c in candidates if c.get("kind") == "workspace"]
    needle = "".join(name.lower().split())

    def _norm(title: str) -> str:
        return "".join(str(title).lower().split())

    exact = [c for c in workspaces if _norm(c.get("title", "")) == needle]
    matched = exact or [c for c in workspaces
                        if needle in _norm(c.get("title", ""))]
    if len(matched) != 1:
        return None
    ws_id = str((matched[0].get("target") or {}).get("workspace_id") or "")
    if not ws_id:
        return None
    ws_name = str(matched[0].get("title") or ws_id)[:24]
    return {"operation": "chat.move_workspace",
            "input": {"session_id": sid, "workspace_id": ws_id},
            "label": f"把当前对话移动到「{ws_name}」"}



def _propose_pref_length(text: str) -> dict | None:
    """ãä»¥ååç­ç®ç­ä¸ç¹ãâ assistant.preferencesï¼Â§22.4å¯ä¿å­ï¼ã"""
    if _PREF_LENGTH_BRIEF_RE.search(text):
        return {"operation": "assistant.preferences",
                "input": {"response_length": "short"},
                "label": "æå©æé»è®¤åç­è®¾ä¸ºç®ç­"}
    if _PREF_LENGTH_DETAILED_RE.search(text):
        return {"operation": "assistant.preferences",
                "input": {"response_length": "detailed"},
                "label": "æå©æé»è®¤åç­è®¾ä¸ºè¯¦ç»"}
    return None


def _propose_memory_window(text: str) -> dict | None:
    m = _MEMORY_WINDOW_RE.search(text)
    if not m:
        return None
    window = int(m.group(1))
    if window < 5 or window > 200:
        return None
    return {"operation": "memory.set_window", "input": {"window": window},
            "label": f"æè·¨ä¼è¯è®°å¿çªå£æ¹ä¸º {window} è½®"}


def _propose_local_preference(text: str) -> dict | None:
    """æ¬å° UI åå¥½ï¼Â§22.4ï¼ï¼ä¸»é¢/å­å·/è¯­è¨å useUIStoreï¼å®¢æ·ç«¯æ§è¡ã"""
    if _THEME_DARK_RE.search(text):
        return {"key": "theme", "value": "dark", "label": "åæ¢å°æ·±è²ä¸»é¢"}
    if _THEME_LIGHT_RE.search(text):
        return {"key": "theme", "value": "light", "label": "åæ¢å°æµè²ä¸»é¢"}
    if _LANG_EN_RE.search(text):
        return {"key": "lang", "value": "en", "label": "æçé¢è¯­è¨åæ¢ä¸ºè±æ"}
    if _FONT_UP_RE.search(text):
        return {"key": "font_scale", "value": "1.25",
                "label": "æ¾å¤§çé¢å­å·"}
    return None


def _unique_navigate_target(
    parsed: ParsedIntent, results: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """唯一目标；多候选返回 None 不猜测。

    resolve_destination 是对实际查询的权威解析：其唯一候选（模块/
    工作区/页面实体/具名实体深链）优先；无候选时回落意图阶段的模块
    命中。返回 (target, 标题提示) 供动作卡命名。
    """
    dest = next((r for r in results
                 if r.get("tool") == "resolve_destination"), None)
    candidates = ((dest or {}).get("data") or {}).get("candidates") or []
    if len(candidates) == 1:
        cand = candidates[0]
        target = cand.get("target")
        if isinstance(target, dict) and target.get("kind"):
            return target, str(cand.get("title") or "")
    module = parsed.primary_module
    if module is not None:
        return {"kind": "module", "route_id": module.route_id.value}, ""
    return None


# 实体深链动作卡的类名（中文；英文统一 "open"）。
_ENTITY_KIND_LABELS = {
    "note": "笔记", "file": "文件", "chat_session": "对话",
    "lesson": "课程", "workspace_chat": "工作区", "textbook": "教材",
    "concept": "概念", "task": "任务", "goal": "目标",
    "archive_item": "归档", "note_revision": "笔记",
    "file_folder": "文件夹",
}


def _navigate_label(target: dict[str, Any], zh: bool,
                    title: str = "") -> str:
    route_id = str(target.get("route_id") or "")
    if route_id:
        try:
            module = catalog.get_module(AssistantRouteId(route_id))
        except ValueError:
            module = None
        if module is not None:
            name = module.name.zh if zh else module.name.en
            return ("打开" if zh else "Open ") + name
    kind_label = _ENTITY_KIND_LABELS.get(str(target.get("kind") or ""))
    hint = str(title or "").strip()[:24]
    if zh:
        if kind_label and hint:
            return f"打开{kind_label}「{hint}」"
        if kind_label:
            return f"打开{kind_label}"
        if hint:
            return f"打开「{hint}」"
        return "打开目标页面"
    if kind_label and hint:
        return f"Open {kind_label} \"{hint}\""
    if kind_label:
        return f"Open {kind_label}"
    if hint:
        return f"Open \"{hint}\""
    return "Open target page"


# -- §23.1/C03 跨模块工作流提案（零 LLM 快路；先预览后批准） -------------
# 短语模式单一事实源在 intent.WORKFLOW_TEMPLATE_PATTERNS（intent 快路
# 与本处模板映射共用，防止两份正则漂移）。

_WORKFLOW_LABELS: dict[str, str] = {
    "setup_learning_space": "建立学习环境（工作区→目标→计划）",
    "weekly_review_to_plan": "本周复盘并安排下周",
    "weak_point_to_practice": "针对待解决点安排练习",
    "material_to_course": "把教材做成一节课",
    "organize_materials": "整理资料并生成摘要笔记",
    "continue_learning_session": "继续上次学习",
}


def _propose_start_workflow(text: str, *, zh: bool,
                            scope_workspace: str | None = None) -> dict | None:
    """§23.1 六模板文字快路：objective 原文截断；实体绑定留给计划预览。"""
    from app.core.config import settings
    if not settings.site_assistant_workflows_enabled:
        return None
    if not zh:
        return None  # 首版仅中文快路；英文经一般回答引导
    from .intent import WORKFLOW_TEMPLATE_PATTERNS
    stripped = text.strip()
    for template, pattern in WORKFLOW_TEMPLATE_PATTERNS:
        if pattern.search(stripped):
            scope: dict[str, str] = {}
            if scope_workspace:
                scope["workspace_id"] = scope_workspace
            return {"template": template,
                    "objective": stripped[:2000],
                    "scope": scope,
                    "selection_ids": {},
                    "label": _WORKFLOW_LABELS[template]}
    return None


# §25.1 订阅词汇 → kind；明确「订阅/提醒我」意图才提案。
_SUBSCRIPTION_WORDS: tuple[tuple[str, str], ...] = (
    ("学习简报|周报|每周简报", "weekly_brief"),
    ("每日任务|每天任务|当天任务提醒", "daily_tasks"),
    ("笔记复习|复习提醒|到期复习", "due_reviews"),
    ("未完成.{0,6}课程|课程提醒|继续上课提醒", "unfinished_course"),
)
_SUBSCRIPTION_INTENT_RE = re.compile(
    r"(订阅|开通|开启|提醒我|给我).{0,24}|"
    r"(每周|每天|每日|周报|简报).{0,16}(订阅|提醒)")
_TIME_RE = re.compile(r"(\d{1,2})[:：点时](\d{2})?分?")


def _propose_manage_subscription(text: str, *, zh: bool) -> dict | None:
    """§25.1：明确订阅意图 + 可识别种类；时间缺失用默认建议时间预填。"""
    from app.core.config import settings
    if not (settings.site_assistant_proactive_enabled and zh):
        return None
    if not _SUBSCRIPTION_INTENT_RE.search(text):
        return None
    for words, kind in _SUBSCRIPTION_WORDS:
        if re.search(words, text):
            m = _TIME_RE.search(text)
            local_time = (f"{int(m.group(1)):02d}:"
                          f"{int(m.group(2) or 0):02d}") if m else ""
            label = {"weekly_brief": "订阅每周学习简报",
                     "daily_tasks": "订阅每日任务提醒",
                     "due_reviews": "订阅笔记复习到期提醒",
                     "unfinished_course": "订阅未完成课程提醒"}[kind]
            if local_time:
                label += f"（{local_time}）"
            return {"operation": "subscribe",
                    "input": {"kind": kind, "local_time": local_time},
                    "label": label}
    return None


def _lesson_topic(text: str) -> str:
    """从备课请求文字提取主题：去掉请求语，保留核心内容（≤500 字）。"""
    import re
    topic = re.sub(
        r"(帮我|请帮我|麻烦|我想|我要|来一节|创建|生成|准备|备)(一节|一门)?"
        r"(的)?(课|课程|课件|PPT|ppt)|关于|的主题", "", text.strip())
    topic = re.sub(r"[。.！!？?…]+$", "", topic).strip()
    return topic[:500] if len(topic) >= 2 else ""


def _lesson_duration(text: str) -> int | None:
    """提取明确时长；只接受 CreateLessonRequest 的离散枚举，不猜测就近值。"""
    import re
    m = re.search(r"(\d{1,2})\s*分钟", text)
    if not m:
        return None
    value = int(m.group(1))
    return value if value in (5, 10, 15, 20, 30) else None
