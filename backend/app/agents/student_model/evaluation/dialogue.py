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
_MIN_CHARS = 2


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
                       max_candidates: int = 12) -> list[S.ConceptRef]:
    """概念候选（§7.2）：来自已选教材节点的严格名称/别名匹配。零候选时
    返回空列表——由 LLM/服务端记 concept_unresolved，不静默跳过。"""
    try:
        from .scope import get_scope_resolver
        scope = get_scope_resolver().resolve(student_id, workspace_id)
    except Exception:
        return []
    hits: list[S.ConceptRef] = []
    for concept in scope.allowed_concepts:
        names = [concept.display_name] + _aliases_of(concept)
        for name in names:
            if name and name in message_text:
                hits.append(concept)
                break
        if len(hits) >= max_candidates:
            break
    return hits


def _aliases_of(concept: S.ConceptRef) -> list[str]:
    return []   # ConceptRef 不带别名；严格名称匹配（§7.2），模糊匹配不做


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
    source_id = new_source_id()
    receipt = S.SourceReceipt(
        source_id=source_id, source_revision=1, kind=S.SourceKind.DIALOGUE,
        observed_at=S.utc_now_iso(),
        workspace_id_at_observation=workspace_id,
        canonical_text=text, assistance_events=[],
        spans=[S.EvidenceSpanOwnership(
            start=0, end=len(text), owner="dialogue")],
        source_session_ref=session_ref, message_ref=message_id,
        scope_revision=scope_revision)
    job = S.EvaluationJob(
        job_id="job_" + source_id[4:],
        kind=S.JobKind.DIALOGUE_EVALUATION,
        source_id=source_id, source_revision=1,
        workspace_id=workspace_id, scope_revision=scope_revision,
        priority=S.JobPriority.CURRENT_DIALOGUE.value,
        created_at=S.utc_now_iso(), updated_at=S.utc_now_iso())
    journal.register_source(receipt, job=job)
    return source_id


def after_turn_hook(student_id: str, session: Any) -> list[str]:
    """统一 turn hook（supervisor/legacy/voice 共用，§13.1）：回合保存后对
    本回合新增的学生消息受理 dialogue 来源。返回受理的 source_ids。"""
    source_ids: list[str] = []
    try:
        messages = list(getattr(session, "messages", []) or [])
        # 本回合的 user 消息 = 尚无 dialogue 受理且未被 assessment 拥有的
        state = get_journal(student_id).state()
        handled = {src.receipt.message_ref
                   for src in state.sources.values()
                   if src.receipt.message_ref}
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            if str(message.get("message_id") or "") in handled:
                break   # 已受理过的最近一条 → 更早的都处理过了
            sid = register_dialogue_source(
                student_id=student_id, session=session, message=message)
            if sid is not None:
                source_ids.append(sid)
            break   # 只处理本回合最后一条学生消息
    except Exception:
        pass
    return source_ids
