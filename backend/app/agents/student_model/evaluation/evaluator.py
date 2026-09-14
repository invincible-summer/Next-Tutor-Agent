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
from .jobs import ClaimedJob
from .llm import EvaluationLLMRunner, build_system_message
from .service import CommitRejected, LearnerEvaluationService
from .store import (JournalState, get_journal, new_interpretation_id,
                    new_synthesis_id)


async def run_dialogue_job(student_id: str, claimed: ClaimedJob, *,
                           runner: EvaluationLLMRunner,
                           scheduler=None) -> str:
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    service = LearnerEvaluationService(scheduler)
    journal = get_journal(student_id)
    state = journal.state()
    job = claimed.job
    src = state.sources.get(job.source_id)
    if src is None or src.receipt.source_revision != job.source_revision:
        scheduler.cancel(student_id, job.job_id, reason="source_gone")
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

    from .dialogue import concept_candidates
    candidates = concept_candidates(student_id,
                                    receipt.workspace_id_at_observation,
                                    receipt.canonical_text)
    # §7.2：概念候选完全无法取得 → concept_unresolved（记录原因，仍提交
    # applicable=false 解释；不能零候选静默跳过）
    scenarios = _dialogue_scenarios(state, receipt)
    binding = ("learning_evidence_contract@1.0.0+"
               "dialogue_learner_evaluation@1.0.0")
    if not candidates:
        pack = S.EvaluationContextPack.model_construct()
        abstain = S.LearnerInterpretation(
            applicable=False, abstain_reason="concept_unresolved",
            feedback="")
        service.commit_result(
            student_id, job_id=job.job_id, lease_token=claimed.lease_token,
            expected_generation=journal.state().generation,
            source=receipt, pack=_empty_pack(receipt, binding),
            task=None, interpretation=abstain,
            expected_scope_revision=job.scope_revision or None)
        return "abstained"
    pack = pack_builder.assemble_dialogue_pack(
        source=receipt, scope=scope, state=state, scenarios=scenarios,
        candidates=candidates)
    pack.job_id = job.job_id
    service.record_job_input(
        student_id, job.job_id, input_hash=pack.manifest.input_hash,
        prompt_binding=binding, generation=state.generation)
    system = build_system_message(
        "dialogue_learner_evaluation", scenarios=scenarios,
        output_model=S.LearnerInterpretation)
    out = await runner.run_structured(
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
    for entry in pack.allowlist:
        jid = state.concept_current.get(
            (scope.workspace_id, entry.concept.key), "")
        judgment = state.judgments.get(jid) if jid else None
        if judgment is not None:
            base_claims[entry.concept.key] = list(judgment.claims)
    try:
        service.commit_result(
            student_id, job_id=job.job_id, lease_token=claimed.lease_token,
            expected_generation=journal.state().generation,
            source=receipt, pack=pack, task=None, interpretation=parsed,
            expected_scope_revision=job.scope_revision or None,
            expected_base_judgments=base_claims)
    except CommitRejected:
        scheduler.fail(student_id, job.job_id, error_code="validation_rejected",
                       retryable=False, transport_attempts=out.transport_attempts)
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

async def run_review_job(student_id: str, claimed: ClaimedJob, *,
                         runner: EvaluationLLMRunner,
                         scheduler=None) -> str:
    from app.core import learner_runtime
    scheduler = scheduler or learner_runtime.get_scheduler()
    journal = get_journal(student_id)
    state = journal.state()
    job = claimed.job
    review_id = ""
    for rid, review in state.reviews.items():
        if review.source_id == job.source_id:
            review_id = rid
            break
    src = state.sources.get(job.source_id)
    if not review_id or src is None:
        scheduler.cancel(student_id, job.job_id, reason="review_gone")
        return "cancelled"
    review = state.reviews[review_id]
    old_meta = src.interpretations.get(review.interpretation_id, {})
    old_interp = None
    raw_old = old_meta.get("raw_interpretation")
    if isinstance(raw_old, dict):
        try:
            old_interp = S.LearnerInterpretation.model_validate(raw_old)
        except Exception:
            old_interp = None
    task = None
    if src.receipt.task_ref is not None:
        task = state.tasks.get(src.receipt.task_ref.question_id, {}).get(
            src.receipt.task_ref.question_revision)

    binding = ("learning_evidence_contract@1.0.0+"
               "learner_evaluation_review@1.0.0")
    service = LearnerEvaluationService(scheduler)
    service.record_job_input(
        student_id, job.job_id, input_hash="rh_" + review.review_id,
        prompt_binding=binding, generation=state.generation)
    user = S.canonical_json({
        "原始作答": {"canonical_text": src.receipt.canonical_text,
                     "observed_at": src.receipt.observed_at,
                     "assistance": [a.model_dump()
                                    for a in src.receipt.assistance_events]},
        "题目": (task.model_dump() if task is not None else None),
        "被争议解释": raw_old,
        "异议": {"reason": review.reason,
                 "issue_kind": review.issue_kind},
        "同概念有效历史": [],
    })
    system = build_system_message(
        "learner_evaluation_review", output_model=S.ReviewDecisionOutput)
    out = await runner.run_structured(
        system=system, user=user, output_model=S.ReviewDecisionOutput,
        max_output_tokens=4000)
    if out.parsed is None:
        scheduler.fail(student_id, job.job_id,
                       error_code=out.error_code or "llm_failed",
                       retryable=out.retryable_error)
        return "failed"
    decision: S.ReviewDecisionOutput = out.parsed

    ops: list[Any] = []
    replacement_id = ""
    outbox: list[dict[str, Any]] = []
    if decision.decision == S.ReviewDecisionKind.REVISE \
            and decision.replacement_interpretation is not None:
        replacement_id = new_interpretation_id()
    if decision.decision == S.ReviewDecisionKind.INVALIDATE:
        # 依赖失效传播（§12.4）：撤销解释并级联受影响判断/综合
        from . import lifecycle
        affected = lifecycle.invalidate_interpretation(
            student_id, review.interpretation_id,
            reason="review_invalidated:" + review.review_id,
            extra_ops=ops)
        outbox = [{"event_id": f"resync_{jid}",
                   "consumer": "synthesis", "kind": "concept_dirty",
                   "concept_key": key}
                  for key, jid in affected.items()]
        # G4 §6.6：复核撤销也投递 M9——受影响复习卡需重放日期状态
        outbox.append({"event_id": f"m9_review_{review.review_id}",
                       "consumer": "m9", "kind": "review_resolved",
                       "concept_keys": sorted(affected.keys())})
    ops.append(S.OpReviewResolved(
        review_id=review_id, decision=decision,
        replacement_interpretation_id=replacement_id,
        outbox=outbox))
    journal.append(ops)
    return "succeeded"


# ---------------------------------------------------------------------------
# C7 综合（P9）
# ---------------------------------------------------------------------------

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
    journal.append([S.OpSynthesisCommitted(synthesis=synthesis)])
    return "succeeded"


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
