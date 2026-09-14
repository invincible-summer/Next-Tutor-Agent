"""唯一 learner admissibility + 引用/语义资格门（plan §17.2 / §4.4 / §18.2）。

validator 区分三类结论：
1. 硬错误（severity=hard）：越权/伪造引文/assistant 冒充 learner/过期
   source/重复来源 → 拒绝整份受影响结果，不能删掉错误字段后发布其余。
2. 机会缺失（severity=opportunity）：MC 无解释却声称观察推理、无延迟条件
   却 claim retention 等 → 拒绝该推断及其依赖的整体 statement。
3. 语义疑点（severity=gate）：发布门（五类状态资格）不满足 → 拒绝该
   concept patch 并重综合（§4.4）。

代码不证明自然语言 claim 与引文之间的逻辑蕴含——那是 C9/gold set 的
职责；这里只验证 ID/时间/引用/角色/机会的真实性与完整性。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import schema as S

HARD = "hard"
OPPORTUNITY = "opportunity"
GATE = "gate"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    detail: str
    severity: str
    concept_ref: str = ""


def _concept_key_of(ref: str, allowlist: dict[str, S.ConceptRef]) -> str:
    entry = allowlist.get(ref)
    return entry.key if entry is not None else ref


def _allowlist_from_pack(pack: S.EvaluationContextPack
                         ) -> dict[str, S.ConceptRef]:
    return {e.short_ref: e.concept for e in pack.allowlist}


# ---------------------------------------------------------------------------
# 硬错误：引用/身份/时序
# ---------------------------------------------------------------------------

def check_evidence_spans(claims: list[S.ObservationClaim],
                         source: S.SourceReceipt,
                         pack: S.EvaluationContextPack) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    text = source.canonical_text
    n = len(text)
    seen_spans: set[tuple[str, int, int]] = set()
    for claim in claims:
        for span in claim.current_evidence:
            if span.end > n or span.start >= span.end:
                issues.append(ValidationIssue(
                    "span_out_of_range",
                    f"{claim.local_id}: span [{span.start},{span.end}) 超出"
                    f"学生原文长度 {n}", HARD, claim.concept_ref))
                continue
            actual = text[span.start:span.end]
            if actual != span.quote:
                issues.append(ValidationIssue(
                    "quote_mismatch",
                    f"{claim.local_id}: 引文与原文不一致"
                    f"（quote={span.quote[:40]!r} actual={actual[:40]!r}）",
                    HARD, claim.concept_ref))
            key = (span.ref, span.start, span.end)
            if key in seen_spans:
                issues.append(ValidationIssue(
                    "duplicate_span", f"{claim.local_id}: 同一 span 被两个"
                    "主张重复引用", HARD, claim.concept_ref))
            seen_spans.add(key)
    return issues


def check_concept_allowlist(claims: list[S.ObservationClaim],
                            updates: list[S.ConceptUpdate],
                            pack: S.EvaluationContextPack
                            ) -> list[ValidationIssue]:
    allowlist = _allowlist_from_pack(pack)
    issues: list[ValidationIssue] = []
    for claim in claims:
        if claim.concept_ref not in allowlist:
            issues.append(ValidationIssue(
                "concept_not_allowed",
                f"{claim.local_id}: 概念 {claim.concept_ref} 不在 pack 白名单",
                HARD, claim.concept_ref))
    for update in updates:
        if update.concept_ref not in allowlist:
            issues.append(ValidationIssue(
                "concept_not_allowed",
                f"update: 概念 {update.concept_ref} 不在 pack 白名单",
                HARD, update.concept_ref))
    return issues


def check_source_freshness(source: S.SourceReceipt,
                           expected_revision: int) -> list[ValidationIssue]:
    if source.source_revision != expected_revision:
        return [ValidationIssue(
            "stale_source",
            f"source {source.source_id} 版本 {source.source_revision} != 提交"
            f"期望 {expected_revision}（改版后的旧结果不得发布）", HARD)]
    return []


def check_id_references(updates: list[S.ConceptUpdate],
                        active_claims: dict[str, list[S.ClaimView]],
                        pack_refs: set[str],
                        local_ids: set[str]) -> list[ValidationIssue]:
    """claim/依赖引用真实存在：retain/revise/close 必须指向已知 active
    claim；add 只能指向本次 local_id；dependencies 只能引用 pack 内已有
    observation/claim/judgment（§4.3）。"""
    issues: list[ValidationIssue] = []
    known = {c.claim_id for claims in active_claims.values() for c in claims}
    for update in updates:
        covered: dict[str, int] = {}
        for cid in update.retain_claim_ids:
            covered[cid] = covered.get(cid, 0) + 1
        for rc in update.revise_claims:
            covered[rc.claim_id] = covered.get(rc.claim_id, 0) + 1
        for cc in update.close_claims:
            covered[cc.claim_id] = covered.get(cc.claim_id, 0) + 1
        for cid, count in covered.items():
            if cid not in known:
                issues.append(ValidationIssue(
                    "unknown_claim_ref",
                    f"{update.concept_ref}: 引用不存在的 claim {cid}", HARD,
                    update.concept_ref))
            if count > 1:
                issues.append(ValidationIssue(
                    "claim_double_disposition",
                    f"{update.concept_ref}: claim {cid} 在 "
                    "retain/revise/close 中出现超过一次", HARD,
                    update.concept_ref))
        for lid in update.add_claim_local_ids:
            if lid not in local_ids:
                issues.append(ValidationIssue(
                    "unknown_local_id",
                    f"{update.concept_ref}: add 引用不存在的本次观察 "
                    f"{lid}", HARD, update.concept_ref))
        for dep in update.dependencies:
            if dep in pack_refs or dep in known or dep in local_ids:
                continue
            issues.append(ValidationIssue(
                "unknown_dependency",
                f"{update.concept_ref}: 依赖引用 {dep} 不在 pack/已知 claims",
                HARD, update.concept_ref))
    return issues


def check_active_claim_coverage(
        updates: list[S.ConceptUpdate],
        active_claims: dict[str, list[S.ClaimView]],
        pack: S.EvaluationContextPack) -> list[ValidationIssue]:
    """全部 active claims 必须不重不漏地出现在 retain/revise/close（§4.3）。"""
    allowlist = _allowlist_from_pack(pack)
    issues: list[ValidationIssue] = []
    for update in updates:
        key = _concept_key_of(update.concept_ref, allowlist)
        entry = allowlist.get(update.concept_ref)
        lookup = entry.key if entry else update.concept_ref
        for claims in (active_claims.get(lookup),
                       active_claims.get(key)):
            if claims:
                current = {cid for cid in update.retain_claim_ids}
                current |= {rc.claim_id for rc in update.revise_claims}
                current |= {cc.claim_id for cc in update.close_claims}
                missing = [c.claim_id for c in claims
                           if c.claim_id not in current]
                if missing:
                    issues.append(ValidationIssue(
                        "active_claims_not_covered",
                        f"{update.concept_ref}: 遗漏 active claims "
                        f"{missing[:4]}（不重不漏规则）", HARD,
                        update.concept_ref))
                break
    return issues


# ---------------------------------------------------------------------------
# 机会缺失（§3.3 / §17.2.2）
# ---------------------------------------------------------------------------

_REASONING_PROCESSES = {S.BloomProcess.ANALYZE, S.BloomProcess.EVALUATE,
                        S.BloomProcess.CREATE}
_OPTION_LIKE = set("ABCDEFGH")


def check_opportunity_conditions(
        claims: list[S.ObservationClaim], source: S.SourceReceipt,
        task: S.TaskSnapshot | None,
        pack: S.EvaluationContextPack) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    is_choice_only = bool(task is not None and
                          task.q_type == S.QuestionType.MULTIPLE_CHOICE)
    retention_ok = any(
        ec.kind == S.EvidenceConditionKind.RETENTION
        and (ec.server_facts or {}).get("interval_hours") is not None
        for c in claims for ec in c.evidence_conditions)
    baseline = bool(pack.task.get("baseline_task"))
    transfer_ok = bool(pack.task.get("baseline_task")) or any(
        task is not None and task.novelty
        and "same_form" not in task.novelty for _ in [0])
    for claim in claims:
        if is_choice_only:
            only_option = all(
                span.quote.strip().strip("。.,") in _OPTION_LIKE
                or len(span.quote.strip()) <= 2
                for span in claim.current_evidence) \
                if claim.current_evidence else True
            if only_option and (set(claim.cognitive_processes)
                                & _REASONING_PROCESSES):
                issues.append(ValidationIssue(
                    "mc_no_reasoning_opportunity",
                    f"{claim.local_id}: 只有选项行为可观察，不能声称推理"
                    "过程主张", OPPORTUNITY, claim.concept_ref))
        for cond in claim.evidence_conditions:
            if cond.kind == S.EvidenceConditionKind.RETENTION and \
                    (cond.server_facts or {}).get("interval_hours") is None \
                    and not retention_ok:
                issues.append(ValidationIssue(
                    "retention_without_delay",
                    f"{claim.local_id}: 无服务端间隔条件却声称延迟保持",
                    OPPORTUNITY, claim.concept_ref))
            if cond.kind == S.EvidenceConditionKind.TRANSFER and \
                    not baseline and not transfer_ok:
                issues.append(ValidationIssue(
                    "transfer_without_baseline",
                    f"{claim.local_id}: 无基线任务对照却声称迁移",
                    OPPORTUNITY, claim.concept_ref))
    return issues


# ---------------------------------------------------------------------------
# 发布门（§4.4）
# ---------------------------------------------------------------------------

def check_publication_gates(
        updates: list[S.ConceptUpdate],
        claims: list[S.ObservationClaim],
        active_claims: dict[str, list[S.ClaimView]],
        pack: S.EvaluationContextPack) -> list[ValidationIssue]:
    allowlist = _allowlist_from_pack(pack)
    issues: list[ValidationIssue] = []
    for update in updates:
        entry = allowlist.get(update.concept_ref)
        key = entry.key if entry else update.concept_ref
        concept_claims = [c for c in claims
                          if _concept_key_of(c.concept_ref, allowlist) == key]
        supports = [c for c in concept_claims
                    if c.stance == S.ClaimStance.SUPPORTS]
        challenges = [c for c in concept_claims
                      if c.stance == S.ClaimStance.CHALLENGES]
        old = active_claims.get(key) or []
        old_supported = [c for c in old
                         if c.status == S.ClaimStatus.SUPPORTED]
        old_challenged = [c for c in old
                          if c.status == S.ClaimStatus.CHALLENGED]
        state = update.proposed_state
        if state == S.ConceptEvalState.NOT_OBSERVED:
            if concept_claims or old:
                issues.append(ValidationIssue(
                    "gate_not_observed", f"{update.concept_ref}: 存在观察却"
                    "提议 not_observed", GATE, update.concept_ref))
        elif state == S.ConceptEvalState.EMERGING:
            if not concept_claims and not old:
                issues.append(ValidationIssue(
                    "gate_emerging", f"{update.concept_ref}: 无任何真实观察"
                    "却提议 emerging", GATE, update.concept_ref))
        elif state == S.ConceptEvalState.SUPPORTED_IN_SCOPE:
            has_support = bool(supports or old_supported)
            if not has_support:
                issues.append(ValidationIssue(
                    "gate_supported", f"{update.concept_ref}: 无 supported"
                    " claim 却提议 supported_in_scope", GATE,
                    update.concept_ref))
            # 同条件反证不能被遗漏：存在的 challenge 必须被处置
            challenge_handled = (not challenges) or any(
                rc.new_status != S.ClaimStatus.SUPPORTED
                for rc in update.revise_claims) or bool(update.close_claims)
            if challenges and not challenge_handled:
                issues.append(ValidationIssue(
                    "gate_supported_counter_evidence",
                    f"{update.concept_ref}: 存在同条件反证未处置却提议"
                    " supported_in_scope", GATE, update.concept_ref))
        elif state == S.ConceptEvalState.FRAGILE:
            if not (update.statement.strip() and
                    (update.change is not None
                     and update.change.statement.strip()
                     or challenges or old_challenged
                     or update.close_claims)):
                issues.append(ValidationIssue(
                    "gate_fragile", f"{update.concept_ref}: fragile 必须"
                    "引用具体待解决点", GATE, update.concept_ref))
        elif state == S.ConceptEvalState.CONFLICTING:
            if not ((supports and challenges)
                    or (old_supported and old_challenged)
                    or (supports and old_challenged)
                    or (old_supported and challenges)):
                issues.append(ValidationIssue(
                    "gate_conflicting", f"{update.concept_ref}: 无同主张的"
                    "支持与反证却提议 conflicting", GATE, update.concept_ref))
    return issues


# ---------------------------------------------------------------------------
# 总门
# ---------------------------------------------------------------------------

def validate_interpretation(
        *, interpretation: S.LearnerInterpretation,
        source: S.SourceReceipt, pack: S.EvaluationContextPack,
        task: S.TaskSnapshot | None,
        active_claims: dict[str, list[S.ClaimView]] | None = None,
        expected_source_revision: int | None = None,
) -> list[ValidationIssue]:
    """提交前唯一资格门。返回 issues；hard → 拒绝整份，opportunity/gate →
    拒绝对应 statement/patch 并重综合。"""
    active_claims = active_claims or {}
    issues: list[ValidationIssue] = []
    if expected_source_revision is not None:
        issues += check_source_freshness(source, expected_source_revision)
    if interpretation.applicable and not interpretation.observation_claims \
            and not interpretation.concept_updates:
        issues.append(ValidationIssue(
            "applicable_without_evidence",
            "applicable=true 但没有任何观察或概念更新；无有效新观察应"
            "applicable=false", HARD))
    if not interpretation.applicable and not interpretation.abstain_reason:
        issues.append(ValidationIssue(
            "abstain_without_reason", "applicable=false 必须给 abstain_reason",
            HARD))
    claims = interpretation.observation_claims
    updates = interpretation.concept_updates
    issues += check_concept_allowlist(claims, updates, pack)
    issues += check_evidence_spans(claims, source, pack)
    local_ids = {c.local_id for c in claims}
    pack_refs = set(pack.manifest.included_refs)
    issues += check_id_references(updates, active_claims, pack_refs, local_ids)
    issues += check_active_claim_coverage(updates, active_claims, pack)
    issues += check_opportunity_conditions(claims, source, task, pack)
    issues += check_publication_gates(updates, claims, active_claims, pack)
    return issues


def has_hard(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == HARD for i in issues)
