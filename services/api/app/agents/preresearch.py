"""Deterministic material-grounding policy for the two chat execution paths.

The model may decide whether to perform *follow-up* searches, but the first
search for a turn that explicitly uploads/references material is a system
responsibility.  This module owns only the policy; execution and SSE emission
remain in chat_agent.py/executor.py so the legacy and Supervisor paths keep
their existing event contracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .material_signals import CONTENT_QUERY_RE as _CONTENT_QUERY_RE
from .material_signals import NON_CONTENT_RE as _NON_CONTENT_RE
from .material_signals import FILE_REF_RE as _FILE_REF_RE
from .material_signals import mentions_title


@dataclass(frozen=True)
class MaterialGroundingDecision:
    required: bool
    reason: str = ""
    file_ids: tuple[str, ...] = ()
    visible_file_count: int = 0

    @property
    def trace_reason(self) -> str:
        return self.reason or "none"


def _attachment_ids(attachments: list[dict[str, Any]] | None) -> tuple[str, ...]:
    out: list[str] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id", "")).strip()
        if fid and fid not in out:
            out.append(fid)
    return tuple(out)


def is_content_question(user_message: str) -> bool:
    """Classify whether a turn asks for knowledge rather than an operation.

    This is deliberately deterministic: the presence of authorized material
    must not depend on an LLM remembering a prompt instruction.  Explicit
    academic markers win; short greetings/commands are excluded so a workspace
    does not cause retrieval for every chitchat turn.
    """
    text = re.sub(r"\s+", "", user_message or "")
    if not text or _NON_CONTENT_RE.fullmatch(text):
        return False
    if _CONTENT_QUERY_RE.search(text):
        return True
    # 书名号里的篇目/课文名（≥2 字）几乎总是教材检索意图——「《湖畔夜色讲》」
    # 这类 7 字短句靠长度门槛永远过不去（2026-08-25 取证：双重否决导致
    # 教材就在工作区却完全不触发检索）。
    if mentions_title(user_message or ""):
        return True
    # A substantive non-operational sentence is still a knowledge request.
    # Keep this conservative for very short control messages.
    return len(text) >= 6 and not _FILE_REF_RE.fullmatch(text)


def decide_material_grounding(
    session: Any,
    user_message: str,
    attachments: list[dict[str, Any]] | None = None,
) -> MaterialGroundingDecision:
    """Return the hard retrieval decision for a single turn.

    A workspace merely having readable material does not force a search for
    greetings or unrelated turns.  Current-turn attachments/references and an
    explicit material-grounded question do force one search.  File ids are
    constrained only when the current message provides them; otherwise the
    search covers the authorized visible overlay, never the owner's whole
    Library.
    """
    try:
        from ..core.workspace import merged_knowledge_files
        visible, _ = merged_knowledge_files(session)
    except Exception:
        visible = []
    visible_ids = {str(f.get("id", "")) for f in visible if f.get("id")}
    attached_ids = tuple(fid for fid in _attachment_ids(attachments)
                         if fid in visible_ids)
    if attached_ids:
        return MaterialGroundingDecision(
            True, "current_turn_material", attached_ids, len(visible))
    if attachments:
        # Even if an attachment metadata object is incomplete or stale, the
        # upload/reference action itself is a grounding signal.  Do not silently
        # downgrade it to an ordinary answer.
        return MaterialGroundingDecision(True, "current_turn_material", (), len(visible))
    pending = tuple(fid for fid in dict.fromkeys(
        getattr(session, "pending_material_file_ids", []) or []) if fid in visible_ids)
    if pending:
        return MaterialGroundingDecision(
            True, "pending_material_action", pending, len(visible))
    if _FILE_REF_RE.search(user_message or "") and visible:
        return MaterialGroundingDecision(
            True, "explicit_material_reference", (), len(visible))
    if visible and is_content_question(user_message):
        return MaterialGroundingDecision(
            True, "workspace_material_content_question", (), len(visible))
    return MaterialGroundingDecision(False, "none", (), len(visible))


def consume_pending_materials(session: Any, decision: MaterialGroundingDecision) -> None:
    """Clear one-shot upload/reference signals after the mandatory search ran."""
    if not decision.required:
        return
    try:
        session.pending_material_file_ids = []
    except Exception:
        pass


def build_query(user_message: str, decision: MaterialGroundingDecision,
                visible_files: list[dict[str, Any]] | None = None) -> str:
    """Keep the student's question primary, adding only bounded source names."""
    query = (user_message or "").strip()
    if query:
        return query
    names = [str(f.get("filename", "")).strip() for f in (visible_files or [])
             if str(f.get("filename", "")).strip()]
    return "、".join(names[:3]) or "资料内容"
