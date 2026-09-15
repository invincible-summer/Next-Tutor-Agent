"""P4/P5/P9 的 LLM 调用包装与 job 执行（plan §6.2 evaluator.py）。

- run_dialogue_job（C5/P4）：pack → P4 → validator → commit。
- run_review_job（C9/P5）：独立上下文复核 → review_resolved + 级联失效。
- run_synthesis_job（C7/P9）：已有 observation 重组 → synthesis_committed。

与 assessment job 相同纪律：真实 runner 调用、validator 门、
CommitRejected → job failed、lease/generation/scope 再检查。
"""
from __future__ import annotations

from typing import Any

from . import context as pack_builder
from . import schema as S
from .jobs import ClaimedJob, job_deadline
from .llm import EvaluationLLMRunner, build_system_message
from .service import CommitRejected, LearnerEvaluationService
from .store import (JournalState, get_journal, new_interpretation_id,
                    new_judgment_id, new_synthesis_id)


def _dialogue_session_context(
        receipt: S.SourceReceipt) -> tuple[list[str], dict]:
    """R03：有边界的前文与会话情景。

    - prior_texts：本消息之前的 assistant 追问/讲解文本（短答"3"的
      概念归属来自上一追问）；
    - session_context：最近消息摘要（角色/摘录/ID/时间）+ 任务绑定，
      不含全文、不越过本消息（答后内容不得倒用作答前帮助）。
    """
    prior_texts: list[str] = []
    context_messages: list[dict] = []
    task_binding: dict = {}
    try:
        from app.core.session import load_session
        session = load_session(receipt.source_session_ref)
        if session is None:
            return prior_texts, {}
        messages = list(getattr(session, "messages", []) or [])
        seen_current = False
        window: list[dict] = []
        for message in reversed(messages):
            mid = str(message.get("message_id") or "")
            if mid == receipt.message_ref:
                seen_current = True
                continue
            if not seen_current:
                continue
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role not in ("user", "assistant") or not content.strip():
                continue
            window.append({
                "role": role,
                "excerpt": content[:200],
                "message_id": mid,
                "created_at": message.get("created_at"),
            })
            if role == "assistant":
                prior_texts.append(content[:400])
            if len(window) >= 6:
                break
        context_messages = list(reversed(window))
        prior_texts = list(reversed(prior_texts))[-3:]
        binding = getattr(session, "task_binding", None)
        if isinstance(binding, dict) and binding.get("concept_id"):
            task_binding = {
                "task_id": binding.get("task_id") or "",
                "goal_id": binding.get("goal_id") or "",
                "concept_id": binding.get("concept_id") or "",
            }
    except Exception:
        return [], {}
    session_context: dict = {"recent_messages": context_messages}
    if task_binding:
        session_context["task_binding"] = task_binding
    return prior_texts, session_context


async def run_dialogue_job(student_id: str, claimed: ClaimedJob, *,
                           runner: EvaluationLLMRunner,
                           scheduler=None) -> str:
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    service = LearnerEvaluationService(scheduler)
    journal = get_journal(student_id)
    state = journal.state()
    # R05：认领后立即冻结 generation，提交 CAS 用快照值
    generation_at_claim = state.generation
    job = claimed.job
    src = state.sources.get(job.source_id)
    if src is None or src.receipt.source_revision != job.source_revision:
        scheduler.cancel(student_id, job.job_id, reason="source_gone")
        return "cancelled"
    if src.availability != "available":
        scheduler.cancel(student_id, job.job_id, reason="source_unavailable")
        return "cancelled"
    receipt = src.receipt
    if receipt.workspace_id_at_observation:
        try:
            from .scope import get_scope_resolver
            scope = get_scope_resolver().resolve(
                student_id, receipt.workspace_id_at_observation)
        except Exception:
            scheduler.cancel(student_id, job.job_id, reason="scope_gone")
            return "cancelled"
    else:
        scheduler.cancel(student_id, job.job_id, reason="no_workspace")
        return "cancelled"

    async with learner_runtime.workspace_lock(
            student_id, receipt.workspace_id_at_observation):
        from .dialogue import concept_candidates
        prior_texts, session_context = _dialogue_session_context(
            receipt)
        candidates = concept_candidates(
            student_id, receipt.workspace_id_at_observation,
            receipt.canonical_text, prior_texts=prior_texts)
        # §7.2：概念候选完全无法取得 → concept_unresolved（记录原因，仍提交
        # applicable=false 解释；不能零候选静默跳过）
        scenarios = _dialogue_scenarios(state, receipt)
        binding = ("learning_evidence_contract@1.0.0+"
                   "dialogue_learner_evaluation@1.0.0")
        if not candidates:
            abstain = S.LearnerInterpretation(
                applicable=False, abstain_reason="concept_unresolved",
                feedback="")
            service.commit_result(
                student_id, job_id=job.job_id,
                lease_token=claimed.lease_token,
                expected_generation=generation_at_claim,
                source=receipt, pack=_empty_pack(receipt, binding),
                task=None, interpretation=abstain,
                expected_scope_revision=job.scope_revision or None)
            return "abstained"
        pack = pack_builder.assemble_dialogue_pack(
            source=receipt, scope=scope, state=state, scenarios=scenarios,
            candidates=candidates, session_context=session_context)
        pack.job_id = job.job_id
        service.record_job_input(
            student_id, job.job_id, input_hash=pack.manifest.input_hash,
            prompt_binding=binding, generation=state.generation,
            included_refs=list(pack.manifest.included_refs),
            truncations=list(pack.manifest.truncations))
        system = build_system_message(
            "dialogue_learner_evaluation", scenarios=scenarios,
            output_model=S.LearnerInterpretation)
        out = await runner.run_structured(
            deadline_at=job_deadline(claimed.job),
            system=system, user=pack_builder.pack_user_message(pack),
            output_model=S.LearnerInterpretation, max_output_tokens=4000)
        if out.parsed is None:
            scheduler.fail(student_id, job.job_id,
                           error_code=out.error_code or "llm_failed",
                           retryable=out.retryable_error,
                           transport_attempts=out.transport_attempts)
            return "failed"
        parsed: S.LearnerInterpretation = out.parsed
        base_claims: dict[str, list[S.ClaimView]] = {}
        base_judgment_ids: dict[str, str] = {}
        for entry in pack.allowlist:
            jid = state.concept_current.get(
                (scope.workspace_id, entry.concept.key), "")
            base_judgment_ids[entry.concept.key] = jid
            judgment = state.judgments.get(jid) if jid else None
            if judgment is not None:
                base_claims[entry.concept.key] = list(judgment.claims)
        try:
            service.commit_result(
                student_id, job_id=job.job_id,
                lease_token=claimed.lease_token,
                expected_generation=generation_at_claim,
                source=receipt, pack=pack, task=None, interpretation=parsed,
                expected_scope_revision=job.scope_revision or None,
                expected_base_judgments=base_claims,
                expected_base_judgment_ids=base_judgment_ids)
        except CommitRejected:
            scheduler.fail(student_id, job.job_id,
                           error_code="validation_rejected",
                           retryable=False,
                           transport_attempts=out.transport_attempts)
            return "failed"
        return "succeeded"


def _dialogue_scenarios(state: JournalState, receipt: S.SourceReceipt
                        ) -> list[str]:
    from app.prompts.learner_evaluation import P4_SCENARIOS
    out: list[str] = []
    out.append(P4_SCENARIOS["dialogue_spontaneous"])
    # 上一个真实提问（同会话更早的 assistant 消息以问号结尾）→ followup
    for src in state.sources.values():
        if src.receipt.source_session_ref == receipt.source_session_ref \
                and src.receipt.observed_at < receipt.observed_at:
            out.append(P4_SCENARIOS["dialogue_followup"])
            break
    return out


def _empty_pack(receipt: S.SourceReceipt, binding: str
                ) -> S.EvaluationContextPack:
    return S.EvaluationContextPack(
        pack_id="pack_" + receipt.source_id[4:16], job_id="",
        source_id=receipt.source_id, prompt_binding=binding,
        manifest=S.PackManifest(
            included_refs=["s1"], omitted_refs=[], truncations=[],
            input_hash="ih_" + receipt.source_id[4:],
            prompt_ref=binding, evidence_watermark=""))


# ---------------------------------------------------------------------------
# C9 复核（P5）
# ---------------------------------------------------------------------------

def _rebuild_pack_for_review(student_id: str, state: JournalState,
                             receipt: S.SourceReceipt,
                             task: S.TaskSnapshot | None,
                             ) -> S.EvaluationContextPack | None:
    """按原解释链路确定性重建 pack（allowlist 顺序可重放），供替代解释
    的 validator 使用。scope 已失效时返回 None（调用方按不可修订处理）。"""
    workspace_id = receipt.workspace_id_at_observation
    if not workspace_id:
        return None
    try:
        from .scope import get_scope_resolver
        scope = get_scope_resolver().resolve(student_id, workspace_id)
    except Exception:
        return None
    binding = ("learning_evidence_contract@1.0.0+"
               "learner_evaluation_review@1.0.0")
    if task is not None:
        return pack_builder.assemble_assessment_pack(
            source=receipt, task=task, scope=scope, state=state,
            task_result=None, scenarios=[], prompt_binding=binding,
            hint_concepts=[p.strip() for p in task.source_badge.split(",")])
    from .dialogue import concept_candidates
    candidates = concept_candidates(student_id, workspace_id,
                                    receipt.canonical_text)
    if not candidates:
        return None
    return pack_builder.assemble_dialogue_pack(
        source=receipt, scope=scope, state=state, scenarios=[],
        candidates=candidates, prompt_binding=binding)


async def run_review_job(student_id: str, claimed: ClaimedJob, *,
                         runner: EvaluationLLMRunner,
                         scheduler=None) -> str:
    """R06：按本 job 绑定的 review_id 执行复核并完整结案。

    - uphold：原解释保持有效，复核 resolved。
    - revise：替代解释经 validator 后与 TaskResult、判断、复核决定、
      job 终态**同一事务**提交。
    - invalidate：解释撤销 + 递归失效 + 决定同一事务。
    - insufficient_evidence：复核保持待确定（active），job abstained。
    - 解释/来源已消失：dismiss 复核并取消 job，允许新的合法异议。
    """
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    journal = get_journal(student_id)
    state = journal.state()
    generation_at_claim = state.generation
    job = claimed.job
    review_id = state.review_by_job.get(job.job_id, "")
    review = state.reviews.get(review_id)
    src = state.sources.get(job.source_id)
    if review is None or src is None or review.source_id != job.source_id:
        scheduler.cancel(student_id, job.job_id, reason="review_gone")
        return "cancelled"
    if review.status != "active":
        return "succeeded"        # 幂等：HTTP 重试/重复投递
    if review.interpretation_id not in src.interpretations:
        # 被争议解释已删除/撤销（复核期间删除）→ 关闭复核，允许新异议
        journal.append([S.OpReviewDismissed(
            review_id=review_id, reason="interpretation_gone",
            job_id=job.job_id)], expected_generation=generation_at_claim)
        return "cancelled"
    receipt = src.receipt
    if src.availability != "available":
        journal.append([S.OpReviewDismissed(
            review_id=review_id, reason="source_unavailable",
            job_id=job.job_id)], expected_generation=generation_at_claim)
        return "cancelled"

    old_meta = src.interpretations.get(review.interpretation_id, {})
    raw_old = old_meta.get("raw_interpretation")
    task = None
    if receipt.task_ref is not None:
        task = state.tasks.get(receipt.task_ref.question_id, {}).get(
            receipt.task_ref.question_revision)

    binding = ("learning_evidence_contract@1.0.0+"
               "learner_evaluation_review@1.0.0")
    service = LearnerEvaluationService(scheduler)
    service.record_job_input(
        student_id, job.job_id, input_hash="rh_" + review.review_id,
        prompt_binding=binding, generation=state.generation)
    # 同概念有效历史（受污染解释的原始主张）：复核上下文需要
    history: list[Any] = []
    if receipt.workspace_id_at_observation:
        for (ws, key), jid in state.concept_current.items():
            if ws != receipt.workspace_id_at_observation or not jid:
                continue
            judgment = state.judgments.get(jid)
            if judgment is not None:
                history.append({
                    "concept_key": key,
                    "state": judgment.state.value,
                    "claims": [c.model_dump() for c in judgment.claims[:8]],
                })
    user = S.canonical_json({
        "原始作答": {"canonical_text": receipt.canonical_text,
                     "observed_at": receipt.observed_at,
                     "assistance": [a.model_dump()
                                    for a in receipt.assistance_events]},
        "题目": (task.model_dump() if task is not None else None),
        "被争议解释": raw_old,
        "异议": {"reason": review.reason,
                 "issue_kind": review.issue_kind},
        "同概念有效历史": history[:16],
    })
    system = build_system_message(
        "learner_evaluation_review", output_model=S.ReviewDecisionOutput)
    out = await runner.run_structured(
        deadline_at=job_deadline(claimed.job),
        system=system, user=user, output_model=S.ReviewDecisionOutput,
        max_output_tokens=4000)
    if out.parsed is None:
        scheduler.fail(student_id, job.job_id,
                       error_code=out.error_code or "llm_failed",
                       retryable=out.retryable_error,
                       transport_attempts=out.transport_attempts)
        return "failed"
    decision: S.ReviewDecisionOutput = out.parsed

    ops: list[Any] = []
    outbox: list[dict[str, Any]] = []
    replacement_id = ""
    if decision.decision == S.ReviewDecisionKind.REVISE \
            and decision.replacement_interpretation is not None:
        pack = _rebuild_pack_for_review(student_id, state, receipt, task)
        base_claims: dict[str, list[S.ClaimView]] = {}
        base_judgment_ids: dict[str, str] = {}
        if pack is not None:
            for entry in pack.allowlist:
                jid = state.concept_current.get(
                    (receipt.workspace_id_at_observation,
                     entry.concept.key), "")
                base_judgment_ids[entry.concept.key] = jid
                judgment = state.judgments.get(jid) if jid else None
                if judgment is not None:
                    base_claims[entry.concept.key] = list(judgment.claims)
        # 任务改分：开放题按替代 criterion results 重算；MC 正误保持服务端
        # 确定判定（§9.5），复核不越权改写。
        replacement_tr = None
        if task is not None and decision.replacement_criterion_results and \
                task.q_type != S.QuestionType.MULTIPLE_CHOICE:
            from .grading import compute_task_result
            replacement_tr = compute_task_result(
                task, decision.replacement_criterion_results,
                receipt.canonical_text)
        if pack is None:
            # scope 已失效：无法安全物化替代解释 → 维持原解释，按 uphold
            # 处理并在决定里记录限制
            decision = decision.model_copy(update={
                "decision": S.ReviewDecisionKind.UPHOLD,
                "reason": decision.reason + "（scope 已变化，无法物化替代解释）"})
        else:
            try:
                commit_ops, commit_outcome = service.build_commit_operations(
                    student_id, job_id=job.job_id,
                    lease_token=claimed.lease_token,
                    expected_generation=generation_at_claim,
                    source=receipt, pack=pack, task=task,
                    interpretation=decision.replacement_interpretation,
                    task_result=replacement_tr,
                    expected_scope_revision=job.scope_revision or None,
                    expected_base_judgments=base_claims,
                    expected_base_judgment_ids=base_judgment_ids)
                ops.extend(commit_ops)
                replacement_id = commit_outcome.interpretation_id
                outbox.extend([{"event_id": f"resync_{jid}",
                                "consumer": "synthesis",
                                "kind": "concept_dirty",
                                "concept_key": key}
                               for key, jid in zip(base_judgment_ids,
                                                   commit_outcome.judgment_ids)])
            except CommitRejected as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "review %s replacement rejected: %s", review_id, exc)
                scheduler.fail(student_id, job.job_id,
                               error_code="replacement_rejected",
                               retryable=False,
                               transport_attempts=out.transport_attempts)
                return "failed"
    elif decision.decision == S.ReviewDecisionKind.INVALIDATE:
        # 撤销 + 递归失效（R08）与复核决定同一事务
        from . import lifecycle
        revoke_ops, affected = lifecycle.build_invalidation_ops(
            student_id, state, review.interpretation_id,
            reason="review_invalidated:" + review.review_id)
        ops.extend(revoke_ops)
        outbox.extend([{"event_id": f"resync_{jid}",
                        "consumer": "synthesis", "kind": "concept_dirty",
                        "concept_key": key} for key, jid in affected.items()])
        # G4 §6.6：复核撤销投递 M9（复习卡重放）
        outbox.append({"event_id": f"m9_review_{review.review_id}",
                       "consumer": "m9", "kind": "review_resolved",
                       "concept_keys": sorted(affected.keys())})

    ops.append(S.OpReviewResolved(
        review_id=review_id, decision=decision,
        replacement_interpretation_id=replacement_id,
        job_id=job.job_id,
        outbox=outbox))
    journal.append(ops, expected_generation=generation_at_claim)
    # invalidate 影响的区需要重综合排队（§12.5）
    if decision.decision == S.ReviewDecisionKind.INVALIDATE and \
            receipt.workspace_id_at_observation:
        from . import lifecycle as _lc
        _lc.request_resynthesis(
            student_id, receipt.workspace_id_at_observation,
            reason="review_invalidated")
    return "succeeded"

async def run_synthesis_job(student_id: str, claimed: ClaimedJob, *,
                            runner: EvaluationLLMRunner,
                            scope_type: S.ScopeType = S.ScopeType.CONCEPT,
                            concept_key: str = "",
                            scheduler=None) -> str:
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    journal = get_journal(student_id)
    state = journal.state()
    job = claimed.job
    binding = ("learning_evidence_contract@1.0.0+"
               "learning_scope_synthesis@1.0.0")
    claims: list[S.ClaimView] = []
    workspace_id = job.workspace_id
    if scope_type == S.ScopeType.CONCEPT and concept_key:
        jid = state.concept_current.get((workspace_id, concept_key), "")
        judgment = state.judgments.get(jid) if jid else None
        if judgment is not None:
            claims = list(judgment.claims)
    else:
        for (ws, key), jid in state.concept_current.items():
            if ws != workspace_id or not jid:
                continue
            judgment = state.judgments.get(jid)
            if judgment is not None:
                claims.extend(judgment.claims)
    inputs = {
        "scope_type": scope_type.value,
        "workspace_id": workspace_id,
        "concept_key": concept_key,
        "有效主张": [c.model_dump() for c in claims[:64]],
        "observation_excerpts": _recent_observations(
            state, workspace_id, concept_key),
    }
    service = LearnerEvaluationService(scheduler)
    service.record_job_input(
        student_id, job.job_id, input_hash="sh_" + job.job_id[4:],
        prompt_binding=binding, generation=state.generation)
    system = build_system_message(
        "learning_scope_synthesis", output_model=S.ScopeSynthesisOutput)
    out = await runner.run_structured(
        deadline_at=job_deadline(claimed.job),
        system=system, user=S.canonical_json(inputs),
        output_model=S.ScopeSynthesisOutput, max_output_tokens=4000)
    if out.parsed is None:
        scheduler.fail(student_id, job.job_id,
                       error_code=out.error_code or "llm_failed",
                       retryable=out.retryable_error)
        return "failed"
    output: S.ScopeSynthesisOutput = out.parsed
    concept_ref = None
    if scope_type == S.ScopeType.CONCEPT and concept_key:
        for (ws, key), jid in state.concept_current.items():
            if ws == workspace_id and key == concept_key and jid:
                judgment = state.judgments.get(jid)
                if judgment is not None:
                    concept_ref = judgment.concept_ref
                break
    synthesis = S.ScopeSynthesis(
        synthesis_id=new_synthesis_id(), scope_type=scope_type,
        workspace_id=workspace_id, concept_ref=concept_ref,
        statement=output.statement, claim_refs=output.claim_refs,
        theme_summaries=output.theme_summaries, changes=output.changes,
        open_questions=output.open_questions,
        priority_probe=output.priority_probe, limits=output.limits,
        scope_revision=job.scope_revision or "unknown",
        evidence_watermark=state.watermark,
        pending_source_count=0, generated_at=S.utc_now_iso())
    # R08：确定性重放失效概念（见 _restorable_judgments 规则）
    restored = _restorable_judgments(
        state, workspace_id,
        [concept_key] if (scope_type == S.ScopeType.CONCEPT and concept_key)
        else "")
    journal.append([S.OpSynthesisCommitted(
        synthesis=synthesis, job_id=job.job_id,
        restored_judgments=restored)])
    return "succeeded"


def _obs_to_source_index(state: JournalState) -> dict[str, str]:
    """obs_id -> source_id（从各解释持久化的 observation_map 重建）。"""
    idx: dict[str, str] = {}
    for src in state.sources.values():
        for meta in src.interpretations.values():
            if isinstance(meta, dict):
                for m in meta.get("observation_map") or []:
                    if isinstance(m, dict) and m.get("obs_id"):
                        idx[str(m["obs_id"])] = str(m.get("source_id") or "")
    return idx


def _interpretation_revoked(state: JournalState, interpretation_id: str,
                            ) -> bool:
    for src in state.sources.values():
        meta = src.interpretations.get(interpretation_id)
        if isinstance(meta, dict) and meta.get("revoked"):
            return True
    return False


def _restorable_judgments(state: JournalState, workspace_id: str,
                          concept_keys: list[str] | str,
                          ) -> list[S.ConceptJudgment]:
    """R08：失效概念的确定性重放（§12 重放基础概念判断）。

    对当前没有有效判断的概念键，找最近一个同概念判断 J：
    - J 的全部主张依据仍存活（来源 available 且产生解释未撤销）→
      原样恢复（state/statement 不变）；
    - 部分存活 → 恢复存活主张，state=fragile（依据基础被削弱）；
    - 零存活 → 不恢复（保持 not_observed）。
    纯 journal 事实推导，不新增 LLM 语义。
    """
    keys = ([concept_keys] if isinstance(concept_keys, str)
            else list(concept_keys))
    if not keys:
        # 工作区综合：重放该区全部缺失概念
        known: set[str] = set()
        for (ws, key) in state.concept_current:
            if ws == workspace_id:
                known.add(key)
        for judgment in state.judgments.values():
            if judgment.workspace_id == workspace_id:
                known.add(judgment.concept_ref.key)
        keys = [k for k in known
                if (workspace_id, k) not in state.concept_current]
    if not keys:
        return []
    obs_src = _obs_to_source_index(state)
    latest: dict[str, S.ConceptJudgment] = {}
    for judgment in state.judgments.values():
        if judgment.workspace_id != workspace_id:
            continue
        key = judgment.concept_ref.key
        if key not in keys:
            continue
        old = latest.get(key)
        if old is None or judgment.created_at >= old.created_at:
            latest[key] = judgment
    restored: list[S.ConceptJudgment] = []
    for key, judgment in latest.items():
        if (workspace_id, key) in state.concept_current:
            continue
        surviving: list[S.ClaimView] = []
        for claim in judgment.claims:
            obs = claim.created_by_observation or                 claim.updated_at_observation
            source_id = obs_src.get(obs, "")
            src_state = state.sources.get(source_id)
            if src_state is None or src_state.availability != "available":
                continue
            interp = next(
                (i for i, meta in src_state.interpretations.items()
                 if isinstance(meta, dict)
                 and not meta.get("revoked")
                 and obs in {str(m.get("obs_id"))
                             for m in (meta.get("observation_map") or [])
                             if isinstance(m, dict)}), "")
            if not interp:
                continue
            surviving.append(claim)
        if not surviving:
            continue
        replay = judgment.model_copy(deep=True)
        replay = replay.model_copy(update={
            "judgment_id": new_judgment_id(),
            "claims": surviving,
            "state": (judgment.state if len(surviving) == len(judgment.claims)
                      else S.ConceptEvalState.FRAGILE),
            "evidence_watermark": state.watermark,
            "created_at": S.utc_now_iso(),
        })
        restored.append(replay)
    return restored


def _recent_observations(state: JournalState, workspace_id: str,
                         concept_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for src in state.sources.values():
        if src.receipt.workspace_id_at_observation != workspace_id:
            continue
        meta = src.interpretations.get(src.current_interpretation_id, {})
        raw = meta.get("raw_interpretation") or {}
        for claim in (raw.get("observation_claims") or []
                      if isinstance(raw, dict) else []):
            out.append({
                "source_id": src.receipt.source_id,
                "observed_at": src.receipt.observed_at,
                "statement": claim.get("statement"),
                "stance": claim.get("stance"),
                "excerpt": src.receipt.canonical_text[:300],
            })
        if len(out) >= 24:
            break
    out.sort(key=lambda o: o["observed_at"], reverse=True)
    return out
