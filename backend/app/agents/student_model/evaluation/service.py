"""LearnerEvaluationService：唯一事务提交 facade（plan §6.2 / §10.2）。

任何 learner 写操作只从 service/store 到 journal（§17.1）；提交前再检查
身份/范围/source revision/lease/generation——权限撤销、账号删除、source
改版时丢弃旧结果（§10.2）。

materialize：ConceptUpdate patch → ConceptJudgment（服务端分配
observation/claim/judgment ID；retain/revise/close 物化到新的 claims 集）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import schema as S
from . import validator as V
from .jobs import JobScheduler
from .store import (EvidenceJournal, GenerationConflictError,
                    get_journal, new_claim_id, new_interpretation_id,
                    new_judgment_id, new_observation_id)


class CommitRejected(RuntimeError):
    """校验失败：不产生新能力结论（§7.1 C6）。"""

    def __init__(self, issues: list[V.ValidationIssue]) -> None:
        hard = [i for i in issues if i.severity == V.HARD]
        detail = "; ".join(f"{i.code}:{i.detail}" for i in hard[:5]) or \
            "; ".join(f"{i.code}:{i.detail}" for i in issues[:5])
        super().__init__(detail)
        self.issues = issues


@dataclass
class CommitOutcome:
    interpretation_id: str
    judgment_ids: list[str] = field(default_factory=list)
    dropped_updates: list[str] = field(default_factory=list)
    abstained: bool = False


def _resolve_concept(ref: str, pack: S.EvaluationContextPack
                     ) -> S.ConceptRef | None:
    for entry in pack.allowlist:
        if entry.short_ref == ref:
            return entry.concept
    return None


def materialize_judgment(update: S.ConceptUpdate, *,
                         concept: S.ConceptRef,
                         workspace_id: str, scope_revision: str,
                         source_id: str, watermark: str,
                         observations: dict[str, str],
                         active_claims: list[S.ClaimView],
                         prompt_ref: str) -> S.ConceptJudgment:
    """patch → 物化判断：retain 保持原样，revise 改写，close 移出 active，
    add 由本次观察生成新 claim（§4.3）。observations: local_id → obs_id。"""
    retained = {c.claim_id: c for c in active_claims}
    claims: list[S.ClaimView] = []
    for cid in update.retain_claim_ids:
        if cid in retained:
            claims.append(retained[cid])
    for rc in update.revise_claims:
        base = retained.get(rc.claim_id)
        if base is None:
            continue
        claims.append(base.model_copy(update={
            "statement": rc.new_statement,
            "status": rc.new_status,
            "support_refs": rc.support_refs or base.support_refs,
            "challenge_refs": rc.challenge_refs or base.challenge_refs,
            "limits": rc.limits or base.limits,
            "updated_at_observation": source_id,
        }))
    for add_id in update.add_claim_local_ids:
        obs_id = observations.get(add_id, "")
        claims.append(S.ClaimView(
            claim_id=new_claim_id(), concept_ref=concept,
            statement="", status=S.ClaimStatus.TENTATIVE,
            created_by_observation=obs_id,
            updated_at_observation=obs_id))
    # add claims 的 statement/status 由解释中的观察回填（statement 必填）
    return S.ConceptJudgment(
        judgment_id=new_judgment_id(),
        base_judgment_id=update.base_judgment_id,
        concept_ref=concept,
        workspace_id=workspace_id,
        state=update.proposed_state,
        statement=update.statement,
        claims=claims,
        change=update.change,
        next_probe=update.next_probe,
        dependencies=[observations.get(d, d) for d in update.dependencies],
        evidence_watermark=watermark,
        policy_version=S.POLICY_VERSION,
        theory_version=S.THEORY_VERSION,
        prompt_ref=prompt_ref,
        created_at=S.utc_now_iso(),
        source_id=source_id,
        scope_revision=scope_revision)


class LearnerEvaluationService:
    def __init__(self, scheduler: JobScheduler | None = None) -> None:
        self._scheduler = scheduler

    @property
    def scheduler(self) -> JobScheduler:
        if self._scheduler is None:
            from app.core import learner_runtime
            self._scheduler = learner_runtime.get_scheduler()
        return self._scheduler

    # ------------------------------------------------------------------
    def record_job_input(self, student_id: str, job_id: str, *,
                         input_hash: str, prompt_binding: str,
                         generation: str) -> None:
        get_journal(student_id).append(
            [S.OpJobInputPrepared(job_id=job_id, input_hash=input_hash,
                                  prompt_binding=prompt_binding,
                                  generation=generation)],
            expected_generation=generation)

    # ------------------------------------------------------------------
    def commit_result(
            self, student_id: str, *,
            job_id: str, lease_token: str, expected_generation: str,
            source: S.SourceReceipt, pack: S.EvaluationContextPack,
            task: S.TaskSnapshot | None,
            interpretation: S.LearnerInterpretation | None,
            task_result: S.TaskResult | None = None,
            continuation: S.ContinuationAction | None = None,
            expected_scope_revision: str | None = None,
            expected_base_judgments: dict[str, list[S.ClaimView]] | None = None,
            outbox: list[dict[str, Any]] | None = None,
    ) -> CommitOutcome:
        """提交语义结果（§6.4 result_committed：可含 TaskResult+解释+判断+
        待投递项同一事务）。硬校验失败抛 CommitRejected，不产生新结论。"""
        journal = get_journal(student_id)
        # lease：旧 worker/超时回包不能提交（§6.5）
        if not self.scheduler.lease_valid(student_id, job_id, lease_token):
            raise CommitRejected([V.ValidationIssue(
                "stale_lease", "lease 无效或已过期，结果不予提交", V.HARD)])
        # scope：评价 pack 构建后教材撤选/graph revision 改变（§18.3.8）
        if expected_scope_revision is not None \
                and source.scope_revision \
                and source.scope_revision != expected_scope_revision:
            raise CommitRejected([V.ValidationIssue(
                "scope_changed", "scope revision 已变化，丢弃旧结果", V.HARD)])
        issues: list[V.ValidationIssue] = []
        if interpretation is not None:
            issues = V.validate_interpretation(
                interpretation=interpretation, source=source, pack=pack,
                task=task, active_claims=expected_base_judgments or {},
                expected_source_revision=source.source_revision)
        hard = [i for i in issues if i.severity == V.HARD]
        if hard:
            raise CommitRejected(hard)

        interpretation_id = new_interpretation_id()
        judgments: list[S.ConceptJudgment] = []
        dropped: list[str] = []
        state = journal.state()
        watermark = state.watermark
        if interpretation is not None and interpretation.applicable:
            observations = {c.local_id: new_observation_id()
                            for c in interpretation.observation_claims}
            claim_by_obs: dict[str, S.ObservationClaim] = {
                c.local_id: c for c in interpretation.observation_claims}
            for update in interpretation.concept_updates:
                if any(i.severity != V.HARD and i.concept_ref ==
                        update.concept_ref for i in issues):
                    dropped.append(update.concept_ref)   # 拒 patch 并重综合
                    continue
                concept = _resolve_concept(update.concept_ref, pack)
                if concept is None:
                    dropped.append(update.concept_ref)
                    continue
                base_claims = (expected_base_judgments or {}).get(
                    concept.key, [])
                judgment = materialize_judgment(
                    update, concept=concept,
                    workspace_id=source.workspace_id_at_observation,
                    scope_revision=source.scope_revision,
                    source_id=source.source_id, watermark=watermark,
                    observations=observations, active_claims=base_claims,
                    prompt_ref=pack.prompt_binding)
                _fill_added_claim_views(judgment, claim_by_obs, observations)
                judgments.append(judgment)
        abstained = interpretation is None or not interpretation.applicable
        journal.append(
            [S.OpResultCommitted(
                job_id=job_id, source_id=source.source_id,
                source_revision=source.source_revision,
                scope_revision=source.scope_revision or "no_scope",
                task_result=task_result,
                interpretation_id=interpretation_id,
                interpretation=interpretation,
                judgments=judgments, continuation=continuation,
                abstained=abstained,
                outbox=list(outbox or []) + (
                    [{"event_id": f"resync_{judgment.judgment_id}",
                      "consumer": "synthesis", "kind": "concept_dirty",
                      "concept_key": judgment.concept_ref.key}
                     for judgment in judgments]
                    + [{"event_id": f"resync_{source.source_id}",
                        "consumer": "synthesis", "kind": "concept_dirty",
                        "concept_key": d} for d in dropped]))],
            expected_generation=expected_generation)
        return CommitOutcome(interpretation_id=interpretation_id,
                             judgment_ids=[j.judgment_id for j in judgments],
                             dropped_updates=dropped, abstained=abstained)


def _fill_added_claim_views(judgment: S.ConceptJudgment,
                            claims_by_local: dict[str, S.ObservationClaim],
                            observations: dict[str, str]) -> None:
    """add 生成的占位 ClaimView 回填 statement/stance→status 与证据。"""
    for i, view in enumerate(judgment.claims):
        if view.statement or not view.created_by_observation:
            continue
        obs_id = view.created_by_observation
        for local_id, mapped in observations.items():
            if mapped == obs_id and local_id in claims_by_local:
                claim = claims_by_local[local_id]
                status = (S.ClaimStatus.SUPPORTED
                          if claim.stance == S.ClaimStance.SUPPORTS
                          else S.ClaimStatus.CHALLENGED
                          if claim.stance == S.ClaimStance.CHALLENGES
                          else S.ClaimStatus.TENTATIVE)
                judgment.claims[i] = view.model_copy(update={
                    "statement": claim.statement,
                    "status": status,
                    "assistance_scope": claim.warrant,
                    "limits": claim.limits,
                    "support_refs": [mapped]
                    if claim.stance == S.ClaimStance.SUPPORTS else [],
                    "challenge_refs": [mapped]
                    if claim.stance == S.ClaimStance.CHALLENGES else [],
                })
                break
