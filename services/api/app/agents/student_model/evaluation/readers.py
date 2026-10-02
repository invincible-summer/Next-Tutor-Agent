"""R18（update_plan §4）：教学消费者的唯一合法评价读取口。

M3/M5/M9 等学习内容消费者**不得**直接遍历 journal 的全工作区判断——
必须显式携带 workspace，经 scope 求交后的有效投影读取；无 workspace
只允许非个性化建议。读取零 LLM。
"""
from __future__ import annotations

from typing import Any


def scoped_concept_states(student_id: str, workspace_id: str, *,
                          include_out_of_scope: bool = False
                          ) -> dict[str, Any] | None:
    """当前工作区的概念状态投影 {concept_id: {"state": ..., "judgment_id":
    ..., "statement": ...}}。workspace 为空/不合法 → None（非个性化降级）。

    键为完整概念身份命中的视图（含 concept_revision 匹配）；归档/删除
    来源与 reconciling 语义由 projections 统一处理。

    include_out_of_scope：附加**本工作区**当前判断中已越出选卷范围的概
    念（仍严格按 workspace 隔离，不跨区）。供 M9 计划滞后检测使用——
    计划里的概念离开选卷范围本身就是重规划信号。"""
    if not workspace_id:
        return None
    try:
        from .projections import concept_views
        from .scope import ScopeNotFound, get_scope_resolver
        from .store import get_journal
        scope = get_scope_resolver().resolve(student_id, workspace_id)
    except Exception:
        return None
    out: dict[str, Any] = {}
    for view in concept_views(student_id, scope):
        if view.state is None:
            continue      # reconciling/未观察不驱动教学结论
        out[view.concept_ref.concept_id] = {
            "state": view.state.value,
            "statement": view.statement,
            "judgment_id": view.judgment_id,
            "concept_key": view.concept_ref.key,
            "display_name": view.concept_ref.display_name,
            "claims": [c.statement for c in view.claims[:6]],
        }
    if include_out_of_scope:
        state = get_journal(student_id).state()
        in_scope_keys = {v["concept_key"] for v in out.values()}
        for (ws, key), jid in state.concept_current.items():
            if ws != workspace_id or not jid:
                continue
            judgment = state.judgments.get(jid)
            if judgment is None or judgment.state is None:
                continue
            if judgment.concept_ref.key in in_scope_keys:
                continue
            src = state.sources.get(judgment.source_id)
            if src is not None and src.availability != "available":
                continue
            out[judgment.concept_ref.concept_id] = {
                "state": judgment.state.value,
                "statement": judgment.statement,
                "judgment_id": judgment.judgment_id,
                "concept_key": judgment.concept_ref.key,
                "display_name": judgment.concept_ref.display_name,
                "claims": [c.statement for c in judgment.claims[:6]],
            }
    return out or None


def scoped_workspace_summary(student_id: str, workspace_id: str) -> Any | None:
    """当前工作区档案总览；无/非法 workspace → None。"""
    if not workspace_id:
        return None
    try:
        from .projections import workspace_summary
        from .scope import ScopeNotFound, get_scope_resolver
        scope = get_scope_resolver().resolve(student_id, workspace_id)
    except Exception:
        return None
    return workspace_summary(student_id, scope)
