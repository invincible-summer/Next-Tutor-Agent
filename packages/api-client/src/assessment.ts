/**
 * Assessment domain (mirrors `app/api/v1/assessment.py` + the in-session
 * practice loop of `app/api/v1/quiz.py`): CAT sessions (start/answer/
 * next/report), single-question submissions, hint/reveal/practice and
 * answered-records recovery. Errors carry `detail.error.code` envelopes
 * (`question_not_found`, `question_revision_mismatch`,
 * `question_already_answered`, `scope_revision_conflict`,
 * `evaluation_disabled`, …) parsed by the transport into `ApiError.code`.
 *
 * `next()` wires the transport's bounded `evaluation_pending` re-poll —
 * the semantic evaluator may still be queueing when the caller advances.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export interface ConceptRef {
  graph_owner_namespace?: string;
  textbook_id?: string;
  file_ids?: string[];
  concept_id: string;
  concept_revision?: number;
  display_name?: string;
  key?: string;
}

export interface TaskResultFeedback {
  strengths?: string;
  improvement?: string;
  next_step?: string;
}

export interface TaskResult {
  question_ref?: { question_id: string; question_revision: number };
  grading_status?: string;
  verdict?: "correct" | "partial" | "wrong" | null;
  task_score?: number | null;
  criterion_results?: {
    criterion_id: string;
    result: string;
    evidence_refs?: string[];
    comment?: string;
  }[];
  first_error?: { location_ref?: string; description?: string; preceding_correct_refs?: string[] } | null;
  hypotheses?: { statement: string; evidence_refs?: string[]; distinguishing_probe?: string }[];
  feedback?: TaskResultFeedback | null;
  answer_fingerprint?: string;
  computed_at?: string;
  rubric_hash?: string;
  [key: string]: unknown;
}

export interface QuestionIllustrationRef {
  kind: string;
  svg: string;
  alt: string;
  caption: string;
  width: number;
  height: number;
  content_hash: string;
  [key: string]: unknown;
}

export interface QuestionPublic {
  question_id: string;
  question_revision: number;
  q_type: "multiple_choice" | "fill_blank" | "short_answer";
  stem: string;
  options: Record<string, string>;
  input_spec: { kind: "choice" | "text"; max_bytes: number; requires_explanation: boolean };
  concept_refs: ConceptRef[];
  source_badge: string;
  hints_available: boolean;
  illustration: QuestionIllustrationRef | null;
  visual_role?: string;
  illustration_artifact_id?: string;
  [key: string]: unknown;
}

export interface CatReportItem {
  question_id: string;
  question_revision: number;
  question: QuestionPublic | null;
  attempt_id?: string;
  observed_at?: string;
  task_result: TaskResult | null;
  evaluation_status?: string;
  feedback?: string;
}

export interface CatReport {
  assessment_id: string;
  workspace_id?: string;
  status?: string;
  stop_reason?: string;
  stop_code?: string;
  asked?: number;
  graded?: number;
  pending?: number;
  counts?: { correct: number; partial: number; wrong: number };
  difficulty?: number;
  evaluation_mode?: string;
  items: CatReportItem[];
}

export interface SubmissionPayload {
  question_id: string;
  question_revision: number;
  student_answer: string;
  expected_scope_revision?: string;
  assessment_id?: string;
  reply_message_ref?: string;
  workspace_id?: string;
}

export interface SubmissionAck {
  attempt_id: string;
  source_id: string;
  job_id: string;
  question_id: string;
  question_revision: number;
  task_result: TaskResult | null;
  evaluation: { status?: string; interpretation_id?: string; reason?: string };
  links?: { poll: string; events: string };
  duplicate?: boolean;
  learner_feedback?: string;
}

export interface AssessmentStartPayload {
  /** Empty string = free/temporary mode (evaluation forced temporary). */
  workspace_id?: string;
  concept_keys: string[];
  goal?: { purpose?: "adaptive" | "diagnose" | "practice"; target_claims?: string[] };
  illustration_request?: "auto" | "none" | "required";
  illustration_mode?: "v1" | "v2" | "v3";
  generation_hint?: string;
  evaluation_mode?: "closed_loop" | "temporary";
  q_type?: string;
  count?: number;
  probe_ref?: Record<string, unknown>;
  expected_scope_revision?: string;
  grade?: string;
  subject?: string;
}

export interface AssessmentStartResponse {
  status: string;
  assessment_id: string;
  workspace_id?: string;
  illustration_mode?: string;
  evaluation_mode?: string;
  difficulty?: number;
  question: QuestionPublic;
}

export interface AssessmentAnswerResponse {
  status: string;
  assessment_id: string;
  evaluation_mode?: string;
  task_result: TaskResult | null;
  evaluation: { status?: string; interpretation_id?: string };
  stop_reason: string;
  summary?: CatReport;
}

export interface AssessmentNextResponse {
  status: string;
  assessment_id: string;
  illustration_mode?: string;
  evaluation_mode?: string;
  stop_reason: string;
  difficulty?: number;
  question: QuestionPublic | null;
  summary?: CatReport;
}

export interface AssessmentNextOptions {
  expectedRevision?: number;
  signal?: AbortSignalLike | null;
  /** Override the default 20s `evaluation_pending` re-poll window. */
  conflictDeadlineMs?: number;
}

export interface AssessmentClient {
  /** POST /assessment/start — open (or resume) a CAT session. */
  start(payload: AssessmentStartPayload, signal?: AbortSignalLike | null): Promise<AssessmentStartResponse>;
  /** POST /assessment/answer — graded 202; `summary` appears at terminal state. */
  answer(payload: {
    assessment_id: string;
    question_id: string;
    question_revision: number;
    student_answer: string;
  }): Promise<AssessmentAnswerResponse>;
  /**
   * POST /assessment/next — next question or terminal report. Retries
   * bounded while the server answers 409 `evaluation_pending`.
   */
  next(assessmentId: string, options?: AssessmentNextOptions): Promise<AssessmentNextResponse>;
  /** GET /assessment/active?workspace_id= — resumable session or `none`. */
  active(workspaceId?: string, signal?: AbortSignalLike | null): Promise<Record<string, unknown>>;
  report(assessmentId?: string, signal?: AbortSignalLike | null): Promise<{ status: string; summary: CatReport }>;
  abandon(assessmentId: string, expectedRevision?: number): Promise<{ status: string; assessment_id: string; stop_reason: string }>;
  hint(questionId: string, questionRevision: number, hintKind?: string): Promise<{ status: string; hint: string }>;
  reveal(questionId: string, questionRevision: number): Promise<{ status: string; answer: string; explanation: string; already_answered: boolean }>;
  practice(
    questionId: string,
    payload: { question_revision: number; mode?: "same" | "variant"; expected_scope_revision?: string },
  ): Promise<{ status: string; question: QuestionPublic; origin_question_ref: { question_id: string; question_revision: number } }>;
  getQuestion(questionId: string, revision?: number, signal?: AbortSignalLike | null): Promise<{ question: QuestionPublic; revealed?: { answer: string; explanation: string } }>;
  /** POST /assessment/submissions — reliable 202 acceptance (idempotent via key). */
  submit(payload: SubmissionPayload, options?: { idempotencyKey?: string }): Promise<SubmissionAck>;
  getSubmission(attemptId: string, signal?: AbortSignalLike | null): Promise<{
    attempt_id: string;
    source_id: string;
    task_result: TaskResult | null;
    evaluation: { status: string; interpretation_id: string };
    question: QuestionPublic | null;
    feedback: string;
  }>;
  records(options?: {
    offset?: number;
    limit?: number;
    verdict?: string;
    workspaceId?: string;
  }): Promise<{ items: Record<string, unknown>[]; total: number; offset: number; limit: number }>;

  // --- in-session practice loop (/quiz, guest-accessible) -------------------
  /** POST /quiz/record — MC card submission (deterministic grading, 202). */
  quizRecord(payload: {
    question_id: string;
    question_revision: number;
    student_answer: string;
    session_id?: string;
    reply_message_ref?: string;
  }): Promise<Record<string, unknown>>;
  /** GET /quiz/submission — recover the accepted answer snapshot. */
  quizSubmission(
    questionId: string,
    questionRevision: number,
    signal?: AbortSignalLike | null,
  ): Promise<{ submission: Record<string, unknown> | null }>;
  /** GET /quiz/hint — key-step hint derived from the frozen rubric. */
  quizHint(questionId: string, questionRevision: number, signal?: AbortSignalLike | null): Promise<{ status: string; hint: string }>;
  /** GET /quiz/recent — recently asked questions. */
  quizRecent(limit?: number, signal?: AbortSignalLike | null): Promise<Record<string, unknown>[] | Record<string, unknown>>;
}

export function createAssessmentClient(transport: Transport): AssessmentClient {
  const questionBase = (questionId: string) =>
    `/assessment/questions/${encodeURIComponent(questionId)}`;
  return {
    start: (payload, signal) =>
      transport
        .request<AssessmentStartResponse>("/assessment/start", { method: "POST", json: payload, signal })
        .then((result) => result.body),
    answer: (payload) =>
      transport
        .request<AssessmentAnswerResponse>("/assessment/answer", { method: "POST", json: payload })
        .then((result) => result.body),
    next: (assessmentId, options = {}) =>
      transport
        .request<AssessmentNextResponse>("/assessment/next", {
          method: "POST",
          json: { assessment_id: assessmentId, expected_revision: options.expectedRevision ?? 0 },
          waitForConflict: {
            code: "evaluation_pending",
            deadlineMs: options.conflictDeadlineMs,
          },
          signal: options.signal ?? null,
        })
        .then((result) => result.body),
    active: (workspaceId = "", signal) =>
      transport
        .request<Record<string, unknown>>("/assessment/active", {
          query: { workspace_id: workspaceId },
          signal,
        })
        .then((result) => result.body),
    report: (assessmentId = "", signal) =>
      transport
        .request<{ status: string; summary: CatReport }>("/assessment/report", {
          query: { assessment_id: assessmentId },
          signal,
        })
        .then((result) => result.body),
    abandon: (assessmentId, expectedRevision = 0) =>
      transport
        .request<{ status: string; assessment_id: string; stop_reason: string }>("/assessment/abandon", {
          method: "POST",
          json: { assessment_id: assessmentId, expected_revision: expectedRevision },
        })
        .then((result) => result.body),
    hint: (questionId, questionRevision, hintKind) =>
      transport
        .request<{ status: string; hint: string }>(`${questionBase(questionId)}/hint`, {
          method: "POST",
          json: { question_revision: questionRevision, hint_kind: hintKind ?? "key_step" },
        })
        .then((result) => result.body),
    reveal: (questionId, questionRevision) =>
      transport
        .request<{ status: string; answer: string; explanation: string; already_answered: boolean }>(
          `${questionBase(questionId)}/reveal`,
          { method: "POST", json: { question_revision: questionRevision } },
        )
        .then((result) => result.body),
    practice: (questionId, payload) =>
      transport
        .request<{ status: string; question: QuestionPublic; origin_question_ref: { question_id: string; question_revision: number } }>(
          `${questionBase(questionId)}/practice`,
          { method: "POST", json: payload },
        )
        .then((result) => result.body),
    getQuestion: (questionId, revision = 0, signal) =>
      transport
        .request<{ question: QuestionPublic; revealed?: { answer: string; explanation: string } }>(
          questionBase(questionId),
          { query: { revision }, signal },
        )
        .then((result) => result.body),
    submit: (payload, options) =>
      transport
        .request<SubmissionAck>("/assessment/submissions", {
          method: "POST",
          json: payload,
          idempotencyKey: options?.idempotencyKey,
        })
        .then((result) => result.body),
    getSubmission: (attemptId, signal) =>
      transport
        .request<{
          attempt_id: string;
          source_id: string;
          task_result: TaskResult | null;
          evaluation: { status: string; interpretation_id: string };
          question: QuestionPublic | null;
          feedback: string;
        }>(`/assessment/submissions/${encodeURIComponent(attemptId)}`, { signal })
        .then((result) => result.body),
    records: (options = {}) =>
      transport
        .request<{ items: Record<string, unknown>[]; total: number; offset: number; limit: number }>(
          "/assessment/records",
          {
            query: {
              offset: options.offset ?? 0,
              limit: options.limit ?? 20,
              verdict: options.verdict ?? "",
              workspace_id: options.workspaceId ?? "",
            },
          },
        )
        .then((result) => result.body),
    quizRecord: (payload) =>
      transport
        .request<Record<string, unknown>>("/quiz/record", { method: "POST", json: payload })
        .then((result) => result.body),
    quizSubmission: (questionId, questionRevision, signal) =>
      transport
        .request<{ submission: Record<string, unknown> | null }>("/quiz/submission", {
          query: { question_id: questionId, question_revision: questionRevision },
          signal,
        })
        .then((result) => result.body),
    quizHint: (questionId, questionRevision, signal) =>
      transport
        .request<{ status: string; hint: string }>("/quiz/hint", {
          query: { question_id: questionId, question_revision: questionRevision },
          signal,
        })
        .then((result) => result.body),
    quizRecent: (limit, signal) =>
      transport
        .request<Record<string, unknown>[] | Record<string, unknown>>("/quiz/recent", {
          query: { limit },
          signal,
        })
        .then((result) => result.body),
  };
}
