/**
 * Evaluation domains (mirrors `app/api/v1/learner_evaluation.py` +
 * `app/api/v1/evaluation.py`).
 *
 * - `/learner-evaluation/*` is the per-workspace learning archive consumed by
 *   the insights/memory pages: workspace cards, concept projections, source
 *   sessions, the evidence timeline, job status and the review/synthesis
 *   mutations. Lists share the `{items,total,offset,limit[,revision]}`
 *   envelope; mutations answer `detail.error.code` envelopes
 *   (`revision_conflict`, `scope_changed`, `job_not_retryable`, …) that the
 *   transport surfaces as `ConflictError`/`ApiError`.
 * - `/evaluation/*` is the tutor self-evaluation panel (M7): report, traces,
 *   context budget, improvement proposals (the human-in-the-loop approve/
 *   reject/apply gate) and deployed teaching guidance.
 *
 * The Web client's legacy `student_id` query parameter is dropped — the
 * backend resolves identity from the token and ignores it.
 */
import type { AbortSignalLike, QueryValue, Transport } from "./types.ts";

// --- learner-evaluation DTOs -------------------------------------------------
// Timestamps are server ISO strings; categories are not a ladder — never map
// them onto numeric scores.

export interface EvalConceptRef {
  graph_owner_namespace?: string;
  textbook_id?: string;
  file_ids?: string[];
  concept_id: string;
  concept_revision?: string;
  display_name?: string;
  /** Stable server encoding (ConceptRef.key, truncated sha256). */
  key?: string;
  [key: string]: unknown;
}

export interface EvalClaim {
  claim_id: string;
  concept_ref?: EvalConceptRef;
  statement?: string;
  /** supported | tentative | challenged | unobserved */
  status?: string;
  support_refs?: string[];
  challenge_refs?: string[];
  assistance_scope?: string;
  limits?: string[];
  [key: string]: unknown;
}

export interface EvalLearningChange {
  /** strengthened | weakened | mixed | stable | unknown */
  direction?: string;
  /** comparable | partially_comparable | not_comparable | no_prior */
  comparison?: string;
  prior_refs?: string[];
  current_refs?: string[];
  statement?: string;
  alternative_explanations?: string[];
  [key: string]: unknown;
}

export interface EvalNextProbe {
  /** explain | practice | variant | transfer | delayed_recheck | self_check */
  kind?: string;
  concept_ref?: string;
  target_claim?: string;
  instruction?: string;
  rationale?: string;
  expected_observation?: string;
  /** full_demo | key_hints | independent */
  assistance?: string;
  stop_condition?: string;
  [key: string]: unknown;
}

export interface ConceptEvaluationView {
  concept_ref: EvalConceptRef;
  /** not_observed | emerging | supported_in_scope | fragile | conflicting | null */
  state: string | null;
  /** ready | pending | reconciling | unavailable | disabled */
  evaluation_status?: string;
  judgment_id?: string;
  statement?: string;
  claims?: EvalClaim[];
  change?: EvalLearningChange | null;
  next_probe?: EvalNextProbe | null;
  /** current | out_of_scope | source_removed | needs_mapping */
  scope_status?: string;
  evidence_count?: number;
  last_observed_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface EvalCoverageCounts {
  observed_concepts?: number;
  not_observed_concepts?: number;
  by_state?: Record<string, number>;
  reconciling_concepts?: number;
  [key: string]: unknown;
}

export interface EvalScopeSynthesis {
  synthesis_id?: string;
  /** concept | session | workspace */
  scope_type?: string;
  workspace_id?: string;
  statement?: string;
  claim_refs?: string[];
  theme_summaries?: Record<string, unknown>[];
  changes?: Record<string, unknown>[];
  open_questions?: Record<string, unknown>[];
  priority_probe?: EvalNextProbe | null;
  limits?: string[];
  scope_revision?: string;
  evidence_watermark?: string;
  pending_source_count?: number;
  generated_at?: string;
  [key: string]: unknown;
}

export interface WorkspaceEvaluationSummary {
  workspace_id: string;
  scope_revision?: string;
  evaluation_status?: string;
  evaluated_through?: string;
  pending_source_count?: number;
  failed_source_count?: number;
  disabled_source_count?: number;
  allowed_concept_count?: number;
  coverage?: EvalCoverageCounts;
  synthesis?: EvalScopeSynthesis | null;
  workspace_name?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface WorkspaceEvaluationListItem {
  workspace_id: string;
  workspace_name?: string;
  evaluation_status?: string;
  scope_revision?: string;
  coverage?: EvalCoverageCounts;
  updated_at?: string;
  [key: string]: unknown;
}

export interface EvalWorkspacesResponse {
  items: WorkspaceEvaluationListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface EvalConceptsResponse {
  items: ConceptEvaluationView[];
  total: number;
  offset: number;
  limit: number;
  /** Scope revision at read time (optimistic-concurrency token). */
  revision: string;
}

export interface EvalSessionItem {
  source_session_ref: string;
  has_evidence: boolean;
  /** available | archived | deleted */
  availability?: string;
  last_observed_at?: string;
  kinds?: string[];
  [key: string]: unknown;
}

export interface EvalSessionsResponse {
  items: EvalSessionItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface EvalSessionEvidenceItem {
  source_id: string;
  /** dialogue | assessment */
  kind?: string;
  observed_at?: string;
  availability?: string;
  canonical_text?: string;
  interpretation_id?: string;
  review_status?: string;
  [key: string]: unknown;
}

export interface EvalSourceTimelineItem {
  source_id: string;
  kind?: string;
  observed_at?: string;
  scope_status?: string;
  /** available | archived | deleted */
  availability?: string;
  concept_refs?: string[];
  summary?: string;
  source_session_ref?: string;
  interpretation_id?: string;
  review_status?: string;
  [key: string]: unknown;
}

export interface EvalTimelineResponse {
  items: EvalSourceTimelineItem[];
  total: number;
  offset: number;
  limit: number;
}

/** Single evidence detail: raw performance, public/revealed task views,
 *  interpretation, review history. Grown server-side; treated as open-ended. */
export interface EvalEvidenceDetail {
  source_id: string;
  kind?: string;
  observed_at?: string;
  /** Current source version (review `expected_revision` / delete If-Match). */
  source_revision?: number;
  /** Current interpretation id ("" = not yet evaluated). */
  interpretation_id?: string;
  workspace_id?: string;
  availability?: string;
  canonical_text?: string;
  assistance?: Record<string, unknown>[];
  task?: Record<string, unknown> | null;
  revealed?: { answer: string; explanation: string } | null;
  interpretation?: Record<string, unknown> | null;
  task_result?: Record<string, unknown> | null;
  reviews?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface EvalJobDetail {
  job_id: string;
  /** queued | running | retry_wait | completed | abstained | failed | cancelled */
  state: string;
  kind?: string;
  workspace_id?: string;
  error_code?: string;
  attempt_count?: number;
  transport_attempts?: number;
  retryable?: boolean;
}

export interface EvalReviewPayload {
  interpretation_id: string;
  reason: string;
  issue_kind?: string;
  expected_revision: number;
}

// --- M7 self-evaluation DTOs ---------------------------------------------------

export interface EvalReport {
  ts?: number;
  total_turns?: number;
  total_evaluated?: number;
  failure_distribution?: Record<string, number>;
  top_strategies?: {
    strategy: string;
    subject: string;
    avg_success_rate: number;
    sample_size: number;
  }[];
  pending_proposals?: number;
  tokens_per_turn?: number | null;
  [key: string]: unknown;
}

export type EvalProposalStatus = "proposed" | "approved" | "applied" | "rejected";

export interface EvalProposal {
  id: string;
  ts?: number;
  /** Open-ended guidance format (current); legacy proposals fall back to
   *  target/change. */
  title?: string;
  applicability?: string;
  guidance?: string;
  cautions?: string[];
  applied_ts?: number;
  /** Turns taught since application; null = unknowable for legacy data. */
  impact_turns?: number | null;
  target?: string;
  change?: string;
  rationale?: string;
  confidence?: number;
  evidence?: string[];
  status?: EvalProposalStatus;
  [key: string]: unknown;
}

/** Deployed teaching guidance (active + revoked audit trail). */
export interface EvalGuidanceEntry {
  id: string;
  source_proposal?: string;
  title?: string;
  applicability?: string;
  guidance?: string;
  cautions?: string[];
  confidence?: number;
  applied_at?: number;
  active?: boolean;
  revoked_at?: number;
  impact_turns?: number | null;
  [key: string]: unknown;
}

export interface EvalTrace {
  id: string;
  ts?: number;
  session_id?: string;
  student_id?: string;
  concept?: string;
  subject?: string;
  intent?: string;
  grade?: string;
  mode?: string;
  outcome?: string;
  tool_count?: number;
  steps?: number;
  tokens_used?: number;
  duration_sec?: number;
  failure_type?: string;
  failure_cause?: string;
  recommendation?: string;
  [key: string]: unknown;
}

/** Context/compaction/reasoning telemetry; profile/usage/pressure buckets are
 *  loose and grow with the runtime, so the method is generic. */
export interface ContextBudgetReport {
  status?: string;
  trace_count?: number;
  llm_calls?: number;
  profile?: Record<string, unknown>;
  usage?: Record<string, unknown>;
  pressure?: Record<string, number>;
  reasoning_modes?: Record<string, number>;
  compaction?: Record<string, unknown>;
  tool_projection?: Record<string, unknown>;
  recovery?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface EvaluationClient {
  /** GET /learner-evaluation/workspaces — own workspace cards. */
  workspaces(
    options?: { offset?: number; limit?: number },
    signal?: AbortSignalLike | null,
  ): Promise<EvalWorkspacesResponse>;
  /** GET /learner-evaluation/workspaces/{wid} — archive overview
   *  (narrative/coverage/pending buckets). */
  workspace(workspaceId: string, signal?: AbortSignalLike | null): Promise<WorkspaceEvaluationSummary>;
  /** GET /learner-evaluation/workspaces/{wid}/concepts — scoped concept
   *  projection (unobserved nodes left-joined by scope). */
  concepts(
    workspaceId: string,
    query?: { state?: string; textbookId?: string; q?: string; offset?: number; limit?: number },
    signal?: AbortSignalLike | null,
  ): Promise<EvalConceptsResponse>;
  /** GET /learner-evaluation/workspaces/{wid}/concepts/{key} — single concept
   *  claims/change/next_probe (`key` = ConceptRef.key). */
  concept(
    workspaceId: string,
    conceptKey: string,
    signal?: AbortSignalLike | null,
  ): Promise<ConceptEvaluationView>;
  /** GET /learner-evaluation/workspaces/{wid}/sessions — source sessions of
   *  this workspace (rows exist even without evidence). */
  sessions(
    workspaceId: string,
    options?: { offset?: number; limit?: number },
    signal?: AbortSignalLike | null,
  ): Promise<EvalSessionsResponse>;
  /** GET /learner-evaluation/workspaces/{wid}/sessions/{ref} — observations
   *  attributed to one session. */
  sessionEvidence(
    workspaceId: string,
    sourceSessionRef: string,
    signal?: AbortSignalLike | null,
  ): Promise<{ items: EvalSessionEvidenceItem[]; total: number }>;
  /** GET /learner-evaluation/workspaces/{wid}/evidence — locatable evidence
   *  timeline (filter by concept key / source kind / session). */
  evidence(
    workspaceId: string,
    query?: {
      conceptKey?: string;
      sourceKind?: string;
      sourceSessionRef?: string;
      offset?: number;
      limit?: number;
    },
    signal?: AbortSignalLike | null,
  ): Promise<EvalTimelineResponse>;
  /** GET /learner-evaluation/evidence/{sourceId} — full evidence detail. */
  evidenceDetail<T = EvalEvidenceDetail>(
    sourceId: string,
    signal?: AbortSignalLike | null,
  ): Promise<T>;
  /** GET /learner-evaluation/jobs/{jobId} — job status (no raw model output). */
  job(jobId: string, signal?: AbortSignalLike | null): Promise<EvalJobDetail>;
  /** POST /learner-evaluation/evidence/{sourceId}/reviews — "evaluation
   *  inaccurate" review intake; one active review per source. */
  createReview(
    sourceId: string,
    payload: EvalReviewPayload,
  ): Promise<{ review_id: string; job_id: string; duplicate?: boolean }>;
  /** POST /learner-evaluation/jobs/{jobId}/retry — manually retry a failed
   *  job (opens a new job with parent_job_id). */
  retryJob(
    jobId: string,
    expectedRevision?: number,
  ): Promise<{ job_id: string; parent_job_id: string }>;
  /** POST /learner-evaluation/workspaces/{wid}/synthesis — trigger workspace
   *  synthesis (re-organizes valid evidence only). */
  requestSynthesis(
    workspaceId: string,
    expectedScopeRevision?: string,
  ): Promise<{ job_id: string; duplicate?: boolean }>;
  /** DELETE /learner-evaluation/evidence/{sourceId} — physically remove an
   *  evidence source and invalidate derived state. Pass `expectedRevision`
   *  to send the backend's If-Match guard. */
  deleteEvidence(
    sourceId: string,
    options?: { expectedRevision?: number },
  ): Promise<{ status: string; deleted: string; affected_concepts?: string[]; source_session_ref?: string }>;

  // --- M7 self-evaluation (/evaluation/*) ------------------------------------
  /** GET /evaluation/report — system-level effectiveness snapshot. */
  report(signal?: AbortSignalLike | null): Promise<EvalReport>;
  /** GET /evaluation/traces — recent turn evaluation traces (default 50). */
  traces(limit?: number, signal?: AbortSignalLike | null): Promise<EvalTrace[]>;
  /** GET /evaluation/proposals — all improvement proposals. */
  proposals(signal?: AbortSignalLike | null): Promise<EvalProposal[]>;
  /** GET /evaluation/context-budget — non-sensitive context/compaction
   *  telemetry (default 200 traces sampled). */
  contextBudget<T = ContextBudgetReport>(
    limit?: number,
    signal?: AbortSignalLike | null,
  ): Promise<T>;
  /** GET /evaluation/guidance — applied/revoked teaching guidance entries. */
  guidance(signal?: AbortSignalLike | null): Promise<EvalGuidanceEntry[]>;
  /** DELETE /evaluation/guidance/{entryId} — revoke (instant rollback, entry
   *  kept for audit). */
  revokeGuidance(entryId: string): Promise<{ entry_id: string; active: boolean }>;
  /** PATCH /evaluation/proposals/{id} — human-in-the-loop status transition;
   *  "applied" also deploys the guidance text into the teaching engine. */
  patchProposal(
    proposalId: string,
    status: Exclude<EvalProposalStatus, "proposed">,
  ): Promise<{ proposal_id: string; status: string }>;
}

export function createEvaluationClient(transport: Transport): EvaluationClient {
  const base = (workspaceId: string) =>
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}`;
  return {
    workspaces: (options = {}, signal) =>
      transport
        .request<EvalWorkspacesResponse>("/learner-evaluation/workspaces", {
          query: { offset: options.offset, limit: options.limit },
          signal,
        })
        .then((result) => result.body),
    workspace: (workspaceId, signal) =>
      transport
        .request<WorkspaceEvaluationSummary>(base(workspaceId), { signal })
        .then((result) => result.body),
    concepts: (workspaceId, query = {}, signal) => {
      const wire: Record<string, QueryValue> = {
        state: query.state,
        textbook_id: query.textbookId,
        q: query.q,
        offset: query.offset,
        limit: query.limit,
      };
      return transport
        .request<EvalConceptsResponse>(`${base(workspaceId)}/concepts`, { query: wire, signal })
        .then((result) => result.body);
    },
    concept: (workspaceId, conceptKey, signal) =>
      transport
        .request<ConceptEvaluationView>(
          `${base(workspaceId)}/concepts/${encodeURIComponent(conceptKey)}`,
          { signal },
        )
        .then((result) => result.body),
    sessions: (workspaceId, options = {}, signal) =>
      transport
        .request<EvalSessionsResponse>(`${base(workspaceId)}/sessions`, {
          query: { offset: options.offset, limit: options.limit },
          signal,
        })
        .then((result) => result.body),
    sessionEvidence: (workspaceId, sourceSessionRef, signal) =>
      transport
        .request<{ items: EvalSessionEvidenceItem[]; total: number }>(
          `${base(workspaceId)}/sessions/${encodeURIComponent(sourceSessionRef)}`,
          { signal },
        )
        .then((result) => result.body),
    evidence: (workspaceId, query = {}, signal) => {
      const wire: Record<string, QueryValue> = {
        concept_key: query.conceptKey,
        source_kind: query.sourceKind,
        source_session_ref: query.sourceSessionRef,
        offset: query.offset,
        limit: query.limit,
      };
      return transport
        .request<EvalTimelineResponse>(`${base(workspaceId)}/evidence`, { query: wire, signal })
        .then((result) => result.body);
    },
    evidenceDetail: <T>(sourceId: string, signal?: AbortSignalLike | null) =>
      transport
        .request<T>(`/learner-evaluation/evidence/${encodeURIComponent(sourceId)}`, { signal })
        .then((result) => result.body),
    job: (jobId, signal) =>
      transport
        .request<EvalJobDetail>(`/learner-evaluation/jobs/${encodeURIComponent(jobId)}`, { signal })
        .then((result) => result.body),
    createReview: (sourceId, payload) =>
      transport
        .request<{ review_id: string; job_id: string; duplicate?: boolean }>(
          `/learner-evaluation/evidence/${encodeURIComponent(sourceId)}/reviews`,
          { method: "POST", json: payload },
        )
        .then((result) => result.body),
    retryJob: (jobId, expectedRevision = 0) =>
      transport
        .request<{ job_id: string; parent_job_id: string }>(
          `/learner-evaluation/jobs/${encodeURIComponent(jobId)}/retry`,
          { method: "POST", json: { expected_revision: expectedRevision } },
        )
        .then((result) => result.body),
    requestSynthesis: (workspaceId, expectedScopeRevision = "") =>
      transport
        .request<{ job_id: string; duplicate?: boolean }>(
          `${base(workspaceId)}/synthesis`,
          { method: "POST", json: { expected_scope_revision: expectedScopeRevision } },
        )
        .then((result) => result.body),
    deleteEvidence: (sourceId, options = {}) =>
      transport
        .request<{ status: string; deleted: string; affected_concepts?: string[]; source_session_ref?: string }>(
          `/learner-evaluation/evidence/${encodeURIComponent(sourceId)}`,
          {
            method: "DELETE",
            headers: options.expectedRevision !== undefined
              ? { "If-Match": String(options.expectedRevision) }
              : undefined,
          },
        )
        .then((result) => result.body),
    report: (signal) =>
      transport
        .request<EvalReport>("/evaluation/report", { signal })
        .then((result) => result.body),
    traces: (limit, signal) =>
      transport
        .request<EvalTrace[]>("/evaluation/traces", { query: { limit }, signal })
        .then((result) => result.body),
    proposals: (signal) =>
      transport
        .request<EvalProposal[]>("/evaluation/proposals", { signal })
        .then((result) => result.body),
    contextBudget: <T>(limit?: number, signal?: AbortSignalLike | null) =>
      transport
        .request<T>("/evaluation/context-budget", { query: { limit }, signal })
        .then((result) => result.body),
    guidance: (signal) =>
      transport
        .request<EvalGuidanceEntry[]>("/evaluation/guidance", { signal })
        .then((result) => result.body),
    revokeGuidance: (entryId) =>
      transport
        .request<{ entry_id: string; active: boolean }>(
          `/evaluation/guidance/${encodeURIComponent(entryId)}`,
          { method: "DELETE" },
        )
        .then((result) => result.body),
    patchProposal: (proposalId, status) =>
      transport
        .request<{ proposal_id: string; status: string }>(
          `/evaluation/proposals/${encodeURIComponent(proposalId)}`,
          { method: "PATCH", json: { status } },
        )
        .then((result) => result.body),
  };
}
