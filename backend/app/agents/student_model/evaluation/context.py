"""ContextPack 组装与预算（plan §8）。

只依赖注入 reader（§6.2）；assemble_assessment_pack 供 M4 统一链使用，
dialogue pack 在 G3 接入。输入分层按 §8.1：trusted_scope / current /
task / assistance / textbook / prior / related / session / workspace /
preferences / manifest。所有内容序列化进 user message 的 JSON。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import schema as S
from .store import JournalState, SourceState

MAX_ALLOWLIST = 12            # §7.2：概念候选最多 12 个
MAX_PRIOR_SOURCES = 6         # §8.2：每概念历史挑选上限（实现取最近+反证）
MAX_RELATED = 6               # 3 前置 + 3 相关（G2 起步为空，M5 供给后填充）


def _pack_id(source_id: str) -> str:
    return "pack_" + hashlib.sha256(source_id.encode()).hexdigest()[:14]


def _short_refs(concepts: list[S.ConceptRef]) -> list[S.PackConceptRefEntry]:
    """服务端短引用 c1/c2/… → ConceptRef；仅在 pack 内有效（§4.1）。"""
    return [S.PackConceptRefEntry(short_ref=f"c{i + 1}", concept=c)
            for i, c in enumerate(concepts[:MAX_ALLOWLIST])]


def select_allowlist(scope: S.EvaluationScope,
                     task: S.TaskSnapshot | None,
                     hint_concepts: list[str] | None = None,
                     ) -> list[S.ConceptRef]:
    """概念候选：题目 concept_refs 优先（已在 scope 内），其余取 scope
    严格同名匹配（§7.2：候选来自当前教学目标/检索节点/严格匹配）。"""
    chosen: list[S.ConceptRef] = []
    keys: set[str] = set()
    if task is not None:
        for c in task.concept_refs:
            if c.key not in keys:
                chosen.append(c)
                keys.add(c.key)
    for c in scope.allowed_concepts:
        if len(chosen) >= MAX_ALLOWLIST:
            break
        for hint in hint_concepts or []:
            if hint and (c.concept_id == hint or c.display_name == hint) \
                    and c.key not in keys:
                chosen.append(c)
                keys.add(c.key)
    return chosen[:MAX_ALLOWLIST]


def _sources_by_concept_key(state: JournalState) -> dict[str, set[str]]:
    """概念 key → 归属它的来源集合：经物化判断（judgment.source_id）与
    任务概念归属（不猜名称）。"""
    mapping: dict[str, set[str]] = {}
    for judgment in state.judgments.values():
        if judgment.source_id:
            mapping.setdefault(judgment.concept_ref.key,
                               set()).add(judgment.source_id)
    for src in state.sources.values():
        ref = src.receipt.task_ref
        if ref is None:
            continue
        task = state.tasks.get(ref.question_id, {}).get(
            ref.question_revision)
        if task is None:
            continue
        for concept in task.concept_refs:
            mapping.setdefault(concept.key,
                               set()).add(src.receipt.source_id)
    return mapping


def prior_same_concept(state: JournalState, workspace_id: str,
                       concept_keys: set[str]) -> dict[str, Any]:
    """§8.2 历史挑选：当前判断、最近有效表现、最近反例；只取本来源之前
    的历史（observed_at 序），不取 superseded/revoked。来源按概念归属
    过滤（经判断/任务），不同概念的历史不互相污染。"""
    by_concept = _sources_by_concept_key(state)
    out: dict[str, Any] = {}
    for key in concept_keys:
        judgment_id = state.concept_current.get((workspace_id, key), "")
        judgment = state.judgments.get(judgment_id) if judgment_id else None
        entry: dict[str, Any] = {"concept_key": key}
        if judgment is not None:
            entry["current_claim_views"] = [c.model_dump()
                                            for c in judgment.claims]
            entry["current_state"] = judgment.state.value
            entry["updated_at"] = judgment.created_at
        attributed = by_concept.get(key, set())
        sources: list[dict[str, Any]] = []
        for src in state.sources.values():
            if src.availability != "available":
                continue
            if src.receipt.workspace_id_at_observation != workspace_id:
                continue
            if src.receipt.source_id not in attributed:
                continue
            interp = src.interpretations.get(src.current_interpretation_id)
            if not interp or interp.get("revoked") or interp.get("abstained"):
                continue
            raw = interp.get("raw_interpretation") or {}
            claims = raw.get("observation_claims") or []
            if isinstance(claims, list) and claims:
                sources.append({
                    "source_id": src.receipt.source_id,
                    "observed_at": src.receipt.observed_at,
                    "assistance_floor":
                        src.receipt.assistance_floor.value,
                    "excerpt":
                        src.receipt.canonical_text[:400],
                    "claims": [
                        {"statement": c.get("statement"),
                         "stance": c.get("stance"),
                         "limits": c.get("limits")}
                        for c in claims[:4]],
                })
        sources.sort(key=lambda s: s["observed_at"], reverse=True)
        recent = sources[:MAX_PRIOR_SOURCES]
        # §8.2：必须保留最近反证
        challenges = [s for s in sources
                      if any(c.get("stance") == "challenges"
                             for c in s["claims"])]
        for ch in challenges:
            if ch not in recent:
                recent.append(ch)
        entry["recent_sources"] = recent[:MAX_PRIOR_SOURCES + 2]
        entry["no_prior"] = not recent and judgment is None
        out[key] = entry
    return out


def assemble_assessment_pack(
        *, source: S.SourceReceipt, task: S.TaskSnapshot,
        scope: S.EvaluationScope | None, state: JournalState,
        task_result: S.TaskResult | None,
        scenarios: list[str], prompt_binding: str,
        learner_preferences: dict[str, Any] | None = None,
        hint_concepts: list[str] | None = None,
        workspace_name: str = "",
) -> S.EvaluationContextPack:
    """C4 作答解释的 ContextPack（§8.1 分层）。"""
    allow_concepts = select_allowlist(scope, task, hint_concepts) \
        if scope is not None else list(task.concept_refs[:MAX_ALLOWLIST])
    entries = _short_refs(allow_concepts)
    keys = {e.concept.key for e in entries}
    prior = prior_same_concept(state,
                               source.workspace_id_at_observation,
                               keys) if scope is not None else {}
    task_payload: dict[str, Any] = {
        "task_mode": ("multiple_choice"
                      if task.q_type == S.QuestionType.MULTIPLE_CHOICE
                      else "open_answer"),
        "question_id": task.question_id,
        "question_revision": task.question_revision,
        "q_type": task.q_type.value,
        "stem": task.stem,
        "options": task.options,
        "frozen_rubric": [c.model_dump() for c in task.rubric],
        "equivalent_solutions": task.equivalent_solutions,
        "verification": task.verification.model_dump(),
        "evidence_opportunities": [o.model_dump()
                                   for o in task.evidence_opportunities],
        "task_family": task.task_family,
        "novelty": task.novelty,
    }
    if task_result is not None:
        # MC：task_result 服务端已判定，P3 不得改写 verdict（§9.5）
        task_payload["task_result"] = task_result.model_dump()
    current: dict[str, Any] = {
        "ref": "s1",
        "source_id": source.source_id,
        "source_revision": source.source_revision,
        "canonical_text": source.canonical_text,
        "observed_at": source.observed_at,
        "order_hint": "本块是唯一新增学习证据",
    }
    payload_for_hash = {
        "source": source.source_id, "revision": source.source_revision,
        "answer": source.canonical_text,
        "task": task.question_id + "@" + str(task.question_revision),
        "prior": sorted(prior.keys()),
        "allowlist": [e.concept.key for e in entries],
    }
    input_hash = "ih_" + hashlib.sha256(json.dumps(
        payload_for_hash, ensure_ascii=False, sort_keys=True)
        .encode()).hexdigest()[:32]
    return S.EvaluationContextPack(
        pack_id=_pack_id(source.source_id),
        job_id="",
        source_id=source.source_id,
        prompt_binding=prompt_binding,
        scenarios=scenarios,
        output_language="zh",
        allowlist=entries,
        current_student_evidence=current,
        task=task_payload,
        assistance_before_response=[a.model_dump()
                                    for a in source.assistance_events],
        prior_same_concept=prior,
        workspace_context={"workspace_id": source.workspace_id_at_observation,
                           "workspace_name": workspace_name,
                           "scope_revision": source.scope_revision},
        learner_preferences=learner_preferences or {},
        manifest=S.PackManifest(
            included_refs=["s1"] + [e.short_ref for e in entries],
            omitted_refs=[],
            truncations=[],
            input_hash=input_hash,
            prompt_ref=prompt_binding,
            evidence_watermark=state.watermark),
    )


def pack_user_message(pack: S.EvaluationContextPack) -> str:
    """user message = pack 各分层序列化 JSON（§8.1：禁止学生正文进 system）。"""
    return json.dumps(pack.model_dump(), ensure_ascii=False)


def assemble_dialogue_pack(
        *, source: S.SourceReceipt, scope: S.EvaluationScope,
        state: JournalState, scenarios: list[str],
        candidates: list[S.ConceptRef],
        session_context: dict[str, Any] | None = None,
        learner_preferences: dict[str, Any] | None = None,
        workspace_name: str = "",
) -> S.EvaluationContextPack:
    """C5 对话解释的 ContextPack（P4，§8.1 分层）。"""
    entries = _short_refs(candidates)
    keys = {e.concept.key for e in entries}
    prior = prior_same_concept(state, source.workspace_id_at_observation, keys)
    current: dict[str, Any] = {
        "ref": "s1",
        "source_id": source.source_id,
        "source_revision": source.source_revision,
        "canonical_text": source.canonical_text,
        "observed_at": source.observed_at,
        "message_ref": source.message_ref,
        "order_hint": "本块是唯一新增学习证据",
    }
    input_hash = "ih_" + hashlib.sha256(json.dumps({
        "source": source.source_id, "revision": source.source_revision,
        "text": source.canonical_text,
        "prior": sorted(prior.keys()),
        "allowlist": [e.concept.key for e in entries],
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
    binding = ("learning_evidence_contract@1.0.0+"
               "dialogue_learner_evaluation@1.0.0")
    return S.EvaluationContextPack(
        pack_id=_pack_id(source.source_id), job_id="", source_id=source.source_id,
        prompt_binding=binding, scenarios=scenarios, output_language="zh",
        allowlist=entries,
        current_student_evidence=current,
        task={"dialogue_mode": True,
              "scenarios": scenarios},
        assistance_before_response=[a.model_dump()
                                    for a in source.assistance_events],
        prior_same_concept=prior,
        session_context=session_context or {},
        workspace_context={"workspace_id": source.workspace_id_at_observation,
                           "workspace_name": workspace_name,
                           "scope_revision": source.scope_revision},
        learner_preferences=learner_preferences or {},
        manifest=S.PackManifest(
            included_refs=["s1"] + [e.short_ref for e in entries],
            omitted_refs=[], truncations=[], input_hash=input_hash,
            prompt_ref=binding, evidence_watermark=state.watermark))
