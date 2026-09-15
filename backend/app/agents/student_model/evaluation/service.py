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
                         claims_by_local: dict[str, S.ObservationClaim],
                         active_claims: list[S.ClaimView],
                         prompt_ref: str) -> S.ConceptJudgment:
    """patch → 物化判断：retain 保持原样，revise 改写，close 移出 active，
    add 由本次观察直接生成完整 claim（§4.3）。observations: local_id→obs_id。"""
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
        obs = claims_by_local.get(add_id)
        if obs is None:
            continue
        obs_id = observations.get(add_id, "")
        status = (S.ClaimStatus.SUPPORTED
                  if obs.stance == S.ClaimStance.SUPPORTS
                  else S.ClaimStatus.CHALLENGED
                  if obs.stance == S.ClaimStance.CHALLENGES
                  else S.ClaimStatus.TENTATIVE)
        claims.append(S.ClaimView(
            claim_id=new_claim_id(), concept_ref=concept,
            statement=obs.statement, status=status,
            support_refs=[obs_id]
            if obs.stance == S.ClaimStance.SUPPORTS else [],
            challenge_refs=[obs_id]
            if obs.stance == S.ClaimStance.CHALLENGES else [],
            assistance_scope=obs.warrant,
            limits=obs.limits,
            created_by_observation=obs_id,
            updated_at_observation=obs_id))
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
                         generation: str,
                         included_refs: list[str] | None = None,
                         truncations: list[str] | None = None) -> None:
        """R16：随输入指纹保存最小可复现清单（引用 ID 与裁剪说明，
        不含正文副本）。"""
        get_journal(student_id).append(
            [S.OpJobInputPrepared(job_id=job_id, input_hash=input_hash,
                                  prompt_binding=prompt_binding,
                                  generation=generation,
                                  included_refs=included_refs or [],
                                  truncations=truncations or [])],
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
            expected_base_judgment_ids: dict[str, str] | None = None,
            outbox: list[dict[str, Any]] | None = None,
    ) -> CommitOutcome:
        """提交语义结果（§6.4：校验 + 单事务落盘）。"""
        ops, outcome = self.build_commit_operations(
            student_id, job_id=job_id, lease_token=lease_token,
            expected_generation=expected_generation, source=source,
            pack=pack, task=task, interpretation=interpretation,
            task_result=task_result, continuation=continuation,
            expected_scope_revision=expected_scope_revision,
            expected_base_judgments=expected_base_judgments,
            expected_base_judgment_ids=expected_base_judgment_ids,
            outbox=outbox)
        get_journal(student_id).append(ops,
                                       expected_generation=expected_generation)
        return outcome

    def build_commit_operations(
            self, student_id: str, *,
            job_id: str, lease_token: str, expected_generation: str,
            source: S.SourceReceipt, pack: S.EvaluationContextPack,
            task: S.TaskSnapshot | None,
            interpretation: S.LearnerInterpretation | None,
            task_result: S.TaskResult | None = None,
            continuation: S.ContinuationAction | None = None,
            expected_scope_revision: str | None = None,
            expected_base_judgments: dict[str, list[S.ClaimView]] | None = None,
            expected_base_judgment_ids: dict[str, str] | None = None,
            outbox: list[dict[str, Any]] | None = None,
    ) -> tuple[list[Any], CommitOutcome]:
        """构造 result_committed 操作（含全部 R05 CAS 校验）但不落盘。

        R06：复核 revise 需要把替代解释、TaskResult、判断、复核决定与
        job 终态放进**同一个事务**，由调用方组合后一次 append。硬校验
        失败抛 CommitRejected，不产生新能力结论。"""
        journal = get_journal(student_id)
        # lease：旧 worker/超时回包不能提交（§6.5）
        if not self.scheduler.lease_valid(student_id, job_id, lease_token):
            raise CommitRejected([V.ValidationIssue(
                "stale_lease", "lease 无效或已过期，结果不予提交", V.HARD)])
        state = journal.state()
        # source 可用性与当前版本（R05：以 journal 当前事实为准，不信任
        # 调用方内存中的 receipt 副本）
        src_state = state.sources.get(source.source_id)
        if src_state is None:
            raise CommitRejected([V.ValidationIssue(
                "source_gone", "来源已不存在，结果不予提交", V.HARD)])
        if src_state.availability != "available":
            raise CommitRejected([V.ValidationIssue(
                "source_unavailable", "来源已归档/删除，结果不予提交",
                V.HARD)])
        if src_state.receipt.source_revision != source.source_revision:
            raise CommitRejected([V.ValidationIssue(
                "stale_source", "来源已改版，旧结果不予提交", V.HARD)])
        # scope：评价 pack 构建后教材撤选/graph revision 改变（§18.3.8）。
        # 重解析当前事实再比较（R05），不只是比较旧输入自身。
        workspace_id = source.workspace_id_at_observation
        if workspace_id:
            from .scope import ScopeNotFound, get_scope_resolver
            try:
                current_scope_revision = get_scope_resolver().resolve(
                    student_id, workspace_id).scope_revision
            except ScopeNotFound:
                raise CommitRejected([V.ValidationIssue(
                    "workspace_gone", "工作区已删除/失效，结果不予提交",
                    V.HARD)])
            frozen_scope = expected_scope_revision or source.scope_revision
            if frozen_scope and current_scope_revision != frozen_scope:
                raise CommitRejected([V.ValidationIssue(
                    "scope_changed",
                    f"scope revision 已变化（{frozen_scope} → "
                    f"{current_scope_revision}），丢弃旧结果", V.HARD)])
        elif expected_scope_revision is not None and expected_scope_revision:
            raise CommitRejected([V.ValidationIssue(
                "workspace_gone", "来源已无工作区绑定，结果不予提交",
                V.HARD)])
        # 基线判断 CAS（R05）：pack 构建时冻结的 judgment_id 必须仍是当前值
        if expected_base_judgment_ids:
            for key, expected_jid in expected_base_judgment_ids.items():
                current_jid = state.concept_current.get(
                    (workspace_id, key), "")
                if current_jid != expected_jid:
                    raise CommitRejected([V.ValidationIssue(
                        "base_judgment_changed",
                        f"概念 {key} 的基线判断已被并发提交更新"
                        f"（{expected_jid} → {current_jid}），请重新评价",
                        V.HARD)])
        issues: list[V.ValidationIssue] = []
        if interpretation is not None:
            issues = V.validate_interpretation(
                interpretation=interpretation, source=source, pack=pack,
                task=task, active_claims=expected_base_judgments or {},
                expected_source_revision=source.source_revision)
        hard = [i for i in issues if i.severity == V.HARD]
        if hard:
            raise CommitRejected(hard)
        # R15：机会/发布门违规阻断其 observation、statement 与 feedback——
        # 违规主张不得以"当前解释"形态公开（§17.2.2）。
        if interpretation is not None and len(issues) > 0:
            violating_locals = {
                detail.split(":")[0] for i in issues
                if i.severity in (V.OPPORTUNITY, V.GATE)
                for detail in [i.detail] if ":" in i.detail}
            kept_claims = [c for c in interpretation.observation_claims
                           if c.local_id not in violating_locals]
            if len(kept_claims) != len(interpretation.observation_claims) \
                    or interpretation.feedback \
                    or interpretation.assistance_interpretation:
                interpretation = interpretation.model_copy(update={
                    "observation_claims": kept_claims,
                    "feedback": "",
                    "assistance_interpretation": "",
                    "concept_updates": [
                        u for u in interpretation.concept_updates
                        if not any(
                            i.concept_ref == u.concept_ref
                            for i in issues
                            if i.severity in (V.OPPORTUNITY, V.GATE))]})

        interpretation_id = new_interpretation_id()
        judgments: list[S.ConceptJudgment] = []
        dropped: list[str] = []
        state = journal.state()
        watermark = state.watermark
        # R05：无工作区绑定不发布任何 learner 判断（task-only 反馈语义，
        # §11.4 workspace_required）——解释可保存供反馈读取，但标记 abstain。
        publish_learner = interpretation is not None and bool(workspace_id)
        # R10：claim local_id → obs/概念/来源 稳定映射（含未被概念 patch
        # 覆盖的观察，保证时间线/依赖闭包可精确寻址）
        observation_map: list[dict[str, str]] = []
        if interpretation is not None:
            local_obs = {c.local_id: new_observation_id()
                         for c in interpretation.observation_claims}
            for c in interpretation.observation_claims:
                concept = _resolve_concept(c.concept_ref, pack)
                observation_map.append({
                    "local_id": c.local_id,
                    "obs_id": local_obs.get(c.local_id, ""),
                    "concept_key": concept.key if concept is not None else "",
                    "source_id": source.source_id})
        else:
            local_obs = {}
        if publish_learner and interpretation.applicable:
            observations = local_obs
            claim_by_local: dict[str, S.ObservationClaim] = {
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
                    workspace_id=workspace_id,
                    scope_revision=source.scope_revision,
                    source_id=source.source_id, watermark=watermark,
                    observations=observations, claims_by_local=claim_by_local,
                    active_claims=base_claims,
                    prompt_ref=pack.prompt_binding)
                judgments.append(judgment)
        abstained = (interpretation is None or not interpretation.applicable
                     or not publish_learner)
        ops = [S.OpResultCommitted(
            job_id=job_id, source_id=source.source_id,
            source_revision=source.source_revision,
            scope_revision=source.scope_revision or "no_scope",
            task_result=task_result,
            interpretation_id=interpretation_id,
            interpretation=interpretation,
            judgments=judgments, continuation=continuation,
            abstained=abstained,
            observation_map=observation_map,
            outbox=list(outbox or []) + (
                [{"event_id": f"resync_{judgment.judgment_id}",
                  "consumer": "synthesis", "kind": "concept_dirty",
                  "concept_key": judgment.concept_ref.key}
                 for judgment in judgments]
                + [{"event_id": f"resync_{source.source_id}",
                    "consumer": "synthesis", "kind": "concept_dirty",
                    "concept_key": d} for d in dropped]))]
        return ops, CommitOutcome(
            interpretation_id=interpretation_id,
            judgment_ids=[j.judgment_id for j in judgments],
            dropped_updates=dropped, abstained=abstained)
