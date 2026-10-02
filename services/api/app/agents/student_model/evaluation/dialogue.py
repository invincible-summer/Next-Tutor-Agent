"""对话来源划分与 eligibility（plan §7.2 / §2.2 / §13.1）。

代码只排除明确无关与重复（空消息/纯操作命令/问候感谢/单独“懂了、继续”/
已被 assessment 拥有的片段）；范围内教学语境一律进入 LLM applicability
判断——不得用“超过 N 字/有因为所以/术语多”当证据准入（A18）。

受理时机：学生消息已可靠保存（turn hook 内、save_session 之后）；
`evaluation_receipt_pending` 标记 + reconcile 覆盖保存与受理之间的故障
窗口（§13.1）。
"""
from __future__ import annotations

import re
from typing import Any

from . import schema as S
from .store import JournalState, get_journal, new_source_id

# §7.2 明确无关清单：只有这些能被代码直接跳过
_TRIVIAL_PATTERNS = re.compile(
    r"^(谢谢|感谢|多谢|好的|好滴|ok|okay|嗯+|哦+|哈+|"
    r"懂了|明白了|理解了|继续|下一页|下一个|继续讲|再继续|"
    r"你好|您好|hi|hello|嗨)[。.!！~～\s]*$",
    re.IGNORECASE)
_COMMAND_PATTERN = re.compile(
    r"^/(chat|new|clear|help|settings|voice|tts)\b", re.IGNORECASE)
# R03：单字符也可承载有意义作答（"3"/"是"/反例符号）；明确无关由
# _TRIVIAL/_COMMAND 清单排除，不用长度门（A18）
_MIN_CHARS = 1
_RECONCILE_WINDOW = 20      # R03：hook 故障回补窗口（条）


def is_eligible_text(text: str) -> bool:
    """代码级 eligibility 预筛（§7.2）：只跳过明确无关，其余交 LLM。"""
    t = (text or "").strip()
    if len(t) < _MIN_CHARS:
        return False
    if _COMMAND_PATTERN.match(t):
        return False
    if _TRIVIAL_PATTERNS.match(t):
        return False
    return True


def concept_candidates(student_id: str, workspace_id: str,
                       message_text: str, *,
                       prior_texts: list[str] | None = None,
                       max_candidates: int = 12) -> list[S.ConceptRef]:
    """概念候选（§7.2）：来自已选教材节点的严格名称匹配。

    R03：短答（"3"/"是"）本身不含概念名——并集上一条 assistant 追问
    与会话当前任务绑定概念（teaching goal），使追问后的简短回答仍可
    评价。零候选返回空列表——由 LLM/服务端记 concept_unresolved，
    不静默跳过。
    """
    try:
        from .scope import get_scope_resolver
        scope = get_scope_resolver().resolve(student_id, workspace_id)
    except Exception:
        return []
    extra = " ".join(t for t in (prior_texts or []) if t)
    hits: list[S.ConceptRef] = []
    for concept in scope.allowed_concepts:
        names = [concept.display_name] + _aliases_of(concept)
        matched = any(name and (name in message_text or
                                (extra and name in extra))
                      for name in names)
        if matched:
            hits.append(concept)
        if len(hits) >= max_candidates:
            break
    return hits


def _aliases_of(concept: S.ConceptRef) -> list[str]:
    return []   # ConceptRef 不带别名；严格名称匹配（§7.2），模糊匹配不做


def _message_observed_at(message: dict[str, Any]) -> str:
    """R03：消息 created_at（epoch 秒）→ ISO UTC；缺失回退当前时间。"""
    raw = message.get("created_at")
    try:
        epoch = float(raw)
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return S.utc_now_iso()


def _free_spans(n: int, owned: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """[0,n) 减去 owned 区间后的剩余片段（assessment 优先属主）。"""
    if n <= 0:
        return []
    if not owned:
        return [(0, n)]
    merged: list[list[int]] = []
    for s, e in sorted(owned):
        if s >= e:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    free: list[tuple[int, int]] = []
    pos = 0
    for s, e in merged:
        if s > pos:
            free.append((pos, min(s, n)))
        pos = max(pos, e)
        if pos >= n:
            break
    if pos < n:
        free.append((pos, n))
    return [(s, e) for s, e in free if e > s]


def prior_assessment_owned_spans(state: JournalState,
                                 message_ref: str) -> set[tuple[int, int]]:
    """已被 assessment 拥有的 span（§7.2 evidence_ownership 去重）。"""
    owned: set[tuple[int, int]] = set()
    for src in state.sources.values():
        if src.receipt.kind != S.SourceKind.ASSESSMENT:
            continue
        if src.receipt.message_ref != message_ref:
            continue
        for span in src.receipt.spans:
            owned.add((span.start, span.end))
    return owned


def register_dialogue_source(
        *, student_id: str, session: Any, message: dict[str, Any],
        run_hook: bool = True) -> str | None:
    """turn hook：学生消息已保存后受理 dialogue 来源（无 workspace 不受理
    长期评价，§2.3；不合法 workspace 不受理）。返回 source_id 或 None。

    幂等：同 (message_id) 已有 dialogue 来源时跳过。
    """
    from app.core import learner_runtime
    if not learner_runtime.evaluation_enabled():
        return None
    text = S.canonicalize_text(str(message.get("content") or ""))
    message_id = str(message.get("message_id") or "")
    session_ref = str(getattr(session, "session_id", "") or "")
    workspace_id = str(getattr(session, "workspace_id", "") or "")
    student_sid = str(getattr(session, "student_id", "") or student_id)
    if not message_id or not session_ref or not workspace_id:
        return None
    if not is_eligible_text(text):
        return None
    journal = get_journal(student_sid)
    state = journal.state()
    # 幂等：同消息已有可用 dialogue 来源
    for src in state.sources.values():
        if src.receipt.kind == S.SourceKind.DIALOGUE and \
                src.receipt.message_ref == message_id and \
                src.availability == "available":
            return src.receipt.source_id
    try:
        from .scope import get_scope_resolver
        scope = get_scope_resolver().resolve(student_sid, workspace_id)
        scope_revision = scope.scope_revision
    except Exception:
        return None      # 工作区不合法/不存在：不受理（404 语义在 API 层）
    # R03：observed_at 取消息首次可靠落盘时间（ensure_message_ids 冻结
    # 的 created_at），不用 hook 执行时间——跨零点/延迟受理归属正确
    observed_at = _message_observed_at(message)
    # R03：减去 assessment 已拥有的片段（题卡作答不重复评价）；整条被
    # 拥有 → 不受理（§7.2 evidence_ownership 单一属主）
    owned = prior_assessment_owned_spans(state, message_id)
    spans = _free_spans(len(text), owned)
    if not spans:
        return None
    source_id = new_source_id()
    receipt = S.SourceReceipt(
        source_id=source_id, source_revision=1, kind=S.SourceKind.DIALOGUE,
        observed_at=observed_at,
        workspace_id_at_observation=workspace_id,
        canonical_text=text, assistance_events=[],
        spans=[S.EvidenceSpanOwnership(
            start=s, end=e, owner="dialogue") for s, e in spans],
        source_session_ref=session_ref, message_ref=message_id,
        scope_revision=scope_revision)
    # 阶段C（§5/§6.2）：策略归属在受理时冻结（immediate=立即；
    # daily_midnight=零点后可认领），本地活动日按 observed_at 归日
    from app.core.learner_evaluation_policy import (load_policy,
                                                scheduling_facts)
    facts = scheduling_facts(observed_at, load_policy())
    job = S.EvaluationJob(
        job_id="job_" + source_id[4:],
        kind=S.JobKind.DIALOGUE_EVALUATION,
        source_id=source_id, source_revision=1,
        workspace_id=workspace_id, scope_revision=scope_revision,
        priority=S.JobPriority.CURRENT_DIALOGUE.value,
        created_at=S.utc_now_iso(), updated_at=S.utc_now_iso(),
        eligible_after_utc=facts["eligible_after_utc"],
        local_activity_date=facts["local_activity_date"],
        schedule_mode=facts["schedule_mode"],
        policy_revision=int(facts["policy_revision"]))
    journal.register_source(receipt, job=job)
    try:
        from .worker import notify_evaluation_worker
        notify_evaluation_worker()
    except Exception:
        pass
    return source_id


def after_turn_hook(student_id: str, session: Any) -> list[str]:
    """统一 turn hook（supervisor/legacy/voice 共用，§13.1）：回合保存后对
    本回合新增的学生消息受理 dialogue 来源。返回受理的 source_ids。"""
    source_ids: list[str] = []
    try:
        messages = list(getattr(session, "messages", []) or [])
        state = get_journal(student_id).state()
        handled = {src.receipt.message_ref
                   for src in state.sources.values()
                   if src.receipt.message_ref}
        # R03：从最新往回回补所有"已落盘但未受理"的学生消息（上一 hook
        # 崩溃/写失败不丢评），最多回看 _RECONCILE_WINDOW 条；遇到连续
        # 已受理即止（更早的都处理过了）
        budget = _RECONCILE_WINDOW
        for message in reversed(messages):
            if budget <= 0:
                break
            if message.get("role") != "user":
                continue
            mid = str(message.get("message_id") or "")
            if mid in handled:
                break
            budget -= 1
            sid = register_dialogue_source(
                student_id=student_id, session=session, message=message)
            if sid is not None:
                source_ids.append(sid)
    except Exception:
        pass
    return source_ids
